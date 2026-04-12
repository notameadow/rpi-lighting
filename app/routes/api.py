import os
import logging
from flask import Blueprint, jsonify, request, current_app
from app.auth import require_auth
from app.config import LUMINAIR_DIR
from app.database import (get_setting, set_setting, update_scene,
                          delete_scene as db_del_scene, add_scene as db_add_scene,
                          store_scenes, reorder_scenes,
                          add_upload, get_uploads, set_active_upload, delete_upload as db_delete_upload,
                          add_fixture as db_add_fixture, update_fixture as db_update_fixture,
                          delete_fixture as db_delete_fixture)

logger = logging.getLogger('lighting.api')

api_bp = Blueprint('api', __name__)


def _engine():
    return current_app.dmx_engine


def _controller():
    return current_app.controller


# ------------------------------------------------------------------
# State (polled by UI)
# ------------------------------------------------------------------

@api_bp.route('/api/state')
@require_auth
def state():
    engine_state = _engine().get_state()
    engine_state['levels'] = _engine().get_fixture_levels()
    return jsonify(engine_state)


# ------------------------------------------------------------------
# Scenes
# ------------------------------------------------------------------

@api_bp.route('/api/scenes')
@require_auth
def scenes():
    ctrl = _controller()
    result = []
    for s in ctrl.scenes:
        # fixtures: all fixture IDs in the engine mask (includes intentional zeros)
        controlled = set()
        # fixtures_nonzero: only fixtures with at least one non-zero channel
        nonzero = set()
        for f in ctrl.fixtures:
            base = f.dmx_address - 1
            in_mask = False
            has_value = False
            for offset in range(f.channel_count):
                idx = base + offset
                if idx in s.channel_mask:
                    in_mask = True
                    if s.dmx_values[idx] > 0:
                        has_value = True
            if in_mask:
                controlled.add(f.id)
            if has_value:
                nonzero.add(f.id)
        result.append({
            'id': s.id,
            'name': s.name,
            'fade_in': s.fade_in,
            'fade_out': s.fade_out,
            'button_color': s.button_color,
            'locked': s.locked,
            'fixtures': sorted(controlled),
            'fixtures_nonzero': sorted(nonzero),
        })
    return jsonify(result)


@api_bp.route('/api/scene/<int:scene_id>/recall', methods=['POST'])
@require_auth
def recall_scene(scene_id):
    data = request.get_json(silent=True) or {}
    fade = data.get('fade')
    if fade is not None:
        try:
            fade = float(fade)
        except (TypeError, ValueError):
            fade = None
    master_fade = float(get_setting('master_fade_time', '2.0'))
    ok = _engine().recall_scene(scene_id, fade_time=fade, master_fade_time=master_fade)
    return jsonify({'ok': ok})


@api_bp.route('/api/scene/<int:scene_id>/fixtures', methods=['POST'])
@require_auth
def set_scene_fixtures(scene_id):
    """Set which fixtures a scene controls by rebuilding its channel mask."""
    data = request.get_json(silent=True) or {}
    fixture_ids = data.get('fixtures', [])
    if not isinstance(fixture_ids, list):
        return jsonify({'ok': False, 'error': 'fixtures must be a list'}), 400

    ctrl = _controller()
    scene = None
    for s in ctrl.scenes:
        if s.id == scene_id:
            scene = s
            break
    if scene is None:
        return jsonify({'ok': False, 'error': 'scene not found'}), 404

    # Rebuild channel mask from selected fixtures
    new_mask = set()
    fid_set = set(fixture_ids)
    for f in ctrl.fixtures:
        if f.id in fid_set:
            base = f.dmx_address - 1
            for offset in range(f.channel_count):
                new_mask.add(base + offset)
    scene.channel_mask = new_mask
    update_scene(scene_id, channel_mask=new_mask)
    logger.info('Scene %d "%s" fixtures updated: %d fixtures, %d channels',
                scene_id, scene.name, len(fid_set), len(new_mask))
    return jsonify({'ok': True, 'channels': len(new_mask)})


