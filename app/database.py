"""SQLite persistence for lighting controller state."""

import os
import json
import sqlite3
import logging
from app.config import DB_PATH, DATA_DIR

logger = logging.getLogger('lighting.db')


def _connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _connect()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS scene_overrides (
            scene_id INTEGER PRIMARY KEY,
            fade_in REAL,
            fixture_ids TEXT,
            dmx_values BLOB
        );
        CREATE TABLE IF NOT EXISTS deleted_scenes (
            scene_id INTEGER PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS luminair_uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            original_name TEXT,
            uploaded_at TEXT,
            file_size INTEGER,
            fixture_count INTEGER,
            scene_count INTEGER,
            active INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS custom_scenes (
            id INTEGER PRIMARY KEY,
            name TEXT,
            dmx_values BLOB,
            channel_mask TEXT,
            fade_in REAL,
            fade_out REAL,
            button_color TEXT,
            master_level REAL
        );
        CREATE TABLE IF NOT EXISTS fixtures (
            id INTEGER PRIMARY KEY,
            name TEXT,
            model TEXT,
            manufacturer TEXT,
            dmx_address INTEGER,
            channel_count INTEGER,
            profile_channels TEXT,
            grp TEXT
        );
        CREATE TABLE IF NOT EXISTS scenes (
            id INTEGER PRIMARY KEY,
            name TEXT,
            dmx_values BLOB,
            channel_mask TEXT,
            fade_in REAL,
            fade_out REAL,
            button_color TEXT,
            locked INTEGER DEFAULT 0,
            master_level REAL DEFAULT 1.0
        );
    ''')
    # Migrations
    try:
        conn.execute('ALTER TABLE scene_overrides ADD COLUMN dmx_values BLOB')
    except Exception:
        pass
    try:
        conn.execute('ALTER TABLE scenes ADD COLUMN position INTEGER DEFAULT 0')
    except Exception:
        pass
    # Backfill position for existing scenes that have position=0
    rows = conn.execute('SELECT id FROM scenes WHERE position=0 ORDER BY id').fetchall()
    if rows:
        for i, row in enumerate(rows):
            conn.execute('UPDATE scenes SET position=? WHERE id=?', (i, row['id']))
        conn.commit()
    conn.close()
    logger.info('Database initialized')


def get_setting(key, default=None):
    conn = _connect()
    row = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    conn.close()
    return row['value'] if row else default


def set_setting(key, value):
    conn = _connect()
    conn.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
                 (key, str(value)))
    conn.commit()
    conn.close()


def get_scene_overrides():
    """Return {scene_id: {'fade_in': float, 'fixture_ids': list|None, 'dmx_values': bytes|None}}."""
    conn = _connect()
    rows = conn.execute('SELECT scene_id, fade_in, fixture_ids, dmx_values FROM scene_overrides').fetchall()
    conn.close()
    result = {}
    for row in rows:
        fix_ids = json.loads(row['fixture_ids']) if row['fixture_ids'] else None
        result[row['scene_id']] = {
            'fade_in': row['fade_in'],
            'fixture_ids': fix_ids,
            'dmx_values': bytes(row['dmx_values']) if row['dmx_values'] else None,
        }
    return result


def save_scene_fade(scene_id, fade_in):
    conn = _connect()
    existing = conn.execute('SELECT fixture_ids FROM scene_overrides WHERE scene_id=?',
                            (scene_id,)).fetchone()
    fix_ids = existing['fixture_ids'] if existing else None
    conn.execute('INSERT OR REPLACE INTO scene_overrides (scene_id, fade_in, fixture_ids) VALUES (?, ?, ?)',
                 (scene_id, fade_in, fix_ids))
    conn.commit()
    conn.close()


def add_upload(filename, original_name, file_size, fixture_count, scene_count):
    conn = _connect()
    conn.execute('UPDATE luminair_uploads SET active=0')
    conn.execute('''INSERT INTO luminair_uploads
        (filename, original_name, uploaded_at, file_size, fixture_count, scene_count, active)
        VALUES (?, ?, datetime('now'), ?, ?, ?, 1)''',
        (filename, original_name, file_size, fixture_count, scene_count))
    conn.commit()
    conn.close()


def get_uploads():
    conn = _connect()
    rows = conn.execute(
        'SELECT * FROM luminair_uploads ORDER BY uploaded_at DESC').fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_active_upload(upload_id):
    conn = _connect()
    conn.execute('UPDATE luminair_uploads SET active=0')
    conn.execute('UPDATE luminair_uploads SET active=1 WHERE id=?', (upload_id,))
    row = conn.execute('SELECT filename FROM luminair_uploads WHERE id=?', (upload_id,)).fetchone()
    conn.commit()
    conn.close()
    return row['filename'] if row else None


def delete_upload(upload_id):
    conn = _connect()
    row = conn.execute('SELECT filename, active FROM luminair_uploads WHERE id=?', (upload_id,)).fetchone()
    conn.commit()
    conn.close()
    if row:
        return {'filename': row['filename'], 'active': bool(row['active'])}
    return None


def get_deleted_scene_ids():
    conn = _connect()
    rows = conn.execute('SELECT scene_id FROM deleted_scenes').fetchall()
    conn.close()
    return {row['scene_id'] for row in rows}


def add_deleted_scene(scene_id):
    conn = _connect()
    conn.execute('INSERT OR IGNORE INTO deleted_scenes (scene_id) VALUES (?)', (scene_id,))
    conn.commit()
    conn.close()


def remove_deleted_scene(scene_id):
    conn = _connect()
    conn.execute('DELETE FROM deleted_scenes WHERE scene_id=?', (scene_id,))
    conn.commit()
    conn.close()


def get_custom_scenes():
    conn = _connect()
    rows = conn.execute('SELECT * FROM custom_scenes').fetchall()
    conn.close()
    result = []
    for row in rows:
        mask = set(json.loads(row['channel_mask'])) if row['channel_mask'] else set()
        result.append({
            'id': row['id'],
            'name': row['name'],
            'dmx_values': bytes(row['dmx_values']),
            'channel_mask': mask,
            'fade_in': row['fade_in'] or 0.0,
            'fade_out': row['fade_out'] or 0.2,
            'button_color': row['button_color'] or 'rgba(255,255,255,1)',
            'master_level': row['master_level'] or 1.0,
        })
    return result


def save_custom_scene(scene_id, name, dmx_values, channel_mask, fade_in, fade_out, button_color, master_level):
    conn = _connect()
    conn.execute('''INSERT OR REPLACE INTO custom_scenes
        (id, name, dmx_values, channel_mask, fade_in, fade_out, button_color, master_level)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (scene_id, name, bytes(dmx_values), json.dumps(sorted(channel_mask)),
         fade_in, fade_out, button_color, master_level))
    conn.commit()
    conn.close()


