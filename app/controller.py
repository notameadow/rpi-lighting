"""Top-level controller: manages fixtures and scenes.

DB is the source of truth. Luminair files are an import mechanism only —
parsed on upload, stored to DB, never re-parsed on boot.
"""

import os
import logging
from app.config import LUMINAIR_DIR
from app.luminair.parser import parse_luminair
from app.luminair.models import Fixture, Scene, ChannelProfile
from app.database import (
    has_stored_data, store_fixtures, store_scenes, load_fixtures, load_scenes,
    update_scene, delete_scene as db_delete_scene, add_scene as db_add_scene,
    get_setting,
)

logger = logging.getLogger('lighting.controller')


class Controller:
    def __init__(self, engine):
        self.engine = engine
        self.fixtures = []
        self.scenes = []
        engine._on_scene_modified = self._persist_scene

        if has_stored_data():
            self._load_from_db()
        else:
            logger.info('No stored data — waiting for Luminair upload')

    def _load_from_db(self):
        """Load fixtures and scenes from DB."""
        import json

        raw_fixtures = load_fixtures()
        self.fixtures = []
        for r in raw_fixtures:
            channels = json.loads(r['profile_channels'])
            self.fixtures.append(Fixture(
                id=r['id'], name=r['name'], model=r['model'],
                manufacturer=r['manufacturer'], dmx_address=r['dmx_address'],
                channel_count=r['channel_count'],
                profile=ChannelProfile(channels=channels),
                group=r['grp'],
            ))

        raw_scenes = load_scenes()
        self.scenes = []
        for r in raw_scenes:
            mask = set(json.loads(r['channel_mask'])) if r['channel_mask'] else set()
            self.scenes.append(Scene(
                id=r['id'], name=r['name'],
                dmx_values=bytes(r['dmx_values']) if r['dmx_values'] else bytes(512),
                channel_mask=mask,
                fade_in=r['fade_in'] or 0.0,
                fade_out=r['fade_out'] or 0.2,
                button_color=r['button_color'] or 'rgba(255,255,255,1)',
                locked=bool(r['locked']),
                master_level=r['master_level'] or 1.0,
            ))

        self.engine.set_fixtures(self.fixtures)
        self.engine.set_scenes(self.scenes)
        logger.info('Loaded from DB: %d fixtures, %d scenes', len(self.fixtures), len(self.scenes))

        # Recall default scene if configured
        default_id = get_setting('default_scene')
        if default_id:
            try:
                scene_id = int(default_id)
                if self.engine.recall_scene(scene_id, fade_time=0):
                    scene = next((s for s in self.scenes if s.id == scene_id), None)
                    name = scene.name if scene else '?'
                    logger.info('Default scene recalled: %d "%s"', scene_id, name)
            except (ValueError, TypeError):
                pass

    def import_luminair(self, filepath):
        """Parse a Luminair file and store everything to DB. Replaces all data."""
        fixtures, scenes = parse_luminair(filepath)
        self.fixtures = fixtures
        self.scenes = scenes

        store_fixtures(fixtures)
        store_scenes(scenes)

        self.engine.set_fixtures(fixtures)
        self.engine.set_scenes(scenes)
        logger.info('Imported %s: %d fixtures, %d scenes (stored to DB)',
                     os.path.basename(filepath), len(fixtures), len(scenes))

    def _persist_scene(self, scene):
        """Called by engine when a scene's DMX values are modified by fader."""
        update_scene(scene.id, dmx_values=scene.dmx_values)