@api_bp.route('/api/scene/<int:scene_id>/copy', methods=['POST'])
@require_auth
def copy_scene(scene_id):
    """Duplicate a scene with a new name."""
    data = request.get_json(silent=True) or {}
    ctrl = _controller()
    src = None
    for s in ctrl.scenes:
        if s.id == scene_id:
            src = s
            break
    if src is None:
        return jsonify({'ok': False, 'error': 'scene not found'}), 404

    new_name = data.get('name', src.name + ' copy')

    from app.luminair.models import Scene
    new_scene = Scene(
        id=0, name=new_name,
        dmx_values=bytes(src.dmx_values), channel_mask=set(src.channel_mask),
        fade_in=src.fade_in, fade_out=src.fade_out,
        button_color=src.button_color, locked=False, master_level=src.master_level,
    )
    new_id = db_add_scene(new_scene)
    new_scene.id = new_id
    ctrl.scenes.append(new_scene)
    _engine().set_scenes(ctrl.scenes)

    logger.info('Copied scene %d "%s" → %d "%s"', scene_id, src.name, new_id, new_name)
    return jsonify({'ok': True, 'id': new_id, 'name': new_name})


@api_bp.route('/api/scene/create', methods=['POST'])
@require_auth
def create_scene():
    """Create a new scene from the current DMX state."""
    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'name required'}), 400
    button_color = data.get('button_color', 'rgba(100,100,100,1)')
    fade_in = float(data.get('fade_in', 2.0))

    dmx_values, channel_mask = _engine().snapshot_current()

    from app.luminair.models import Scene
    new_scene = Scene(
        id=0, name=name,
        dmx_values=dmx_values, channel_mask=channel_mask,
        fade_in=fade_in, fade_out=0.2,
        button_color=button_color, locked=False, master_level=1.0,
    )
    new_id = db_add_scene(new_scene)
    new_scene.id = new_id
    ctrl = _controller()
    ctrl.scenes.append(new_scene)
    _engine().set_scenes(ctrl.scenes)

    logger.info('Created scene %d "%s" from current state (%d channels)', new_id, name, len(channel_mask))
    return jsonify({'ok': True, 'id': new_id, 'name': name})


@api_bp.route('/api/scene/<int:scene_id>', methods=['DELETE'])
@require_auth
def delete_scene(scene_id):
    """Delete a scene."""
    ctrl = _controller()
    found = None
    for s in ctrl.scenes:
        if s.id == scene_id:
            found = s
            break
    if found is None:
        return jsonify({'ok': False, 'error': 'scene not found'}), 404

    ctrl.scenes.remove(found)
    _engine().set_scenes(ctrl.scenes)
    db_del_scene(scene_id)

    logger.info('Deleted scene %d "%s"', scene_id, found.name)
    return jsonify({'ok': True})


@api_bp.route('/api/scenes/reorder', methods=['POST'])
@require_auth
def reorder_scenes_api():
    """Persist scene display order."""
    data = request.get_json(silent=True) or {}
    order = data.get('order')
    if not order or not isinstance(order, list):
        return jsonify({'ok': False, 'error': 'order required'}), 400
    reorder_scenes(order)
    # Also reorder in-memory scene list
    ctrl = _controller()
    id_to_scene = {s.id: s for s in ctrl.scenes}
    ctrl.scenes = [id_to_scene[sid] for sid in order if sid in id_to_scene]
    # Append any scenes not in the order list (shouldn't happen, but safe)
    for s in id_to_scene.values():
        if s not in ctrl.scenes:
            ctrl.scenes.append(s)
    _engine().set_scenes(ctrl.scenes)
    logger.info('Scene order updated: %s', order)
    return jsonify({'ok': True})


@api_bp.route('/api/scene/<int:scene_id>/fade', methods=['POST'])
@require_auth
def set_scene_fade(scene_id):
    """Set custom fade time for a scene."""
    data = request.get_json(silent=True) or {}
    try:
        fade_in = float(data['fade_in'])
    except (KeyError, TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'need fade_in (seconds)'}), 400
    ctrl = _controller()
    for s in ctrl.scenes:
        if s.id == scene_id:
            s.fade_in = max(0.0, fade_in)
            update_scene(scene_id, fade_in=s.fade_in)
            return jsonify({'ok': True, 'fade_in': s.fade_in})
    return jsonify({'ok': False, 'error': 'scene not found'}), 404


@api_bp.route('/api/scene/<int:scene_id>/color', methods=['POST'])
@require_auth
def set_scene_color(scene_id):
    """Update a scene's button colour."""
    data = request.get_json(silent=True) or {}
    color = data.get('button_color', '').strip()
    if not color:
        return jsonify({'ok': False, 'error': 'button_color required'}), 400
    ctrl = _controller()
    for s in ctrl.scenes:
        if s.id == scene_id:
            s.button_color = color
            update_scene(scene_id, button_color=color)
            return jsonify({'ok': True})
    return jsonify({'ok': False, 'error': 'scene not found'}), 404


