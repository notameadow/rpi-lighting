"""Parse Luminair .luminair save files (NSKeyedArchiver binary plists)."""

import plistlib
import logging
from app.luminair.models import (
    Fixture, Scene, ChannelProfile,
    SLIMPAR_PROFILE, RETRO_BLINDER_PROFILE, DUOBLIND_PROFILE, HAZE_PROFILE,
)

logger = logging.getLogger('lighting.luminair')


# ---------------------------------------------------------------------------
# NSKeyedArchiver helpers
# ---------------------------------------------------------------------------

def _load_archive(filepath):
    """Load a binary plist and return the $objects array."""
    with open(filepath, 'rb') as f:
        data = plistlib.load(f)
    return data['$objects'], data['$top']['root']


def _resolve(objects, uid):
    """Resolve a plistlib.UID to its value in the $objects array."""
    if isinstance(uid, plistlib.UID):
        return objects[uid.data]
    return uid


def _resolve_str(objects, val):
    """Resolve a value that might be an NSString dict."""
    v = _resolve(objects, val)
    if isinstance(v, dict) and 'NS.string' in v:
        return v['NS.string']
    if isinstance(v, str):
        return v
    return v


def _unarchive_array(objects, obj):
    """Unarchive an NSArray/NSMutableArray."""
    obj = _resolve(objects, obj)
    if isinstance(obj, dict) and 'NS.objects' in obj and 'NS.keys' not in obj:
        return [_resolve(objects, v) for v in obj['NS.objects']]
    return obj


def _unarchive_dict(objects, obj):
    """Unarchive an NSDictionary/NSMutableDictionary."""
    obj = _resolve(objects, obj)
    if isinstance(obj, dict) and 'NS.keys' in obj:
        keys = [_resolve(objects, k) for k in obj['NS.keys']]
        vals = [_resolve(objects, v) for v in obj['NS.objects']]
        return dict(zip(keys, vals))
    return obj


def _deep_resolve(objects, obj):
    """Resolve all UID references one level deep in a dict."""
    if isinstance(obj, plistlib.UID):
        return _deep_resolve(objects, _resolve(objects, obj))
    if isinstance(obj, dict):
        return {
            (_resolve(objects, k) if isinstance(k, plistlib.UID) else k):
            (_resolve(objects, v) if isinstance(v, plistlib.UID) else v)
            for k, v in obj.items()
        }
    return obj


def _get_class_name(objects, obj):
    if isinstance(obj, dict) and '$class' in obj:
        cls = _resolve(objects, obj['$class'])
        if isinstance(cls, dict):
            return cls.get('$classname', '')
    return ''


# ---------------------------------------------------------------------------
# Fixture profile detection
# ---------------------------------------------------------------------------

def _detect_profile(model_name, num_channels):
    """Map model name to a known channel profile."""
    model = str(model_name).lower()
    if 'slimpar' in model:
        return SLIMPAR_PROFILE, 'slimpar'
    if 'retro' in model or 'blinder tri' in model:
        return RETRO_BLINDER_PROFILE, 'triangle'
    if 'duoblind' in model:
        return DUOBLIND_PROFILE, 'droid'
    if num_channels == 1:
        return HAZE_PROFILE, 'haze'
    # Fallback: generic intensity-only
    return ChannelProfile(channels=['Intensity'] * num_channels), 'unknown'


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_luminair(filepath):
    """
    Parse a .luminair file and return (fixtures, scenes).

    The channel_mask on each scene is derived from trackEditStates — Luminair's
    per-scene track selection. Only tracks marked as edited are included in the
    mask. This correctly distinguishes "intentionally zero" (edited, value=0)
    from "don't touch" (not edited).

    Returns:
        fixtures: list of Fixture
        scenes: list of Scene
    """
    objects, root_uid = _load_archive(filepath)
    root = _unarchive_dict(objects, objects[root_uid.data])

    fixtures = _extract_fixtures(objects, root)

    # Build sub-track → DMX channel index map from OutputTracks
    track_to_dmx = _build_track_to_dmx_map(objects, root, fixtures)

    scenes = _extract_scenes(objects, root, track_to_dmx)

    logger.info('Parsed %s: %d fixtures, %d scenes', filepath, len(fixtures), len(scenes))
    return fixtures, scenes


