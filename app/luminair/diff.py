"""Compare two parsed Luminair files and produce a readable diff."""


def diff_luminair(fixtures_a, scenes_a, fixtures_b, scenes_b):
    """Compare two Luminair parses. A=old, B=new. Returns a diff dict."""
    result = {
        'fixtures': _diff_fixtures(fixtures_a, fixtures_b),
        'scenes': _diff_scenes(fixtures_b, scenes_a, scenes_b),
        'summary': {},
    }
    # Summary counts
    sf = result['fixtures']
    ss = result['scenes']
    result['summary'] = {
        'fixtures_added': len(sf['added']),
        'fixtures_removed': len(sf['removed']),
        'fixtures_moved': len(sf['moved']),
        'scenes_added': len(ss['added']),
        'scenes_removed': len(ss['removed']),
        'scenes_modified': len(ss['modified']),
        'scenes_unchanged': len(ss['unchanged']),
    }
    return result


def _diff_fixtures(fix_a, fix_b):
    """Compare fixture lists by name."""
    a_by_name = {f.name: f for f in fix_a}
    b_by_name = {f.name: f for f in fix_b}

    added = []
    removed = []
    moved = []

    for name, fb in b_by_name.items():
        if name not in a_by_name:
            added.append({'name': name, 'model': fb.model, 'dmx': fb.dmx_address})
        else:
            fa = a_by_name[name]
            if fa.dmx_address != fb.dmx_address:
                moved.append({'name': name, 'from': fa.dmx_address, 'to': fb.dmx_address})

    for name, fa in a_by_name.items():
        if name not in b_by_name:
            removed.append({'name': name, 'model': fa.model, 'dmx': fa.dmx_address})

    return {'added': added, 'removed': removed, 'moved': moved}


def _diff_scenes(fixtures, scenes_a, scenes_b):
    """Compare scene lists by name, then DMX values for matching scenes."""
    a_by_name = {s.name: s for s in scenes_a}
    b_by_name = {s.name: s for s in scenes_b}

    added = []
    removed = []
    modified = []
    unchanged = []

    for name, sb in b_by_name.items():
        if name not in a_by_name:
            added.append({'name': name, 'color': sb.button_color})
            continue
        sa = a_by_name[name]
        changes = _diff_scene_detail(fixtures, sa, sb)
        if changes:
            modified.append({'name': name, 'changes': changes})
        else:
            unchanged.append(name)

    for name in a_by_name:
        if name not in b_by_name:
            removed.append({'name': name, 'color': a_by_name[name].button_color})

    return {'added': added, 'removed': removed, 'modified': modified, 'unchanged': unchanged}


def _diff_scene_detail(fixtures, sa, sb):
    """Compare two scenes with the same name. Returns list of changes or empty."""
    changes = []

    # Fade times
    if abs(sa.fade_in - sb.fade_in) > 0.01:
        changes.append(f'fade_in: {sa.fade_in}s → {sb.fade_in}s')
    if abs(sa.fade_out - sb.fade_out) > 0.01:
        changes.append(f'fade_out: {sa.fade_out}s → {sb.fade_out}s')

    # Button color
    if sa.button_color != sb.button_color:
        changes.append(f'color: {sa.button_color} → {sb.button_color}')

    # DMX values — report per fixture
    for fix in fixtures:
        base = fix.dmx_address - 1
        ch_changes = []
        for offset, ch_name in enumerate(fix.profile.channels):
            idx = base + offset
            if idx >= 512:
                continue
            va = sa.dmx_values[idx] if idx < len(sa.dmx_values) else 0
            vb = sb.dmx_values[idx] if idx < len(sb.dmx_values) else 0
            if va != vb:
                ch_changes.append(f'{ch_name}: {va}→{vb}')
        if ch_changes:
            changes.append(f'{fix.name}: {", ".join(ch_changes)}')

    return changes
