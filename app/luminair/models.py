from dataclasses import dataclass, field
from typing import List


@dataclass
class ChannelProfile:
    channels: List[str]  # e.g. ['Red', 'Green', 'Blue', 'Macro', 'Strobe', 'Mode', 'Intensity']


# Known fixture profiles
SLIMPAR_PROFILE = ChannelProfile(
    channels=['Red', 'Green', 'Blue', 'Macro', 'Strobe', 'Mode', 'Intensity']
)

RETRO_BLINDER_PROFILE = ChannelProfile(
    channels=['Dim', 'Strobe', 'Red', 'Green', 'Blue', 'White', 'Mode', 'Speed']
)

DUOBLIND_PROFILE = ChannelProfile(
    channels=['Intensity']
)

HAZE_PROFILE = ChannelProfile(
    channels=['Level']
)


@dataclass
class Fixture:
    id: int
    name: str
    model: str
    manufacturer: str
    dmx_address: int       # 1-based
    channel_count: int
    profile: ChannelProfile
    group: str             # 'slimpar', 'triangle', 'droid', 'haze'


@dataclass
class Scene:
    id: int
    name: str
    dmx_values: bytes      # 512 bytes
    channel_mask: set       # set of channel indices (0-based) this scene controls
    fade_in: float         # seconds
    fade_out: float        # seconds
    button_color: str      # CSS rgba() or hex
    locked: bool
    master_level: float    # 0.0-1.0