def _build_track_to_dmx_map(objects, root, fixtures):
    """Build a mapping from sub-track index to DMX channel index.

    OutputTracks contains groups, each with sub-tracks (GroupTracks).
    The sub-track order matches trackEditStates in each scene.
    """
    tracks = _unarchive_array(objects, root.get('OutputTracks', {}))
    if not isinstance(tracks, list):
        return {}

    track_to_dmx = {}  # sub-track index -> DMX channel index (0-based)
    sub_idx = 0

    for i, t_raw in enumerate(tracks):
        t = _deep_resolve(objects, t_raw)
        cls = _get_class_name(objects, t_raw if isinstance(t_raw, dict) else _resolve(objects, t_raw))

        if cls == 'SFXLuminairTrackGroup':
            gt_arr = _unarchive_array(objects, t.get('GroupTracks', {}))
            if isinstance(gt_arr, list):
                # Find the fixture for this group to get DMX base address
                group_name = _resolve_str(objects, t.get('GroupName', ''))
                fix = None
                for f in fixtures:
                    if f.name == str(group_name):
                        fix = f
                        break
                for j, st_raw in enumerate(gt_arr):
                    st = _deep_resolve(objects, st_raw)
                    start_ch = st.get('startChannel', st.get('channelStart'))
                    if isinstance(start_ch, int):
                        track_to_dmx[sub_idx] = start_ch - 1  # 0-based
                    elif fix:
                        track_to_dmx[sub_idx] = fix.dmx_address - 1 + j
                    sub_idx += 1
        else:
            # Single track (e.g. Haze)
            start_ch = t.get('startChannel', t.get('channelStart'))
            if isinstance(start_ch, int):
                track_to_dmx[sub_idx] = start_ch - 1
            sub_idx += 1

    return track_to_dmx


def _extract_fixtures(objects, root):
    """Extract fixture patch from OutputTracks."""
    tracks = _unarchive_array(objects, root.get('OutputTracks', {}))
    if not isinstance(tracks, list):
        return []

    fixtures = []
    for i, t_raw in enumerate(tracks):
        t = _deep_resolve(objects, t_raw)
        cls = _get_class_name(objects, t_raw if isinstance(t_raw, dict) else _resolve(objects, t_raw))

        group_name = _resolve_str(objects, t.get('GroupName', t.get('name', f'Fixture {i+1}')))
        model = _resolve_str(objects, t.get('modelName', t.get('fixtureName', 'Unknown')))
        manufacturer = _resolve_str(objects, t.get('manufacturerName', ''))
        num_ch = t.get('numberOfChannels', 1)

        # Get DMX start channel from group tracks or track itself
        dmx_start = None
        if cls == 'SFXLuminairTrackGroup':
            gt_arr = _unarchive_array(objects, t.get('GroupTracks', {}))
            if isinstance(gt_arr, list) and len(gt_arr) > 0:
                first = _deep_resolve(objects, gt_arr[0])
                dmx_start = first.get('startChannel', first.get('channelStart'))
        else:
            dmx_start = t.get('startChannel', t.get('channelStart'))

        if dmx_start is None or not isinstance(dmx_start, int):
            logger.warning('Fixture %d "%s": could not determine DMX address, skipping', i, group_name)
            continue

        profile, group = _detect_profile(model, num_ch)

        # Handle haze special case (single custom channel, not in a group)
        if cls == 'SFXLuminairOutTrack' and num_ch is None:
            num_ch = 1

        fixtures.append(Fixture(
            id=i,
            name=str(group_name),
            model=str(model),
            manufacturer=str(manufacturer) if manufacturer else '',
            dmx_address=int(dmx_start),
            channel_count=int(num_ch) if isinstance(num_ch, int) else len(profile.channels),
            profile=profile,
            group=group,
        ))

    return fixtures


