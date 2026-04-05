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
            fixture_ids TEXT
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
    ''')
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
    """Return {scene_id: {'fade_in': float, 'fixture_ids': list|None}}."""
    conn = _connect()
    rows = conn.execute('SELECT scene_id, fade_in, fixture_ids FROM scene_overrides').fetchall()
    conn.close()
    result = {}
    for row in rows:
        fix_ids = json.loads(row['fixture_ids']) if row['fixture_ids'] else None
        result[row['scene_id']] = {
            'fade_in': row['fade_in'],
            'fixture_ids': fix_ids,
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


def save_scene_fixtures(scene_id, fixture_ids):
    conn = _connect()
    existing = conn.execute('SELECT fade_in FROM scene_overrides WHERE scene_id=?',
                            (scene_id,)).fetchone()
    fade_in = existing['fade_in'] if existing else None
    conn.execute('INSERT OR REPLACE INTO scene_overrides (scene_id, fade_in, fixture_ids) VALUES (?, ?, ?)',
                 (scene_id, fade_in, json.dumps(fixture_ids)))
    conn.commit()
    conn.close()
