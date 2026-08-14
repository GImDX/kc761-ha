"""KC761 manual-v1.8 candidate codec.

The profile intentionally exposes ``HARDWARE_VERIFIED = False``.  It uses the
only implementation candidate currently documented for the RC: little-endian
integers/floats and IEEE-754 binary16 for FP16 fields.  Real captures must
confirm these choices before measurements become a stable release contract.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

from ..const import (
    CMD_GET_DEVICE_INFO,
    CMD_GET_STA,
    FLAG_CAL_DATA,
    FLAG_DEVICE_INFO,
    FLAG_MC_DATA_QUERY,
    FLAG_MC_DATA_UPLOAD,
    FLAG_STATUS_QUERY,
    FLAG_STATUS_UPLOAD,
    FLAG_STREAM_DATA,
    MODEL_NAMES,
    SENSOR_NAMES,
)
from .framing import frame_bytes
from .models import (
    ChannelReading,
    CumulativeChannel,
    DecodeError,
    DeviceInfo,
    RawFrame,
    StatusData,
    utc_from_epoch,
)

CODEC_PROFILE = "manual_v1_8_candidate"
HARDWARE_VERIFIED = False


@dataclass(frozen=True, slots=True)
class DecodedFrame:
    """Semantic classification for known and unknown flags."""

    frame: RawFrame
    kind: str
    data: StatusData | DeviceInfo | None = None


def encode_read_request(sync: int, command: int) -> bytes:
    """Encode one of the two RC read commands."""
    if command not in (CMD_GET_STA, CMD_GET_DEVICE_INFO):
        raise ValueError(f"command is not a supported read: {command:#x}")
    return bytes((0x00, command, sync & 0xFF, 0x00))


def decode_frame(frame: RawFrame) -> DecodedFrame:
    """Decode A2/A3/A5 semantically and classify every other bounded flag."""
    if frame.flag in (FLAG_STATUS_QUERY, FLAG_STATUS_UPLOAD):
        return DecodedFrame(frame, "status", decode_status(frame))
    if frame.flag == FLAG_DEVICE_INFO:
        return DecodedFrame(frame, "device_info", decode_device_info(frame))
    kind = {
        FLAG_MC_DATA_QUERY: "spectrum_query",
        FLAG_MC_DATA_UPLOAD: "spectrum_upload",
        FLAG_STREAM_DATA: "stream_upload",
        FLAG_CAL_DATA: "calibration",
    }.get(frame.flag, "known" if frame.known else "unknown")
    return DecodedFrame(frame, kind)


def decode_status(frame: RawFrame) -> StatusData:
    """Decode an 81-byte A2/A3 status frame."""
    if frame.flag not in (FLAG_STATUS_QUERY, FLAG_STATUS_UPLOAD):
        raise DecodeError(f"not a status flag: {frame.flag:#x}")
    if frame.bag_len != 81 or len(frame.payload) != 77:
        raise DecodeError(f"status length {frame.bag_len}, expected 81")
    body = frame.payload
    validation_errors: list[str] = []
    auto_upload_status = body[3]
    if auto_upload_status not in (0, 1):
        auto_upload_value: int | None = None
        validation_errors.append("auto_upload_status")
    else:
        auto_upload_value = auto_upload_status
    battery = body[4]
    if battery > 100:
        battery_value: int | None = None
        validation_errors.append("battery_percent")
    else:
        battery_value = battery
    air_pressure = struct.unpack_from("<H", body, 5)[0]
    temperature = struct.unpack_from("<h", body, 7)[0] / 10.0
    device_time = utc_from_epoch(struct.unpack_from("<I", body, 9)[0])
    channels: list[ChannelReading] = []
    offset = 29
    for _ in range(3):
        raw_cps = struct.unpack_from("<i", body, offset)[0]
        raw_dose = _decode_half(body, offset + 4)
        raw_eq = _decode_half(body, offset + 6)
        avg_cps = _decode_float(body, offset + 8)
        avg_dose = _decode_half(body, offset + 12)
        avg_eq = _decode_half(body, offset + 14)
        channels.append(
            ChannelReading(
                raw_cps=None if raw_cps == -1 else raw_cps,
                raw_dose_rate=None if raw_dose == -1 else raw_dose,
                raw_dose_equivalent_rate=None if raw_eq == -1 else raw_eq,
                avg_cps=None if avg_cps == -1 else avg_cps,
                avg_dose_rate=None if avg_dose == -1 else avg_dose,
                avg_dose_equivalent_rate=None if avg_eq == -1 else avg_eq,
            )
        )
        offset += 16
    return StatusData(
        sensor_status=body[0],
        volume_status=body[1],
        led_screen_status=body[2],
        auto_upload_status=auto_upload_value,
        battery_percent=battery_value,
        air_pressure_hpa=air_pressure,
        device_temperature_c=temperature,
        device_time=device_time,
        channels=(channels[0], channels[1], channels[2]),
        source_flag=frame.flag,
        raw=frame.raw,
        validation_errors=tuple(validation_errors),
    )


def decode_device_info(frame: RawFrame) -> DeviceInfo:
    """Decode the candidate 100-byte A5 layout.

    The byte map is intentionally isolated here.  It is the manual-v1.8
    candidate used by fixtures; ``hardware_verified`` remains false.
    """
    if frame.flag != FLAG_DEVICE_INFO:
        raise DecodeError(f"not a device-info flag: {frame.flag:#x}")
    if frame.bag_len != 100 or len(frame.payload) != 96:
        raise DecodeError(f"device info length {frame.bag_len}, expected 100")
    body = frame.payload
    model = body[0]
    hw_version = body[1]
    firmware_version = body[2]
    co_firmware_version = body[3]
    sensor_types = (body[4], body[5], body[6])
    device_id = _decode_device_id(body[32:48])
    cumulative: list[CumulativeChannel] = []
    offset = 48
    for _ in range(3):
        spectrum_runtime = struct.unpack_from("<I", body, offset)[0]
        dose_runtime = struct.unpack_from("<I", body, offset + 4)[0]
        cumulative_dose = _decode_float(body, offset + 8)
        cumulative_eq = _decode_float(body, offset + 12)
        cumulative.append(
            CumulativeChannel(
                spectrum_runtime=spectrum_runtime,
                dose_runtime=dose_runtime,
                cumulative_dose=cumulative_dose,
                cumulative_dose_equivalent=cumulative_eq,
            )
        )
        offset += 16
    return DeviceInfo(
        device_model=model,
        hardware_version=hw_version,
        firmware_version=firmware_version,
        coprocessor_firmware_version=co_firmware_version,
        sensor_types=sensor_types,
        device_id=device_id,
        cumulative=(cumulative[0], cumulative[1], cumulative[2]),
        source_flag=frame.flag,
        raw=frame.raw,
        hardware_verified=HARDWARE_VERIFIED,
    )


def _decode_device_id(value: bytes) -> str:
    value = value.split(b"\x00", 1)[0].strip()
    if not value:
        raise DecodeError("empty DEVICE_ID")
    try:
        text = value.decode("ascii")
    except UnicodeDecodeError as err:
        raise DecodeError("DEVICE_ID is not ASCII") from err
    if not text.isprintable():
        raise DecodeError("DEVICE_ID contains control characters")
    return text


def _decode_half(data: bytes, offset: int) -> float:
    value = struct.unpack_from("<e", data, offset)[0]
    return _finite(value, "FP16")


def _decode_float(data: bytes, offset: int) -> float:
    value = struct.unpack_from("<f", data, offset)[0]
    return _finite(value, "FP32")


def _finite(value: float, label: str) -> float:
    if not math.isfinite(value):
        raise DecodeError(f"{label} is not finite")
    return value


def model_name(model: int) -> str:
    return MODEL_NAMES.get(model, f"Unknown ({model})")


def sensor_name(sensor: int) -> str:
    return SENSOR_NAMES.get(sensor, f"Unknown ({sensor:#x})")


def build_status_frame(sync: int, *, upload: bool = False, **kwargs: object) -> bytes:
    """Build a deterministic candidate A2/A3 fixture frame."""
    body = bytearray(77)
    body[0] = int(kwargs.get("sensor_status", 0x00))
    body[1] = int(kwargs.get("volume_status", 0x00))
    body[2] = int(kwargs.get("led_screen_status", 0x02))
    body[3] = int(kwargs.get("auto_upload_status", 0))
    body[4] = int(kwargs.get("battery_percent", 87))
    struct.pack_into("<H", body, 5, int(kwargs.get("air_pressure_hpa", 1013)))
    struct.pack_into("<h", body, 7, int(round(float(kwargs.get("device_temperature_tenths", 235)))))
    struct.pack_into("<I", body, 9, int(kwargs.get("device_time", 1_735_689_600)))
    channel_values = kwargs.get("channels")
    if channel_values is None:
        channel_values = ((100, 0.01, 0.02, 100.0, 0.01, 0.02),) * 3
    offset = 29
    for values in channel_values:  # type: ignore[union-attr]
        raw_cps, raw_dose, raw_eq, avg_cps, avg_dose, avg_eq = values
        struct.pack_into("<i", body, offset, int(raw_cps))
        struct.pack_into("<e", body, offset + 4, float(raw_dose))
        struct.pack_into("<e", body, offset + 6, float(raw_eq))
        struct.pack_into("<f", body, offset + 8, float(avg_cps))
        struct.pack_into("<e", body, offset + 12, float(avg_dose))
        struct.pack_into("<e", body, offset + 14, float(avg_eq))
        offset += 16
    flag = FLAG_STATUS_UPLOAD if upload else FLAG_STATUS_QUERY
    return frame_bytes(sync, flag, bytes(body))


def build_device_info_frame(
    sync: int,
    *,
    model: int = 12,
    hardware_version: int = 12,
    firmware_version: int = 180,
    coprocessor_firmware_version: int = 180,
    sensor_types: tuple[int, int, int] = (2, 0, 0),
    device_id: str = "7601-0000-000001",
) -> bytes:
    """Build a deterministic candidate A5 fixture frame."""
    body = bytearray(96)
    body[0] = model
    body[1] = hardware_version
    body[2] = firmware_version
    body[3] = coprocessor_firmware_version
    body[4:7] = bytes(sensor_types)
    encoded_id = device_id.encode("ascii")
    if len(encoded_id) > 16:
        raise ValueError("device_id must fit in 16 bytes")
    body[32 : 32 + len(encoded_id)] = encoded_id
    offset = 48
    for index in range(3):
        struct.pack_into("<I", body, offset, 3600 * (index + 1))
        struct.pack_into("<I", body, offset + 4, 7200 * (index + 1))
        struct.pack_into("<f", body, offset + 8, 1.0 * (index + 1))
        struct.pack_into("<f", body, offset + 12, 2.0 * (index + 1))
        offset += 16
    return frame_bytes(sync, FLAG_DEVICE_INFO, bytes(body))