@api_bp.route('/api/scene/<int:scene_id>/name', methods=['POST'])
@require_auth
def rename_scene(scene_id):
    """Rename a scene."""
    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'name required'}), 400
    ctrl = _controller()
    for s in ctrl.scenes:
        if s.id == scene_id:
            s.name = name
            update_scene(scene_id, name=name)
            logger.info('Renamed scene %d to "%s"', scene_id, name)
            return jsonify({'ok': True, 'name': name})
    return jsonify({'ok': False, 'error': 'scene not found'}), 404


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@api_bp.route('/api/fixtures')
@require_auth
def fixtures():
    ctrl = _controller()
    result = []
    for f in ctrl.fixtures:
        result.append({
            'id': f.id,
            'name': f.name,
            'model': f.model,
            'manufacturer': f.manufacturer,
            'dmx_address': f.dmx_address,
            'channel_count': f.channel_count,
            'channels': f.profile.channels,
            'group': f.group,
        })
    return jsonify(result)


@api_bp.route('/api/fixture', methods=['POST'])
@require_auth
def create_fixture():
    """Create a new fixture."""
    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'name required'}), 400
    model = data.get('model', '').strip()
    manufacturer = data.get('manufacturer', '').strip()
    dmx_address = int(data.get('dmx_address', 1))
    channels = data.get('channels', ['Intensity'])
    group = data.get('group', 'custom')

    new_id = db_add_fixture(name, model, manufacturer, dmx_address, channels, group)

    # Reload into controller
    from app.luminair.models import Fixture, ChannelProfile
    fix = Fixture(id=new_id, name=name, model=model, manufacturer=manufacturer,
                  dmx_address=dmx_address, channel_count=len(channels),
                  profile=ChannelProfile(channels=channels), group=group)
    ctrl = _controller()
    ctrl.fixtures.append(fix)
    _engine().set_fixtures(ctrl.fixtures)

    logger.info('Created fixture %d "%s" at DMX %d', new_id, name, dmx_address)
    return jsonify({'ok': True, 'id': new_id})


@api_bp.route('/api/fixture/<int:fixture_id>', methods=['PUT'])
@require_auth
def edit_fixture(fixture_id):
    """Update a fixture's properties."""
    data = request.get_json(silent=True) or {}
    ctrl = _controller()
    fix = None
    for f in ctrl.fixtures:
        if f.id == fixture_id:
            fix = f
            break
    if fix is None:
        return jsonify({'ok': False, 'error': 'fixture not found'}), 404

    updates = {}
    if 'name' in data:
        fix.name = data['name'].strip()
        updates['name'] = fix.name
    if 'model' in data:
        fix.model = data['model'].strip()
        updates['model'] = fix.model
    if 'manufacturer' in data:
        fix.manufacturer = data['manufacturer'].strip()
        updates['manufacturer'] = fix.manufacturer
    if 'dmx_address' in data:
        fix.dmx_address = int(data['dmx_address'])
        updates['dmx_address'] = fix.dmx_address
    if 'channels' in data:
        from app.luminair.models import ChannelProfile
        fix.profile = ChannelProfile(channels=data['channels'])
        fix.channel_count = len(data['channels'])
        updates['channels'] = data['channels']
        updates['channel_count'] = fix.channel_count
    if 'group' in data:
        fix.group = data['group']
        updates['group'] = fix.group

    if updates:
        db_update_fixture(fixture_id, **updates)
        _engine().set_fixtures(ctrl.fixtures)

    logger.info('Updated fixture %d: %s', fixture_id, list(updates.keys()))
    return jsonify({'ok': True})


@api_bp.route('/api/fixture/<int:fixture_id>', methods=['DELETE'])
@require_auth
def remove_fixture(fixture_id):
    """Delete a fixture."""
    ctrl = _controller()
    fix = None
    for f in ctrl.fixtures:
        if f.id == fixture_id:
            fix = f
            break
    if fix is None:
        return jsonify({'ok': False, 'error': 'fixture not found'}), 404

    ctrl.fixtures.remove(fix)
    _engine().set_fixtures(ctrl.fixtures)
    db_delete_fixture(fixture_id)

    logger.info('Deleted fixture %d "%s"', fixture_id, fix.name)
    return jsonify({'ok': True})


@api_bp.route('/api/fixture/<int:fixture_id>/channel', methods=['POST'])
@require_auth
def set_channel(fixture_id):
    data = request.get_json(silent=True) or {}
    try:
        offset = int(data['offset'])
        value = max(0, min(255, int(data['value'])))
    except (KeyError, TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'need offset and value'}), 400
    ok = _engine().set_fixture_channels(fixture_id, {offset: value})
    return jsonify({'ok': ok})


