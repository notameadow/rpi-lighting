"""Top-level controller: ties Luminair parser to DMX engine."""

import os
import glob
import logging
from app.config import LUMINAIR_DIR
from app.luminair.parser import parse_luminair
from app.database import get_scene_overrides, get_setting, get_deleted_scene_ids, get_custom_scenes, get_uploads

logger = logging.getLogger('lighting.controller')


class Controller:
    def __init__(self, engine):
        self.engine = engine
        self.fixtures = []
        self.scenes = []
        self._luminair_path = None

        # Load active upload from DB, or fall back to most recent file
        os.makedirs(LUMINAIR_DIR, exist_ok=True)
        uploads = get_uploads()
        active = [u for u in uploads if u['active']]
        if active:
            path = os.path.join(LUMINAIR_DIR, active[0]['filename'])
            if os.path.exists(path):
                try:
                    self.load_luminair(path)
                    return
                except Exception:
                    logger.exception('Failed to load active upload %s', active[0]['filename'])

        files = sorted(glob.glob(os.path.join(LUMINAIR_DIR, '*.luminair')),
                        key=os.path.getmtime, reverse=True)
        if files:
            try:
                self.load_luminair(files[0])
            except Exception:
                logger.exception('Failed to auto-load %s', files[0])

    def load_luminair(self, filepath):
        """Parse a Luminair file, apply saved overrides, push to engine."""
        fixtures, scenes = parse_luminair(filepath)
        self.fixtures = fixtures
        self.scenes = scenes
        self._luminair_path = filepath

        # Remove deleted scenes, add custom scenes, apply overrides
        deleted = get_deleted_scene_ids()
        if deleted:
            self.scenes = [s for s in self.scenes if s.id not in deleted]
            logger.info('Removed %d deleted scenes', len(deleted))

        from app.luminair.models import Scene
        for cs in get_custom_scenes():
            self.scenes.append(Scene(
                id=cs['id'], name=cs['name'], dmx_values=cs['dmx_values'],
                channel_mask=cs['channel_mask'], fade_in=cs['fade_in'],
                fade_out=cs['fade_out'], button_color=cs['button_color'],
                locked=False, master_level=cs['master_level'],
            ))

        self._apply_overrides()

        self.engine.set_fixtures(fixtures)
        self.engine.set_scenes(scenes)
        logger.info('Loaded %s: %d fixtures, %d scenes',
                     os.path.basename(filepath), len(fixtures), len(scenes))

        # Recall default scene if configured
        default_id = get_setting('default_scene')
        if default_id:
            try:
                scene_id = int(default_id)
                if self.engine.recall_scene(scene_id, fade_time=0):
                    scene = next((s for s in scenes if s.id == scene_id), None)
                    name = scene.name if scene else '?'
                    logger.info('Default scene recalled: %d "%s"', scene_id, name)
            except (ValueError, TypeError):
                pass

    def _apply_overrides(self):
        """Apply saved fade times and fixture masks from the database."""
        overrides = get_scene_overrides()
        applied = 0
        for scene in self.scenes:
            ov = overrides.get(scene.id)
            if ov is None:
                continue
            if ov['fade_in'] is not None:
                scene.fade_in = ov['fade_in']
            if ov['fixture_ids'] is not None:
                # Rebuild channel mask from fixture IDs
                new_mask = set()
                fid_set = set(ov['fixture_ids'])
                for fix in self.fixtures:
                    if fix.id in fid_set:
                        base = fix.dmx_address - 1
                        for offset in range(fix.channel_count):
                            new_mask.add(base + offset)
                scene.channel_mask = new_mask
            applied += 1
        if applied:
            logger.info('Applied %d saved scene overrides', applied)