def delete_custom_scene(scene_id):
    conn = _connect()
    conn.execute('DELETE FROM custom_scenes WHERE id=?', (scene_id,))
    conn.commit()
    conn.close()


def next_custom_scene_id():
    conn = _connect()
    row = conn.execute('SELECT MAX(id) as m FROM custom_scenes').fetchone()
    conn.close()
    max_id = row['m'] if row and row['m'] is not None else 999
    return max(1000, max_id + 1)


def save_scene_dmx(scene_id, dmx_values):
    """Save modified DMX values for a scene."""
    conn = _connect()
    existing = conn.execute('SELECT fade_in, fixture_ids FROM scene_overrides WHERE scene_id=?',
                            (scene_id,)).fetchone()
    fade_in = existing['fade_in'] if existing else None
    fix_ids = existing['fixture_ids'] if existing else None
    conn.execute('''INSERT OR REPLACE INTO scene_overrides (scene_id, fade_in, fixture_ids, dmx_values)
        VALUES (?, ?, ?, ?)''', (scene_id, fade_in, fix_ids, bytes(dmx_values)))
    conn.commit()
    conn.close()


# ------------------------------------------------------------------
# Full scene/fixture storage (DB as source of truth)
# ------------------------------------------------------------------

def has_stored_data():
    """Check if DB has scenes and fixtures stored."""
    conn = _connect()
    sc = conn.execute('SELECT COUNT(*) as c FROM scenes').fetchone()['c']
    fx = conn.execute('SELECT COUNT(*) as c FROM fixtures').fetchone()['c']
    conn.close()
    return sc > 0 and fx > 0


