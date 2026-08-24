"""Pure-Python KC761 protocol implementation.

The codec profile is deliberately labelled as a manual candidate.  No field is
reported as hardware verified until real-device fixtures are added.
"""

from .codec import CODEC_PROFILE, HARDWARE_VERIFIED
from .client import SpectrumUploadAssembler
from .framing import FrameParser
from .models import (
    CalibrationData,
    ChannelReading,
    CommandAck,
    DeviceInfo,
    ProtocolError,
    RawFrame,
    SpectrumData,
    SpectrumSegment,
    StatusData,
)

__all__ = [
    "CODEC_PROFILE",
    "HARDWARE_VERIFIED",
    "CalibrationData",
    "ChannelReading",
    "CommandAck",
    "DeviceInfo",
    "FrameParser",
    "ProtocolError",
    "RawFrame",
    "SpectrumData",
    "SpectrumSegment",
    "SpectrumUploadAssembler",
    "StatusData",
]