@api_bp.route('/api/fixture/<int:fixture_id>/color', methods=['POST'])
@require_auth
def set_color(fixture_id):
    """Set RGB/RGBW + intensity, mapped to correct offsets per fixture type."""
    data = request.get_json(silent=True) or {}
    ctrl = _controller()
    fix = None
    for f in ctrl.fixtures:
        if f.id == fixture_id:
            fix = f
            break
    if fix is None:
        return jsonify({'ok': False, 'error': 'fixture not found'}), 404

    values = {}
    channels = fix.profile.channels

    def _map(name, json_key):
        if json_key in data:
            try:
                idx = channels.index(name)
                values[idx] = max(0, min(255, int(data[json_key])))
            except ValueError:
                pass

    _map('Red', 'r')
    _map('Green', 'g')
    _map('Blue', 'b')
    _map('White', 'w')
    _map('Intensity', 'intensity')
    _map('Dim', 'intensity')
    _map('Level', 'intensity')

    if not values:
        return jsonify({'ok': False, 'error': 'no valid channels'}), 400

    ok = _engine().set_fixture_channels(fixture_id, values)
    return jsonify({'ok': ok})


@api_bp.route('/api/fixture/<int:fixture_id>/clear', methods=['POST'])
@require_auth
def clear_override(fixture_id):
    ok = _engine().clear_fixture_channels(fixture_id)
    return jsonify({'ok': ok})


# ------------------------------------------------------------------
# Master / Blackout
# ------------------------------------------------------------------

@api_bp.route('/api/master', methods=['POST'])
@require_auth
def set_master():
    data = request.get_json(silent=True) or {}
    try:
        value = max(0, min(255, int(data['value'])))
    except (KeyError, TypeError, ValueError):
        return jsonify({'ok': False}), 400
    _engine().set_master(value)
    return jsonify({'ok': True, 'master': value})


@api_bp.route('/api/master/fade', methods=['POST'])
@require_auth
def fade_master():
    data = request.get_json(silent=True) or {}
    try:
        target = int(data['target'])
        duration = float(data.get('duration', 2.0))
    except (KeyError, TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'need target'}), 400
    _engine().fade_master(target, duration)
    return jsonify({'ok': True})


@api_bp.route('/api/haze', methods=['POST'])
@require_auth
def haze():
    data = request.get_json(silent=True) or {}
    if 'level' in data:
        _engine().set_haze_level(int(data['level']))
    elif 'on' in data:
        _engine().set_haze(bool(data['on']))
    else:
        _engine().set_haze(True)
    return jsonify({'ok': True})


@api_bp.route('/api/strobe', methods=['POST'])
@require_auth
def strobe():
    data = request.get_json(silent=True) or {}
    if 'on' in data:
        _engine().set_strobe(bool(data['on']))
    if 'speed' in data:
        _engine().set_strobe_speed(int(data['speed']))
    return jsonify({'ok': True})


@api_bp.route('/api/settings', methods=['GET'])
@require_auth
def get_settings():
    default_scene = get_setting('default_scene')
    master_fade_time = get_setting('master_fade_time', '2.0')
    return jsonify({
        'default_scene': int(default_scene) if default_scene is not None else None,
        'master_fade_time': float(master_fade_time),
    })


@api_bp.route('/api/settings', methods=['POST'])
@require_auth
def save_settings():
    data = request.get_json(silent=True) or {}
    if 'default_scene' in data:
        val = data['default_scene']
        if val is None:
            set_setting('default_scene', '')
        else:
            set_setting('default_scene', str(int(val)))
    if 'master_fade_time' in data:
        set_setting('master_fade_time', str(float(data['master_fade_time'])))
    return jsonify({'ok': True})


@api_bp.route('/api/logo', methods=['POST'])
@require_auth
def upload_logo():
    if 'file' not in request.files:
        return jsonify({'ok': False}), 400
    f = request.files['file']
    path = os.path.join(os.path.dirname(LUMINAIR_DIR), 'logo.png')
    f.save(path)
    return jsonify({'ok': True})


@api_bp.route('/api/blackout', methods=['POST'])
@require_auth
def blackout():
    data = request.get_json(silent=True) or {}
    on = data.get('on', True)
    _engine().set_blackout(bool(on))
    return jsonify({'ok': True, 'blackout': bool(on)})