def store_fixtures(fixtures):
    """Write full fixture list to DB, replacing existing."""
    conn = _connect()
    conn.execute('DELETE FROM fixtures')
    for f in fixtures:
        conn.execute('''INSERT INTO fixtures (id, name, model, manufacturer,
            dmx_address, channel_count, profile_channels, grp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (f.id, f.name, f.model, f.manufacturer, f.dmx_address,
             f.channel_count, json.dumps(f.profile.channels), f.group))
    conn.commit()
    conn.close()
    logger.info('Stored %d fixtures to DB', len(fixtures))


def store_scenes(scenes):
    """Write full scene list to DB, replacing existing."""
    conn = _connect()
    conn.execute('DELETE FROM scenes')
    for i, s in enumerate(scenes):
        conn.execute('''INSERT INTO scenes (id, name, dmx_values, channel_mask,
            fade_in, fade_out, button_color, locked, master_level, position)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (s.id, s.name, bytes(s.dmx_values), json.dumps(sorted(s.channel_mask)),
             s.fade_in, s.fade_out, s.button_color, int(s.locked), s.master_level, i))
    conn.commit()
    conn.close()
    logger.info('Stored %d scenes to DB', len(scenes))


def load_fixtures():
    """Load fixtures from DB. Returns list of dicts or empty list."""
    conn = _connect()
    rows = conn.execute('SELECT * FROM fixtures ORDER BY id').fetchall()
    conn.close()
    return [dict(r) for r in rows]


def load_scenes():
    """Load scenes from DB. Returns list of dicts or empty list."""
    conn = _connect()
    rows = conn.execute('SELECT * FROM scenes ORDER BY position, id').fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_scene(scene_id, **kwargs):
    """Update specific fields of a scene in DB."""
    conn = _connect()
    for key, value in kwargs.items():
        if key == 'dmx_values':
            value = bytes(value)
        elif key == 'channel_mask':
            value = json.dumps(sorted(value))
        elif key == 'locked':
            value = int(value)
        conn.execute(f'UPDATE scenes SET {key}=? WHERE id=?', (value, scene_id))
    conn.commit()
    conn.close()


def delete_scene(scene_id):
    """Delete a scene from DB."""
    conn = _connect()
    conn.execute('DELETE FROM scenes WHERE id=?', (scene_id,))
    conn.commit()
    conn.close()


def add_scene(scene):
    """Add a new scene to DB. Returns the assigned ID."""
    conn = _connect()
    row = conn.execute('SELECT MAX(id) as m FROM scenes').fetchone()
    new_id = max(1000, (row['m'] or 0) + 1)
    conn.execute('''INSERT INTO scenes (id, name, dmx_values, channel_mask,
        fade_in, fade_out, button_color, locked, master_level)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (new_id, scene.name, bytes(scene.dmx_values), json.dumps(sorted(scene.channel_mask)),
         scene.fade_in, scene.fade_out, scene.button_color, int(scene.locked), scene.master_level))
    conn.commit()
    conn.close()
    return new_id


def reorder_scenes(scene_ids):
    """Set scene positions from an ordered list of IDs."""
    conn = _connect()
    for i, sid in enumerate(scene_ids):
        conn.execute('UPDATE scenes SET position=? WHERE id=?', (i, sid))
    conn.commit()
    conn.close()


def save_scene_fixtures(scene_id, fixture_ids):
    conn = _connect()
    existing = conn.execute('SELECT fade_in FROM scene_overrides WHERE scene_id=?',
                            (scene_id,)).fetchone()
    fade_in = existing['fade_in'] if existing else None
    conn.execute('INSERT OR REPLACE INTO scene_overrides (scene_id, fade_in, fixture_ids) VALUES (?, ?, ?)',
                 (scene_id, fade_in, json.dumps(fixture_ids)))
    conn.commit()
    conn.close()
