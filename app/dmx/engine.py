import time
import random
import threading
import logging

from app.config import ARTNET_REFRESH_HZ, DMX_CHANNELS

logger = logging.getLogger('lighting.dmx')

# Channels that should be affected by master fader.
# All others (Mode, Strobe, Speed, Macro) pass through unscaled.
INTENSITY_CHANNELS = frozenset([
    'Red', 'Green', 'Blue', 'White', 'Intensity', 'Dim', 'Level',
])


class DMXEngine:
    """40Hz DMX output loop with crossfade interpolation."""

    def __init__(self, sender):
        self._lock = threading.RLock()
        self._sender = sender

        self._current = bytearray(DMX_CHANNELS)
        self._target = bytearray(DMX_CHANNELS)
        self._fade_start = bytearray(DMX_CHANNELS)
        self._fade_start_time = 0.0
        self._fade_duration = 0.0

        self._master = 255
        self._master_fade_start = 255
        self._master_fade_target = 255
        self._master_fade_start_time = 0.0
        self._master_fade_duration = 0.0
        self._blackout = False
        self._active_scene_id = None
        self._active_scene_name = None

        # Manual overrides: {fixture_id: {channel_offset: value}}
        self._overrides = {}

        # Haze: direct DMX ch8 (index 7) control, independent of scenes
        self._haze_on = False
        self._haze_channel = 7  # 0-based, DMX ch8

            # Software strobe: toggles RGB on/off per fixture at random intervals
        self._strobe_on = False
        self._strobe_speed = 128     # base speed (10-255), maps to flash rate
        self._strobe_timings = {}    # fixture_id -> {'period': float, 'offset': float}
        self._strobe_next_change = 0 # when to re-randomize periods

        # Fixture list (set by controller after luminair parse)
        self._fixtures = []
        # Scene list
        self._scenes = []

        # Per-channel master mask: True = scale by master, False = passthrough
        self._master_mask = [True] * DMX_CHANNELS

        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info('DMXEngine started (%d Hz)', ARTNET_REFRESH_HZ)

    def set_fixtures(self, fixtures):
        """Load fixture list and build the master-mask."""
        with self._lock:
            self._fixtures = list(fixtures)
            self._master_mask = [True] * DMX_CHANNELS
            for fix in self._fixtures:
                for offset, ch_name in enumerate(fix.profile.channels):
                    if ch_name not in INTENSITY_CHANNELS:
                        idx = fix.dmx_address - 1 + offset
                        if 0 <= idx < DMX_CHANNELS:
                            self._master_mask[idx] = False
            logger.info('Loaded %d fixtures, master mask built', len(self._fixtures))

    def set_scenes(self, scenes):
        with self._lock:
            self._scenes = list(scenes)
            logger.info('Loaded %d scenes', len(self._scenes))

    # ------------------------------------------------------------------
    # Scene recall
    # ------------------------------------------------------------------

    def recall_scene(self, scene_id, fade_time=None, master_fade_time=None):
        """Crossfade to a scene. Only modifies channels in the scene's mask."""
        with self._lock:
            scene = self._find_scene(scene_id)
            if scene is None:
                return False
            self._fade_start[:] = self._current[:]
            # Clear overrides for fixtures controlled by this scene
            for fix in self._fixtures:
                base = fix.dmx_address - 1
                if any((base + o) in scene.channel_mask for o in range(fix.channel_count)):
                    self._overrides.pop(fix.id, None)
            for ch in scene.channel_mask:
                self._target[ch] = scene.dmx_values[ch]
            self._apply_overrides()
            self._fade_start_time = time.monotonic()
            self._fade_duration = fade_time if fade_time is not None else scene.fade_in
            # If master is at zero, snap channels instantly — crossfade is
            # invisible anyway, and prevents the old scene flashing as master
            # fades up
            if self._master == 0:
                self._fade_duration = 0
            self._active_scene_id = scene.id
            self._active_scene_name = scene.name
            # Fade master back to full
            mft = master_fade_time if master_fade_time is not None else 0.0
            if mft > 0 and self._master < 255:
                self._master_fade_start = self._master
                self._master_fade_target = 255
                self._master_fade_start_time = time.monotonic()
                self._master_fade_duration = mft
            else:
                self._master = 255
                self._master_fade_target = 255
                self._master_fade_duration = 0.0
            logger.info('Recall scene %d "%s" (fade %.1fs, %d channels)',
                        scene.id, scene.name, self._fade_duration, len(scene.channel_mask))
            return True

    def _find_scene(self, scene_id):
        for s in self._scenes:
            if s.id == scene_id:
                return s
        return None

    # ------------------------------------------------------------------
    # Manual fixture control
    # ------------------------------------------------------------------

    def set_fixture_channels(self, fixture_id, values):
        """Set multiple channels on a fixture. values: {offset: value}."""
        with self._lock:
            fix = self._find_fixture(fixture_id)
            if fix is None:
                return False
            overrides = self._overrides.setdefault(fixture_id, {})
            for offset, value in values.items():
                idx = fix.dmx_address - 1 + offset
                if 0 <= idx < DMX_CHANNELS:
                    overrides[offset] = value
                    self._target[idx] = value
                    self._current[idx] = value
            return True

    def clear_fixture_override(self, fixture_id):
        """Remove overrides for a fixture, revert to active scene values."""
        with self._lock:
            self._overrides.pop(fixture_id, None)
            scene = self._find_scene(self._active_scene_id) if self._active_scene_id else None
            if scene:
                fix = self._find_fixture(fixture_id)
                if fix:
                    for offset in range(fix.channel_count):
                        idx = fix.dmx_address - 1 + offset
                        if 0 <= idx < DMX_CHANNELS:
                            self._target[idx] = scene.dmx_values[idx]
                            self._current[idx] = scene.dmx_values[idx]
            return True

    def _find_fixture(self, fixture_id):
        for f in self._fixtures:
            if f.id == fixture_id:
                return f
        return None

    def _apply_overrides(self):
        """Apply manual overrides on top of target buffer."""
        for fix_id, channels in self._overrides.items():
            fix = self._find_fixture(fix_id)
            if fix is None:
                continue
            for offset, value in channels.items():
                idx = fix.dmx_address - 1 + offset
                if 0 <= idx < DMX_CHANNELS:
                    self._target[idx] = value

    # ------------------------------------------------------------------
    # Master / blackout
    # ------------------------------------------------------------------

    def set_master(self, value):
        with self._lock:
            self._master = max(0, min(255, int(value)))
            self._master_fade_target = self._master
            self._master_fade_duration = 0.0

    def fade_master(self, target, duration):
        """Animate master fader from current to target over duration seconds."""
        with self._lock:
            self._master_fade_start = self._master
            self._master_fade_target = max(0, min(255, int(target)))
            self._master_fade_start_time = time.monotonic()
            self._master_fade_duration = max(0.0, float(duration))
            logger.info('Master fade %d → %d over %.1fs',
                        self._master_fade_start, self._master_fade_target, duration)

    def set_blackout(self, on):
        with self._lock:
            self._blackout = bool(on)
            logger.info('Blackout %s', 'ON' if on else 'OFF')

    # ------------------------------------------------------------------
    # Haze (direct ch8 control, independent of scenes)
    # ------------------------------------------------------------------

    def set_haze(self, on):
        with self._lock:
            self._haze_on = bool(on)
            logger.info('Haze %s', 'ON' if on else 'OFF')

    # ------------------------------------------------------------------
    # Strobe (random strobe on all SlimPars, independent of scenes)
    # ------------------------------------------------------------------

    STROBE_RERANDOMIZE_S = 2.0   # re-randomize periods every 2s

    def set_strobe(self, on):
        with self._lock:
            self._strobe_on = bool(on)
            if on:
                self._randomize_strobe()
                self._strobe_next_change = time.monotonic() + self.STROBE_RERANDOMIZE_S
            else:
                self._strobe_timings = {}
            logger.info('Strobe %s (speed=%d)', 'ON' if on else 'OFF', self._strobe_speed)

    def set_strobe_speed(self, speed):
        with self._lock:
            self._strobe_speed = max(10, min(255, int(speed)))
            if self._strobe_on:
                self._randomize_strobe()

    def _randomize_strobe(self):
        """Assign random flash periods and phase offsets to all SlimPar fixtures."""
        # Map speed 10-255 to period: 255=fast (~50ms), 10=slow (~500ms)
        base_period = 0.5 - (self._strobe_speed - 10) / 245 * 0.45  # 0.5s → 0.05s
        for fix in self._fixtures:
            if fix.group == 'slimpar':
                # ±30% random variation on period
                variation = base_period * 0.3
                period = base_period + random.uniform(-variation, variation)
                period = max(0.04, period)
                offset = random.uniform(0, period)  # random phase
                self._strobe_timings[fix.id] = {'period': period, 'offset': offset}

    # ------------------------------------------------------------------
    # 40Hz output loop
    # ------------------------------------------------------------------

    def _run(self):
        interval = 1.0 / ARTNET_REFRESH_HZ
        while self._running:
            start = time.monotonic()
            try:
                self._tick()
            except Exception:
                logger.exception('DMX tick error')
            elapsed = time.monotonic() - start
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _tick(self):
        with self._lock:
            # Interpolate fade
            if self._fade_duration > 0:
                elapsed = time.monotonic() - self._fade_start_time
                t = min(1.0, elapsed / self._fade_duration)
            else:
                t = 1.0

            for i in range(DMX_CHANNELS):
                self._current[i] = int(
                    self._fade_start[i] + (self._target[i] - self._fade_start[i]) * t
                )

            # Apply haze override (direct ch8 control)
            self._current[self._haze_channel] = 255 if self._haze_on else 0

            # Software strobe: toggle RGB on/off per fixture based on timing
            if self._strobe_on:
                now = time.monotonic()
                if now >= self._strobe_next_change:
                    self._randomize_strobe()
                    self._strobe_next_change = now + self.STROBE_RERANDOMIZE_S
                for fix in self._fixtures:
                    timing = self._strobe_timings.get(fix.id)
                    if not timing:
                        continue
                    # Determine on/off phase: 50% duty cycle
                    phase = ((now + timing['offset']) % timing['period']) / timing['period']
                    is_off = phase > 0.5
                    if is_off:
                        base = fix.dmx_address - 1
                        for offset, ch_name in enumerate(fix.profile.channels):
                            if ch_name in ('Red', 'Green', 'Blue', 'White', 'Intensity', 'Dim'):
                                idx = base + offset
                                if 0 <= idx < DMX_CHANNELS:
                                    self._current[idx] = 0

            # Interpolate master fade
            if self._master_fade_duration > 0:
                mf_elapsed = time.monotonic() - self._master_fade_start_time
                mf_t = min(1.0, mf_elapsed / self._master_fade_duration)
                self._master = int(self._master_fade_start +
                    (self._master_fade_target - self._master_fade_start) * mf_t)

            # Build output with master and blackout
            output = bytearray(DMX_CHANNELS)
            if not self._blackout:
                master_f = self._master / 255.0
                for i in range(DMX_CHANNELS):
                    if self._master_mask[i]:
                        output[i] = int(self._current[i] * master_f)
                    else:
                        output[i] = self._current[i]

            self._sender.send(output)

    # ------------------------------------------------------------------
    # State (for API polling)
    # ------------------------------------------------------------------

    def get_state(self):
        with self._lock:
            if self._fade_duration > 0:
                elapsed = time.monotonic() - self._fade_start_time
                fading = elapsed < self._fade_duration
                fade_progress = min(1.0, elapsed / self._fade_duration)
            else:
                fading = False
                fade_progress = 1.0

            return {
                'active_scene_id': self._active_scene_id,
                'active_scene_name': self._active_scene_name,
                'master': self._master,
                'blackout': self._blackout,
                'fading': fading,
                'fade_progress': fade_progress,
                'overrides': {k: dict(v) for k, v in self._overrides.items()},
                'master_fading': self._master_fade_duration > 0 and
                    (time.monotonic() - self._master_fade_start_time) < self._master_fade_duration,
                'haze': self._haze_on,
                'strobe': self._strobe_on,
                'strobe_speed': self._strobe_speed,
            }

    def get_fixture_levels(self):
        """Return per-fixture channel values from the current buffer."""
        with self._lock:
            result = {}
            for fix in self._fixtures:
                channels = {}
                base = fix.dmx_address - 1
                for offset, ch_name in enumerate(fix.profile.channels):
                    idx = base + offset
                    if 0 <= idx < DMX_CHANNELS:
                        channels[ch_name] = self._current[idx]
                result[fix.id] = channels
            return result

    def get_dmx_output(self):
        """Return current 512-byte output for debug."""
        with self._lock:
            output = bytearray(DMX_CHANNELS)
            if not self._blackout:
                master_f = self._master / 255.0
                for i in range(DMX_CHANNELS):
                    if self._master_mask[i]:
                        output[i] = int(self._current[i] * master_f)
                    else:
                        output[i] = self._current[i]
            return list(output)

    def stop(self):
        self._running = False
        self._thread.join(timeout=2)