def _extract_scenes(objects, root, track_to_dmx=None):
    """Extract scenes with DMX snapshots from OutputCueScenes."""
    scenes_arr = _unarchive_array(objects, root.get('OutputCueScenes', {}))
    if not isinstance(scenes_arr, list):
        return []

    scenes = []
    for i, scene_raw in enumerate(scenes_arr):
        scene = _deep_resolve(objects, scene_raw)
        name = _resolve_str(objects, scene.get('name', scene.get('Name', f'Scene {i+1}')))
        fade_in = scene.get('transitionDurationIn', 0.0)
        fade_out = scene.get('transitionDurationOut', 0.2)
        btn_color = _resolve_str(objects, scene.get('buttonColorAsString', '1 1 1 1'))
        locked = scene.get('CueIsLocked', False)
        master = scene.get('cueMasterLevel', 1.0)

        # Extract DMX values (full 512-byte snapshot)
        dmx_values, _ = _extract_dmx_values(objects, scene)

        # Build channel mask from trackEditStates — this is Luminair's
        # per-scene track selection (True = this scene controls that track)
        channel_mask = set()
        if track_to_dmx:
            tes = _unarchive_array(objects, scene.get('trackEditStates', {}))
            if isinstance(tes, list):
                for idx, val in enumerate(tes):
                    if _resolve(objects, val) and idx in track_to_dmx:
                        dmx_idx = track_to_dmx[idx]
                        if 0 <= dmx_idx < 512:
                            channel_mask.add(dmx_idx)

        # Convert button color "R G B A" to CSS rgba
        css_color = _luminair_color_to_css(str(btn_color) if btn_color else '1 1 1 1')

        if not isinstance(fade_in, (int, float)):
            fade_in = 0.0
        if not isinstance(fade_out, (int, float)):
            fade_out = 0.2
        if not isinstance(master, (int, float)):
            master = 1.0

        scenes.append(Scene(
            id=i,
            name=str(name) if name else f'Scene {i+1}',
            dmx_values=bytes(dmx_values),
            channel_mask=channel_mask,
            fade_in=float(fade_in),
            fade_out=float(fade_out),
            button_color=css_color,
            locked=bool(locked),
            master_level=float(master),
        ))

    return scenes


def _extract_dmx_values(objects, scene):
    """Extract 512-byte DMX snapshot and channel mask from a scene.

    Returns (dmx_values, channel_mask) where channel_mask is the set of
    0-based channel indices that this scene actively controls. Channels
    outside the mask are "don't care" and should not be modified on recall.
    """
    dmx = bytearray(512)
    mask = set()

    ch_data_raw = scene.get('allUniversesChannelData', None)
    if ch_data_raw is None:
        return dmx, mask

    ch_arr = _unarchive_array(objects, ch_data_raw)
    if not isinstance(ch_arr, list) or len(ch_arr) == 0:
        return dmx, mask

    # First element is Universe 1 data
    uni1_arr = _unarchive_array(objects, ch_arr[0])
    if not isinstance(uni1_arr, list):
        return dmx, mask

    for i, ch_obj in enumerate(uni1_arr):
        if i >= 512:
            break
        # SLDMXChannel objects have a 'value' key — these are controlled.
        # Placeholder objects (just $class) are uncontrolled.
        if isinstance(ch_obj, dict) and 'value' in ch_obj:
            mask.add(i)
            val = _resolve(objects, ch_obj['value'])
            if isinstance(val, (int, float)):
                dmx[i] = max(0, min(255, int(val)))

    return dmx, mask


def _luminair_color_to_css(color_str):
    """Convert Luminair "R G B A" (0-1 floats) to CSS rgba()."""
    try:
        parts = color_str.strip().split()
        if len(parts) >= 3:
            r = int(float(parts[0]) * 255)
            g = int(float(parts[1]) * 255)
            b = int(float(parts[2]) * 255)
            a = float(parts[3]) if len(parts) >= 4 else 1.0
            return f'rgba({r},{g},{b},{a})'
    except (ValueError, IndexError):
        pass
    return 'rgba(255,255,255,1)'
