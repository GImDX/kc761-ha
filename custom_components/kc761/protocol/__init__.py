"""Pure-Python KC761 protocol implementation.

The codec profile is deliberately labelled as a manual candidate.  No field is
reported as hardware verified until real-device fixtures are added.
"""

from .codec import CODEC_PROFILE, HARDWARE_VERIFIED
from .framing import FrameParser
from .models import (
    ChannelReading,
    DeviceInfo,
    ProtocolError,
    RawFrame,
    StatusData,
)

__all__ = [
    "CODEC_PROFILE",
    "HARDWARE_VERIFIED",
    "ChannelReading",
    "DeviceInfo",
    "FrameParser",
    "ProtocolError",
    "RawFrame",
    "StatusData",
]
