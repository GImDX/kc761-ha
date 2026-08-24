"""Protocol value objects and parse errors."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


class ProtocolError(ValueError):
    """Base class for malformed or unsupported protocol data."""


class FrameError(ProtocolError):
    """A frame cannot be split from the TCP stream."""


class DecodeError(ProtocolError):
    """A frame has a valid envelope but invalid semantic values."""


class TransactionError(ProtocolError):
    """A response cannot be matched to a pending request."""


@dataclass(frozen=True, slots=True)
class RawFrame:
    """One complete frame, including the original bytes for diagnostics/tests."""

    sync: int
    flag: int
    bag_len: int
    payload: bytes
    raw: bytes
    known: bool = True


@dataclass(frozen=True, slots=True)
class ChannelReading:
    """One channel's state values in the manual's candidate units."""

    raw_cps: int | None
    raw_dose_rate: float | None
    raw_dose_equivalent_rate: float | None
    avg_cps: float | None
    avg_dose_rate: float | None
    avg_dose_equivalent_rate: float | None


@dataclass(frozen=True, slots=True)
class StatusData:
    """Decoded A2/A3 status data."""

    sensor_status: int
    volume_status: int
    led_screen_status: int
    auto_upload_status: int | None
    battery_percent: int | None
    air_pressure_hpa: int | None
    device_temperature_c: float | None
    device_time: datetime | None
    channels: tuple[ChannelReading, ChannelReading, ChannelReading]
    source_flag: int
    raw: bytes = field(repr=False)
    validation_errors: tuple[str, ...] = ()

    @property
    def active_sensor(self) -> int:
        """Return the two-bit selected sensor value."""
        return self.sensor_status & 0x03

    @property
    def spectrum_accumulating(self) -> tuple[bool, bool, bool]:
        """Return the three documented accumulation bits."""
        return (
            bool(self.sensor_status & 0x04),
            bool(self.sensor_status & 0x08),
            bool(self.sensor_status & 0x10),
        )


@dataclass(frozen=True, slots=True)
class CumulativeChannel:
    """One channel's resettable cumulative values from the A5 response."""

    spectrum_runtime: int | None
    dose_runtime: int | None
    cumulative_dose: float | None
    cumulative_dose_equivalent: float | None


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Decoded A5 device information using the manual-v1.8 candidate layout."""

    device_model: int
    hardware_version: int
    firmware_version: int
    coprocessor_firmware_version: int
    sensor_types: tuple[int, int, int]
    device_id: str
    cumulative: tuple[CumulativeChannel, CumulativeChannel, CumulativeChannel]
    source_flag: int
    raw: bytes = field(repr=False)
    hardware_verified: bool = False

    @property
    def model_name(self) -> str:
        from ..const import MODEL_NAMES

        return MODEL_NAMES.get(self.device_model, f"Unknown ({self.device_model})")

    @property
    def hw_version_text(self) -> str:
        return f"V{self.hardware_version // 10}.{self.hardware_version % 10}"

    @property
    def fw_version_text(self) -> str:
        return format_fw_version(self.firmware_version)

    @property
    def co_fw_version_text(self) -> str:
        return format_co_fw_version(self.coprocessor_firmware_version)


@dataclass(frozen=True, slots=True)
class SpectrumSegment:
    """One A0/A1 spectrum fragment."""

    source: int
    offset: int
    ratio: int
    counts: tuple[int, ...]
    source_flag: int


@dataclass(frozen=True, slots=True)
class SpectrumData:
    """One complete spectrum reconstructed from bounded A0/A1 fragments."""

    source: int
    counts: tuple[int, ...]
    segment_count: int
    source_flag: int


@dataclass(frozen=True, slots=True)
class CalibrationData:
    """Read-only A6 energy calibration data required to label spectra."""

    factory_version: int
    energy_calibration_select: int
    energy_zoom: tuple[float, float, float]
    energy_offset: tuple[float, float, float]
    custom_energy_calibration: tuple[float, float, float, float]
    channel1_factory_calibration: tuple[float, float, float, float]
    channel2_factory_calibration: tuple[float, float, float, float]
    channel0_factory_low: tuple[float, float, float, float]
    channel0_factory_middle: tuple[float, float, float, float]
    channel0_factory_high: tuple[float, float, float, float]
    channel0_factory_node1: int
    channel0_factory_node2: int
    rad0_trigger_offset: int
    rad2_trigger_offset: int
    rad0_dose_zoom: float
    rad1_dose_zoom: float
    rad2_dose_zoom: float
    neu_window_center: int
    altitude_m_offset: int
    source_flag: int

    def energy_kev(self, source: int, channel: int) -> float:
        """Return the calibrated energy for one channel address."""
        if not 0 <= source <= 2:
            raise ValueError("spectrum source outside 0..2")
        if channel < 0:
            raise ValueError("channel address must be non-negative")
        if source == 0:
            if self.energy_calibration_select == 1:
                coefficients = self.custom_energy_calibration
            elif self.factory_version == 2:
                if channel < self.channel0_factory_node1:
                    coefficients = self.channel0_factory_low
                elif channel <= self.channel0_factory_node2:
                    coefficients = self.channel0_factory_middle
                else:
                    coefficients = self.channel0_factory_high
            else:
                coefficients = self.channel0_factory_middle
        elif source == 1:
            coefficients = self.channel1_factory_calibration
        else:
            coefficients = self.channel2_factory_calibration
        a, b, c, d = coefficients
        unscaled = ((a * channel + b) * channel + c) * channel + d
        return unscaled * self.energy_zoom[source] + self.energy_offset[source]


@dataclass(frozen=True, slots=True)
class CommandAck:
    """One AA response to a state, time, calibration, or clear command."""

    status: int
    command: int
    source_flag: int

    @property
    def successful(self) -> bool:
        return self.status == 0


def format_fw_version(value: int) -> str:
    """Format a V1.80-style integer without dropping trailing zeroes."""
    return f"V{value // 100}.{value % 100:02d}"


def format_co_fw_version(value: int) -> str:
    """Format the observed coprocessor version encoding.

    The real KC761 capture reported ``CO_FW_VER=11`` while the official
    upper-computer screen displayed ``v1.1``.  This differs from the manual's
    shared FW/CO ``/100`` example.  Preserve the manual format for the
    three-digit values used by the fixture and only apply the observed
    shortened form to two-digit values until another firmware family confirms
    the general rule.
    """
    if 10 <= value < 100:
        return f"V{value // 10}.{value % 10}"
    return format_fw_version(value)


def redacted_device_id_suffix(value: str) -> str:
    """Return only a safe tail, without exposing an entire short DEVICE_ID."""
    if len(value) <= 4:
        return f"<{len(value)} characters>"
    return value[-4:]


@dataclass(frozen=True, slots=True)
class PendingResponse:
    """Bookkeeping for one client request."""

    sync: int
    expected_flags: frozenset[int]
    created_at: float


def utc_from_epoch(value: int) -> datetime | None:
    """Convert a UNIX timestamp conservatively."""
    try:
        result = datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
    if result.year < 2000 or result.year > 2200:
        return None
    return result


def as_public_dict(value: Any) -> dict[str, Any]:
    """Return a small JSON-safe summary for diagnostics."""
    if isinstance(value, RawFrame):
        return {"sync": value.sync, "flag": value.flag, "bag_len": value.bag_len}
    if isinstance(value, StatusData):
        return {
            "source_flag": value.source_flag,
            "battery_percent": value.battery_percent,
            "air_pressure_hpa": value.air_pressure_hpa,
            "device_temperature_c": value.device_temperature_c,
        }
    if isinstance(value, DeviceInfo):
        return {
            "device_model": value.device_model,
            "model_name": value.model_name,
            "hardware_version": value.hw_version_text,
            "firmware_version": value.fw_version_text,
            "coprocessor_firmware_version": value.co_fw_version_text,
            "device_id_suffix": redacted_device_id_suffix(value.device_id),
            "hardware_verified": value.hardware_verified,
        }
    return {"type": type(value).__name__}
