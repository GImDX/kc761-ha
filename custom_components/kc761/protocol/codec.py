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
    CMD_GET_MC_DATA,
    CMD_SET_CLEAR,
    CMD_SET_DEVICE_TIME,
    CMD_SET_STA,
    FLAG_ACK,
    FLAG_CAL_DATA,
    FLAG_DEVICE_INFO,
    FLAG_MC_DATA_QUERY,
    FLAG_MC_DATA_UPLOAD,
    FLAG_STATUS_QUERY,
    FLAG_STATUS_UPLOAD,
    FLAG_STREAM_DATA,
    MODEL_NAMES,
    READ_COMMANDS,
    SENSOR_NAMES,
    WRITE_COMMANDS,
)
from .framing import frame_bytes
from .models import (
    CalibrationData,
    ChannelReading,
    CommandAck,
    CumulativeChannel,
    DecodeError,
    DeviceInfo,
    RawFrame,
    SpectrumSegment,
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
    data: StatusData | DeviceInfo | SpectrumSegment | CalibrationData | CommandAck | None = None


def encode_read_request(sync: int, command: int, *, source: int | None = None) -> bytes:
    """Encode one supported read command."""
    if command not in READ_COMMANDS:
        raise ValueError(f"command is not a supported read: {command:#x}")
    if command == CMD_GET_MC_DATA:
        if source is None or not 0 <= source <= 2:
            raise ValueError("spectrum source must be in 0..2")
        return bytes((0x00, command, sync & 0xFF, source, 0x00))
    if source is not None:
        raise ValueError("source is only valid for spectrum reads")
    return bytes((0x00, command, sync & 0xFF, 0x00))


def encode_set_state_request(
    sync: int,
    *,
    sensor_status: int = 0xFF,
    volume_status: int = 0xFF,
    led_screen_status: int = 0xFF,
    auto_upload_status: int = 0xFF,
) -> bytes:
    """Encode a masked 0x62 state update; 0xFF preserves a field."""
    values = (sensor_status, volume_status, led_screen_status, auto_upload_status)
    if any(not 0 <= value <= 0xFF for value in values):
        raise ValueError("state byte outside uint8")
    if auto_upload_status not in (0, 1, 0xFF):
        raise ValueError("automatic upload must be 0, 1, or 0xff")
    return bytes((0x00, CMD_SET_STA, sync & 0xFF, *values, 0x00))


def encode_set_device_time_request(sync: int, epoch: int) -> bytes:
    """Encode a 0x63 UNIX time update."""
    if not 0 <= epoch <= 0xFFFFFFFF:
        raise ValueError("device time outside uint32")
    return bytes((0x00, CMD_SET_DEVICE_TIME, sync & 0xFF)) + struct.pack("<I", epoch) + b"\x00"


def encode_clear_request(
    sync: int,
    *,
    spectrum_mask: int = 0,
    dose_curve_mask: int = 0,
    cumulative_dose_mask: int = 0,
) -> bytes:
    """Encode a 0x66 per-channel clear request."""
    masks = (spectrum_mask, dose_curve_mask, cumulative_dose_mask)
    if not any(masks):
        raise ValueError("at least one clear mask is required")
    if any(mask < 0 or mask > 0x07 for mask in masks):
        raise ValueError("clear masks must use only channel bits 0..2")
    return bytes((0x00, CMD_SET_CLEAR, sync & 0xFF, *masks, 0x00))


def validate_write_request(request: bytes) -> int:
    """Return the command after validating a complete supported write frame."""
    if len(request) < 3 or request[0] != 0:
        raise ValueError("invalid KC761 request envelope")
    command = request[1]
    if command not in WRITE_COMMANDS:
        raise ValueError(f"command is not a supported write: {command:#x}")
    expected_length = {CMD_SET_STA: 8, CMD_SET_DEVICE_TIME: 8, CMD_SET_CLEAR: 7}[command]
    if len(request) != expected_length or request[-1] != 0:
        raise ValueError(f"invalid request length for {command:#x}")
    return command


def decode_frame(frame: RawFrame) -> DecodedFrame:
    """Decode all supported read responses and command acknowledgements."""
    if frame.flag in (FLAG_MC_DATA_QUERY, FLAG_MC_DATA_UPLOAD):
        return DecodedFrame(frame, "spectrum", decode_spectrum_segment(frame))
    if frame.flag in (FLAG_STATUS_QUERY, FLAG_STATUS_UPLOAD):
        return DecodedFrame(frame, "status", decode_status(frame))
    if frame.flag == FLAG_DEVICE_INFO:
        return DecodedFrame(frame, "device_info", decode_device_info(frame))
    if frame.flag == FLAG_CAL_DATA:
        return DecodedFrame(frame, "calibration", decode_calibration(frame))
    if frame.flag == FLAG_ACK:
        return DecodedFrame(frame, "ack", decode_ack(frame))
    kind = {
        FLAG_STREAM_DATA: "stream_upload",
    }.get(frame.flag, "known" if frame.known else "unknown")
    return DecodedFrame(frame, kind)


def decode_spectrum_segment(frame: RawFrame) -> SpectrumSegment:
    """Decode one variable-length A0/A1 spectrum fragment."""
    if frame.flag not in (FLAG_MC_DATA_QUERY, FLAG_MC_DATA_UPLOAD):
        raise DecodeError(f"not a spectrum flag: {frame.flag:#x}")
    if frame.bag_len < 9 or len(frame.payload) != frame.bag_len - 4:
        raise DecodeError("invalid spectrum frame length")
    body = frame.payload
    source = body[0]
    offset = struct.unpack_from("<H", body, 1)[0]
    ratio = struct.unpack_from("<H", body, 3)[0]
    if source > 2:
        raise DecodeError(f"invalid spectrum source: {source}")
    if ratio < 1:
        raise DecodeError("spectrum ratio must be at least one")
    relative = struct.unpack_from(f"<{(len(body) - 5) // 2}H", body, 5)
    counts = tuple(value * ratio for value in relative)
    return SpectrumSegment(source, offset, ratio, counts, frame.flag)


def decode_calibration(frame: RawFrame) -> CalibrationData:
    """Decode the 150-byte A6 calibration frame used for spectrum energy labels."""
    if frame.flag != FLAG_CAL_DATA:
        raise DecodeError(f"not a calibration flag: {frame.flag:#x}")
    if frame.bag_len != 150 or len(frame.payload) != 146:
        raise DecodeError(f"calibration length {frame.bag_len}, expected 150")
    body = frame.payload
    calibration_select = body[1]
    if calibration_select not in (0, 1):
        raise DecodeError(f"invalid energy calibration selection: {calibration_select}")
    def floats(offset: int, count: int = 1) -> tuple[float, ...]:
        return tuple(_decode_float(body, offset + index * 4) for index in range(count))

    zoom = (floats(2)[0], floats(10)[0], floats(18)[0])
    offset = (floats(6)[0], floats(14)[0], floats(22)[0])
    return CalibrationData(
        factory_version=body[0],
        energy_calibration_select=calibration_select,
        energy_zoom=zoom,
        energy_offset=offset,
        custom_energy_calibration=floats(46, 4),
        channel1_factory_calibration=floats(62, 4),
        channel2_factory_calibration=floats(78, 4),
        channel0_factory_low=floats(94, 4),
        channel0_factory_middle=floats(110, 4),
        channel0_factory_high=floats(126, 4),
        channel0_factory_node1=struct.unpack_from("<H", body, 142)[0],
        channel0_factory_node2=struct.unpack_from("<H", body, 144)[0],
        source_flag=frame.flag,
    )


def decode_ack(frame: RawFrame) -> CommandAck:
    """Decode and validate one six-byte AA acknowledgement."""
    if frame.flag != FLAG_ACK:
        raise DecodeError(f"not an ACK flag: {frame.flag:#x}")
    if frame.bag_len != 6 or len(frame.payload) != 2:
        raise DecodeError(f"ACK length {frame.bag_len}, expected 6")
    status, command = frame.payload
    if status not in (0, 1):
        raise DecodeError(f"invalid ACK status: {status}")
    if command not in WRITE_COMMANDS and command != 0x64:
        raise DecodeError(f"invalid ACK command: {command:#x}")
    return CommandAck(status, command, frame.flag)


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
    # Real KC761 traffic uses bit 7 as a status flag.  The official client
    # displays the remaining seven bits as the battery percentage (for
    # example, 0x80 is displayed as 0%).
    battery = body[4] & 0x7F
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
    value = value.rstrip(b"\x00 ")
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


def build_spectrum_frame(
    sync: int,
    *,
    source: int,
    offset: int,
    counts: tuple[int, ...],
    ratio: int = 1,
    upload: bool = False,
) -> bytes:
    """Build one deterministic A0/A1 fixture fragment."""
    if not 0 <= source <= 2 or not 0 <= offset <= 0xFFFF or not 1 <= ratio <= 0xFFFF:
        raise ValueError("invalid spectrum fixture metadata")
    if any(count < 0 or count % ratio or count // ratio > 0xFFFF for count in counts):
        raise ValueError("spectrum counts cannot be represented by ratio")
    body = bytes((source,)) + struct.pack("<HH", offset, ratio)
    body += struct.pack(f"<{len(counts)}H", *(count // ratio for count in counts))
    return frame_bytes(sync, FLAG_MC_DATA_UPLOAD if upload else FLAG_MC_DATA_QUERY, body)


def build_calibration_frame(sync: int) -> bytes:
    """Build a deterministic A6 fixture with identity energy calibration."""
    body = bytearray(146)
    body[0] = 2
    body[1] = 0
    for offset in (2, 10, 18):
        struct.pack_into("<f", body, offset, 1.0)
    identity = (0.0, 0.0, 1.0, 0.0)
    for offset in (46, 62, 78, 94, 110, 126):
        struct.pack_into("<4f", body, offset, *identity)
    struct.pack_into("<HH", body, 142, 512, 1536)
    return frame_bytes(sync, FLAG_CAL_DATA, bytes(body))


def build_ack_frame(sync: int, command: int, *, status: int = 0) -> bytes:
    """Build a deterministic six-byte AA fixture."""
    if status not in (0, 1) or command not in WRITE_COMMANDS | {0x64}:
        raise ValueError("invalid ACK fixture")
    return frame_bytes(sync, FLAG_ACK, bytes((status, command)))