# ------------------------------------------------------------------
# Luminair upload
# ------------------------------------------------------------------

@api_bp.route('/api/luminair/upload', methods=['POST'])
@require_auth
def upload_luminair():
    import time as _time
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'no file field in form'}), 400
    f = request.files['file']
    if not f.filename:
        return jsonify({'ok': False, 'error': 'empty filename'}), 400
    if not f.filename.lower().endswith('.luminair'):
        return jsonify({'ok': False, 'error': 'must be a .luminair file, got: ' + f.filename}), 400

    os.makedirs(LUMINAIR_DIR, exist_ok=True)

    # Save with timestamp prefix for history
    ts = _time.strftime('%Y%m%d_%H%M%S')
    safe_name = f.filename.replace(' ', '_')
    stored_name = f'{ts}_{safe_name}'
    path = os.path.join(LUMINAIR_DIR, stored_name)
    f.save(path)
    file_size = os.path.getsize(path)
    logger.info('Uploaded %s (%d bytes) as %s', f.filename, file_size, stored_name)

    try:
        _controller().import_luminair(path)
        fix_count = len(_controller().fixtures)
        scene_count = len(_controller().scenes)
        add_upload(stored_name, f.filename, file_size, fix_count, scene_count)
        return jsonify({
            'ok': True,
            'fixtures': fix_count,
            'scenes': scene_count,
        })
    except Exception as e:
        logger.exception('Failed to parse %s', f.filename)
        os.remove(path)
        return jsonify({'ok': False, 'error': str(e)}), 500


@api_bp.route('/api/luminair/history')
@require_auth
def luminair_history():
    return jsonify(get_uploads())


@api_bp.route('/api/luminair/activate/<int:upload_id>', methods=['POST'])
@require_auth
def activate_upload(upload_id):
    filename = set_active_upload(upload_id)
    if not filename:
        return jsonify({'ok': False, 'error': 'upload not found'}), 404
    path = os.path.join(LUMINAIR_DIR, filename)
    if not os.path.exists(path):
        return jsonify({'ok': False, 'error': 'file missing'}), 404
    try:
        _controller().import_luminair(path)
        return jsonify({'ok': True, 'fixtures': len(_controller().fixtures),
                        'scenes': len(_controller().scenes)})
    except Exception as e:
        logger.exception('Failed to load %s', filename)
        return jsonify({'ok': False, 'error': str(e)}), 500


@api_bp.route('/api/luminair/delete/<int:upload_id>', methods=['DELETE'])
@require_auth
def delete_upload_entry(upload_id):
    info = db_delete_upload(upload_id)
    if not info:
        return jsonify({'ok': False, 'error': 'not found'}), 404
    if info['active']:
        return jsonify({'ok': False, 'error': 'cannot delete active file'}), 400
    path = os.path.join(LUMINAIR_DIR, info['filename'])
    if os.path.exists(path):
        os.remove(path)
    return jsonify({'ok': True})


@api_bp.route('/api/luminair/diff/<int:id_a>/<int:id_b>')
@require_auth
def diff_uploads(id_a, id_b):
    uploads = {u['id']: u for u in get_uploads()}
    if id_a not in uploads or id_b not in uploads:
        return jsonify({'ok': False, 'error': 'upload not found'}), 404

    from app.luminair.parser import parse_luminair
    from app.luminair.diff import diff_luminair

    path_a = os.path.join(LUMINAIR_DIR, uploads[id_a]['filename'])
    path_b = os.path.join(LUMINAIR_DIR, uploads[id_b]['filename'])
    if not os.path.exists(path_a) or not os.path.exists(path_b):
        return jsonify({'ok': False, 'error': 'file missing'}), 404

    fix_a, scenes_a = parse_luminair(path_a)
    fix_b, scenes_b = parse_luminair(path_b)
    result = diff_luminair(fix_a, scenes_a, fix_b, scenes_b)
    result['file_a'] = uploads[id_a]['original_name'] + ' (' + uploads[id_a]['uploaded_at'] + ')'
    result['file_b'] = uploads[id_b]['original_name'] + ' (' + uploads[id_b]['uploaded_at'] + ')'
    return jsonify(result)


# ------------------------------------------------------------------
# Debug
# ------------------------------------------------------------------

@api_bp.route('/api/dmx')
@require_auth
def dmx_output():
    output = _engine().get_dmx_output()
    non_zero = {i + 1: v for i, v in enumerate(output) if v > 0}
    return jsonify({'channels': non_zero, 'total_active': len(non_zero)})
