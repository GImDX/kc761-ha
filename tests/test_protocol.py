"""Offline protocol and simulator tests."""

from __future__ import annotations

import asyncio
import math
import struct
import unittest
from unittest.mock import AsyncMock, MagicMock
from unittest.mock import patch

from custom_components.kc761.const import (
    CMD_GET_CAL_DATA,
    CMD_GET_DEVICE_INFO,
    CMD_GET_MC_DATA,
    CMD_GET_STA,
    CMD_SET_CLEAR,
    CMD_SET_DEVICE_TIME,
    CMD_SET_STA,
    FLAG_ACK,
    FLAG_CAL_DATA,
    FLAG_MC_DATA_UPLOAD,
    FLAG_MC_DATA_QUERY,
    FLAG_STATUS_QUERY,
    MAX_CONSECUTIVE_PROTOCOL_ERRORS,
)
from custom_components.kc761.protocol import client as client_module
from custom_components.kc761.protocol.client import KC761Client, PendingTransaction, SpectrumUploadAssembler
from custom_components.kc761.protocol.codec import (
    DecodedFrame,
    HARDWARE_VERIFIED,
    build_ack_frame,
    build_calibration_frame,
    build_device_info_frame,
    build_spectrum_frame,
    build_status_frame,
    decode_ack,
    decode_calibration,
    decode_device_info,
    decode_frame,
    decode_spectrum_segment,
    decode_status,
    encode_clear_request,
    encode_read_request,
    encode_set_device_time_request,
    encode_set_state_request,
    model_name,
    sensor_name,
    validate_write_request,
)
from custom_components.kc761.protocol.framing import FrameParser, frame_bytes
from custom_components.kc761.protocol.models import (
    CumulativeChannel,
    DecodeError,
    RawFrame,
    SpectrumData,
    SpectrumSegment,
    TransactionError,
    as_public_dict,
    format_co_fw_version,
    format_fw_version,
    redacted_device_id_suffix,
    utc_from_epoch,
)
from custom_components.kc761.protocol.transport import TcpTransport, TransportError
from tests.simulator import Kc761Simulator, SimulatorScenario, unknown_frame


MANUAL_DEVICE_INFO_FRAME = (
    b"\x05\xa5\x64\x00"
    b"\x0c\x0c\xb4\xb4\x02\x00\x00"
    + b"\x00" * 25
    + b"7601-0000-000001"
    + bytes.fromhex(
        "10 0e 00 00 20 1c 00 00 00 00 80 3f 00 00 00 40 "
        "20 1c 00 00 40 38 00 00 00 00 00 40 00 00 80 40 "
        "30 2a 00 00 60 54 00 00 00 00 40 40 00 00 c0 40"
    )
)


class FramingTests(unittest.TestCase):
    def test_fragmented_fixed_frame(self) -> None:
        parser = FrameParser()
        raw = build_status_frame(7)
        frames = []
        for byte in raw:
            frames.extend(parser.feed(bytes((byte,))))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].sync, 7)
        self.assertEqual(frames[0].flag, FLAG_STATUS_QUERY)
        self.assertEqual(frames[0].bag_len, 81)

    def test_coalesced_frames_and_unknown_flag(self) -> None:
        parser = FrameParser()
        first = build_status_frame(1)
        second = unknown_frame(2)
        frames = parser.feed(first + second)
        self.assertEqual([frame.sync for frame in frames], [1, 2])
        self.assertTrue(frames[0].known)
        self.assertFalse(frames[1].known)

    def test_bad_length_resynchronizes_and_bounds_buffer(self) -> None:
        parser = FrameParser(max_frame_length=128, max_buffer_length=32)
        parser.feed(b"\x99\x88\xff\xff" + build_status_frame(3))
        self.assertGreater(parser.errors, 0)
        self.assertLessEqual(parser.buffered_bytes, 32)

    def test_variable_lengths_are_bounded(self) -> None:
        parser = FrameParser()
        valid = frame_bytes(1, 0xA0, b"\x00" * 5)
        self.assertEqual(parser.feed(valid)[0].bag_len, 9)
        invalid = frame_bytes(2, 0xA0, b"\x00")
        self.assertEqual(parser.feed(invalid), [])
        self.assertGreater(parser.errors, 0)

    def test_fixed_and_stream_invalid_lengths_resynchronize(self) -> None:
        short_parser = FrameParser()
        short_parser.feed(b"\x01\xfe\x02\x00")
        self.assertGreater(short_parser.errors, 0)

        parser = FrameParser()
        self.assertEqual(parser.feed(frame_bytes(1, FLAG_STATUS_QUERY, b"x")), [])
        self.assertEqual(parser.feed(frame_bytes(2, 0xA4, b"x")), [])

        valid_parser = FrameParser()
        valid_stream = valid_parser.feed(frame_bytes(3, 0xA4, b"\x00\x01"))
        self.assertEqual(valid_stream[0].sync, 3)
        self.assertTrue(valid_stream[0].known)
        parser.feed(b"\x01\x02")
        parser.flush()
        self.assertGreaterEqual(parser.errors, 2)
        parser.feed(b"")
        parser._resync("empty")

    def test_malformed_fixed_length_recovers_following_frame(self) -> None:
        parser = FrameParser()
        malformed = frame_bytes(1, FLAG_STATUS_QUERY, b"x")
        frames = parser.feed(malformed + build_status_frame(3))
        self.assertEqual([frame.sync for frame in frames], [3])
        self.assertEqual(parser.buffered_bytes, 0)
        self.assertGreaterEqual(parser.errors, 1)

    def test_frame_builder_rejects_out_of_range_values(self) -> None:
        with self.assertRaises(ValueError):
            frame_bytes(256, 1)
        with self.assertRaises(ValueError):
            frame_bytes(1, 256)
        with self.assertRaises(ValueError):
            frame_bytes(1, 1, b"x" * 65532)


class CodecTests(unittest.TestCase):
    def test_candidate_profile_is_not_hardware_verified(self) -> None:
        self.assertFalse(HARDWARE_VERIFIED)

    def test_status_semantics_and_sentinel(self) -> None:
        raw = build_status_frame(
            4,
            sensor_status=0x15,
            battery_percent=75,
            air_pressure_hpa=1001,
            device_temperature_tenths=-35,
            channels=((-1, -1, -1, -1, -1, -1),) * 3,
        )
        frame = FrameParser().feed(raw)[0]
        data = decode_status(frame)
        self.assertEqual(data.active_sensor, 1)
        self.assertEqual(data.spectrum_accumulating, (True, False, True))
        self.assertEqual(data.battery_percent, 75)
        self.assertAlmostEqual(data.device_temperature_c or 0, -3.5)
        self.assertIsNone(data.channels[0].raw_cps)
        self.assertIsNone(data.channels[0].avg_dose_rate)

    def test_invalid_scalar_status_values_are_unknown_and_counted(self) -> None:
        raw = build_status_frame(1, battery_percent=101, auto_upload_status=7)
        data = decode_status(FrameParser().feed(raw)[0])
        self.assertIsNone(data.battery_percent)
        self.assertIsNone(data.auto_upload_status)
        self.assertEqual(
            data.validation_errors,
            ("auto_upload_status", "battery_percent"),
        )

        async def route() -> None:
            client = KC761Client("127.0.0.1", 1)
            await client._route_frame(FrameParser().feed(raw)[0])
            self.assertEqual(client.data_errors, 2)

        asyncio.run(route())

    def test_battery_percentage_uses_low_seven_bits(self) -> None:
        empty = ((-1, -1, -1, -1, -1, -1),) * 3

        flagged_empty = decode_status(
            FrameParser().feed(
                build_status_frame(1, battery_percent=0x80, channels=empty)
            )[0]
        )
        flagged_full = decode_status(
            FrameParser().feed(
                build_status_frame(2, battery_percent=0xE4, channels=empty)
            )[0]
        )
        flagged_invalid = decode_status(
            FrameParser().feed(
                build_status_frame(3, battery_percent=0xE5, channels=empty)
            )[0]
        )

        self.assertEqual(flagged_empty.battery_percent, 0)
        self.assertNotIn("battery_percent", flagged_empty.validation_errors)
        self.assertEqual(flagged_full.battery_percent, 100)
        self.assertIsNone(flagged_invalid.battery_percent)
        self.assertIn("battery_percent", flagged_invalid.validation_errors)

    def test_nan_is_rejected(self) -> None:
        body = bytearray(build_status_frame(1)[4:])
        struct.pack_into("<f", body, 37, math.nan)
        raw = frame_bytes(1, FLAG_STATUS_QUERY, bytes(body))
        with self.assertRaises(DecodeError):
            decode_status(FrameParser().feed(raw)[0])

    def test_device_info_manual_vector_and_builder(self) -> None:
        self.assertEqual(len(MANUAL_DEVICE_INFO_FRAME), 100)
        frame = FrameParser().feed(MANUAL_DEVICE_INFO_FRAME)[0]
        info = decode_device_info(frame)
        self.assertEqual(info.device_id, "7601-0000-000001")
        self.assertEqual(info.model_name, "KC761A/B")
        self.assertEqual(info.hw_version_text, "V1.2")
        self.assertEqual(info.fw_version_text, "V1.80")
        self.assertEqual(info.co_fw_version_text, "V1.80")
        self.assertEqual(info.sensor_types, (2, 0, 0))
        self.assertEqual(
            info.cumulative,
            (
                CumulativeChannel(3600, 7200, 1.0, 2.0),
                CumulativeChannel(7200, 14400, 2.0, 4.0),
                CumulativeChannel(10800, 21600, 3.0, 6.0),
            ),
        )
        self.assertFalse(info.hardware_verified)
        self.assertEqual(build_device_info_frame(5), MANUAL_DEVICE_INFO_FRAME)

    def test_decode_classifies_nonsemantic_known_flags(self) -> None:
        frame = FrameParser().feed(build_calibration_frame(1))[0]
        self.assertEqual(decode_frame(frame).kind, "calibration")

    def test_request_rejects_write_and_unknown_commands(self) -> None:
        self.assertEqual(encode_read_request(1, CMD_GET_STA), b"\x00\x53\x01\x00")
        self.assertEqual(encode_read_request(0xFF, CMD_GET_DEVICE_INFO), b"\x00\x54\xff\x00")
        self.assertEqual(encode_read_request(2, CMD_GET_MC_DATA, source=1), b"\x00\x52\x02\x01\x00")
        self.assertEqual(encode_read_request(3, CMD_GET_CAL_DATA), b"\x00\x55\x03\x00")
        with self.assertRaises(ValueError):
            encode_read_request(1, 0x66)
        with self.assertRaises(ValueError):
            encode_read_request(1, CMD_GET_MC_DATA)
        with self.assertRaises(ValueError):
            encode_read_request(1, CMD_GET_STA, source=0)

    def test_spectrum_calibration_ack_and_write_codecs(self) -> None:
        spectrum_frame = FrameParser().feed(
            build_spectrum_frame(7, source=0, offset=10, counts=(20, 22, 24), ratio=2)
        )[0]
        segment = decode_spectrum_segment(spectrum_frame)
        self.assertEqual((segment.source, segment.offset, segment.ratio), (0, 10, 2))
        self.assertEqual(segment.counts, (20, 22, 24))
        self.assertEqual(decode_frame(spectrum_frame).kind, "spectrum")

        calibration_frame = FrameParser().feed(
            build_calibration_frame(
                8,
                rad0_trigger_offset=17,
                rad2_trigger_offset=29,
                rad0_dose_zoom=0.5,
                rad1_dose_zoom=1.25,
                rad2_dose_zoom=4.75,
                neu_window_center=1536,
                altitude_m_offset=-321,
            )
        )[0]
        calibration = decode_calibration(calibration_frame)
        self.assertEqual(calibration.rad0_trigger_offset, 17)
        self.assertEqual(calibration.rad2_trigger_offset, 29)
        self.assertEqual(calibration.rad0_dose_zoom, 0.5)
        self.assertEqual(calibration.rad1_dose_zoom, 1.25)
        self.assertEqual(calibration.rad2_dose_zoom, 4.75)
        self.assertEqual(calibration.neu_window_center, 1536)
        self.assertEqual(calibration.altitude_m_offset, -321)
        self.assertEqual(calibration.energy_kev(0, 123), 123)
        self.assertEqual(calibration.energy_kev(1, 456), 456)
        self.assertEqual(calibration.energy_kev(2, 789), 789)
        with self.assertRaises(ValueError):
            calibration.energy_kev(3, 0)
        with self.assertRaises(ValueError):
            calibration.energy_kev(0, -1)

        ack_frame = FrameParser().feed(build_ack_frame(9, CMD_SET_STA))[0]
        ack = decode_ack(ack_frame)
        self.assertTrue(ack.successful)
        self.assertEqual(ack.command, CMD_SET_STA)
        failed = decode_ack(FrameParser().feed(build_ack_frame(9, CMD_SET_CLEAR, status=1))[0])
        self.assertFalse(failed.successful)

        state = encode_set_state_request(1, sensor_status=4, auto_upload_status=1)
        self.assertEqual(state, b"\x00\x62\x01\x04\xff\xff\x01\x00")
        self.assertEqual(validate_write_request(state), CMD_SET_STA)
        time_request = encode_set_device_time_request(2, 0x67748580)
        self.assertEqual(time_request, b"\x00\x63\x02\x80\x85\x74\x67\x00")
        self.assertEqual(validate_write_request(time_request), CMD_SET_DEVICE_TIME)
        clear = encode_clear_request(3, spectrum_mask=1, dose_curve_mask=2, cumulative_dose_mask=4)
        self.assertEqual(clear, b"\x00\x66\x03\x01\x02\x04\x00")
        self.assertEqual(validate_write_request(clear), CMD_SET_CLEAR)

        with self.assertRaises(ValueError):
            encode_set_state_request(1, auto_upload_status=2)
        with self.assertRaises(ValueError):
            encode_set_device_time_request(1, -1)
        with self.assertRaises(ValueError):
            encode_clear_request(1)
        with self.assertRaises(ValueError):
            encode_clear_request(1, spectrum_mask=8)
        with self.assertRaises(ValueError):
            validate_write_request(b"\x00\x64\x01\x00")

    def test_decode_rejects_wrong_flags_and_lengths(self) -> None:
        frame = RawFrame(0, 0xA1, 9, b"\x00" * 5, b"\x00" * 9)
        with self.assertRaises(DecodeError):
            decode_status(frame)
        bad = RawFrame(0, FLAG_STATUS_QUERY, 80, b"\x00" * 76, b"\x00" * 80)
        with self.assertRaises(DecodeError):
            decode_status(bad)
        bad_info = RawFrame(0, 0xA5, 99, b"\x00" * 95, b"\x00" * 99)
        with self.assertRaises(DecodeError):
            decode_device_info(bad_info)
        with self.assertRaises(DecodeError):
            decode_device_info(RawFrame(0, 0xA2, 100, b"\x00" * 96, b"\x00" * 100))

    def test_spectrum_calibration_ack_and_fixture_validation_edges(self) -> None:
        with self.assertRaises(ValueError, msg="state bytes must be uint8"):
            encode_set_state_request(1, sensor_status=-1)
        with self.assertRaises(ValueError, msg="request envelope must start with zero"):
            validate_write_request(b"\x01\x62\x00")
        with self.assertRaises(ValueError, msg="write request length is command-specific"):
            validate_write_request(b"\x00\x62\x00\x00")

        with self.assertRaises(DecodeError):
            decode_spectrum_segment(RawFrame(1, FLAG_STATUS_QUERY, 9, b"\x00" * 5, b""))
        with self.assertRaises(DecodeError):
            decode_spectrum_segment(RawFrame(1, FLAG_MC_DATA_QUERY, 8, b"\x00" * 4, b""))
        with self.assertRaises(DecodeError):
            decode_spectrum_segment(
                RawFrame(1, FLAG_MC_DATA_QUERY, 9, bytes((3,)) + struct.pack("<HH", 0, 1), b"")
            )
        with self.assertRaises(DecodeError):
            decode_spectrum_segment(
                RawFrame(1, FLAG_MC_DATA_QUERY, 9, bytes((0,)) + struct.pack("<HH", 0, 0), b"")
            )

        with self.assertRaises(DecodeError):
            decode_calibration(RawFrame(1, FLAG_STATUS_QUERY, 150, b"\x00" * 146, b""))
        with self.assertRaises(DecodeError):
            decode_calibration(RawFrame(1, FLAG_CAL_DATA, 149, b"\x00" * 145, b""))
        invalid_calibration = bytearray(146)
        invalid_calibration[1] = 2
        with self.assertRaises(DecodeError):
            decode_calibration(RawFrame(1, FLAG_CAL_DATA, 150, bytes(invalid_calibration), b""))

        with self.assertRaises(DecodeError):
            decode_ack(RawFrame(1, FLAG_STATUS_QUERY, 6, b"\x00\x62", b""))
        with self.assertRaises(DecodeError):
            decode_ack(RawFrame(1, FLAG_ACK, 5, b"\x00", b""))
        with self.assertRaises(DecodeError):
            decode_ack(RawFrame(1, FLAG_ACK, 6, b"\x02\x62", b""))
        with self.assertRaises(DecodeError):
            decode_ack(RawFrame(1, FLAG_ACK, 6, b"\x00\x65", b""))

        with self.assertRaises(ValueError):
            build_spectrum_frame(1, source=3, offset=0, counts=(1,))
        with self.assertRaises(ValueError):
            build_spectrum_frame(1, source=0, offset=0, counts=(3,), ratio=2)
        with self.assertRaises(ValueError):
            build_ack_frame(1, CMD_SET_STA, status=2)

    def test_device_id_and_public_helpers(self) -> None:
        leading = decode_device_info(FrameParser().feed(build_device_info_frame(1, device_id=" leading"))[0])
        self.assertEqual(leading.device_id, " leading")
        trailing = decode_device_info(FrameParser().feed(build_device_info_frame(1, device_id="RAW-ID   "))[0])
        self.assertEqual(trailing.device_id, "RAW-ID")
        empty = bytearray(build_device_info_frame(1)[4:])
        empty[32:48] = b"\x00" * 16
        with self.assertRaises(DecodeError):
            decode_device_info(FrameParser().feed(frame_bytes(1, 0xA5, bytes(empty)))[0])
        non_ascii = bytearray(build_device_info_frame(1)[4:])
        non_ascii[32] = 0xFF
        with self.assertRaises(DecodeError):
            decode_device_info(FrameParser().feed(frame_bytes(1, 0xA5, bytes(non_ascii)))[0])
        embedded_nul = bytearray(build_device_info_frame(1)[4:])
        embedded_nul[32:48] = b"AB\x00CD" + b"\x00" * 11
        with self.assertRaises(DecodeError):
            decode_device_info(FrameParser().feed(frame_bytes(1, 0xA5, bytes(embedded_nul)))[0])
        control = bytearray(build_device_info_frame(1)[4:])
        control[32] = 1
        with self.assertRaises(DecodeError):
            decode_device_info(FrameParser().feed(frame_bytes(1, 0xA5, bytes(control)))[0])
        info = decode_device_info(FrameParser().feed(build_device_info_frame(1))[0])
        status = decode_status(FrameParser().feed(build_status_frame(1))[0])
        self.assertEqual(format_fw_version(180), "V1.80")
        self.assertEqual(format_co_fw_version(11), "V1.1")
        observed_info = decode_device_info(
            FrameParser().feed(build_device_info_frame(2, coprocessor_firmware_version=11))[0]
        )
        self.assertEqual(observed_info.co_fw_version_text, "V1.1")
        self.assertEqual(model_name(10), "KC761")
        self.assertEqual(model_name(255), "Unknown (255)")
        self.assertEqual(sensor_name(255), "Unknown (0xff)")
        self.assertEqual(as_public_dict(info)["device_id_suffix"], "0001")
        self.assertEqual(redacted_device_id_suffix("AB"), "<2 characters>")
        self.assertEqual(redacted_device_id_suffix("ABCDE"), "BCDE")
        self.assertEqual(as_public_dict(status)["battery_percent"], 87)
        self.assertEqual(as_public_dict(RawFrame(1, 2, 4, b"", b"\x01\x02\x04\x00"))["flag"], 2)
        self.assertEqual(as_public_dict(object())["type"], "object")
        self.assertIsNone(utc_from_epoch(0))
        self.assertIsNone(utc_from_epoch(2**63))

        with self.assertRaises(ValueError):
            from custom_components.kc761.protocol.codec import build_device_info_frame as build_info

            build_info(1, device_id="x" * 17)

    def test_transport_without_connection_errors(self) -> None:
        async def run() -> None:
            transport = TcpTransport("127.0.0.1", 1, connect_timeout=0.01, write_timeout=0.01)
            with self.assertRaises(TransportError):
                await transport.async_read()
            with self.assertRaises(TransportError):
                await transport.async_write(b"x")
            await transport.async_close()

        asyncio.run(run())

    def test_transport_error_paths(self) -> None:
        async def run() -> None:
            transport = TcpTransport("127.0.0.1", 1, connect_timeout=0.01, write_timeout=0.01)
            with self.assertRaises(TransportError):
                await transport.async_connect()

            transport.reader = MagicMock()
            transport.reader.read = AsyncMock(side_effect=OSError("read"))
            with self.assertRaises(TransportError):
                await transport.async_read()

            transport.writer = MagicMock()
            transport.writer.is_closing.return_value = False
            transport.writer.drain = AsyncMock(side_effect=OSError("write"))
            transport.writer.wait_closed = AsyncMock(side_effect=OSError("close"))
            with self.assertRaises(TransportError):
                await transport.async_write(b"x")

            transport.writer = MagicMock()
            transport.writer.wait_closed = AsyncMock(side_effect=OSError("close"))
            await transport.async_close()

            connected = TcpTransport("host", 1)
            connected.writer = MagicMock()
            connected.writer.is_closing.return_value = False
            await connected.async_connect()

        asyncio.run(run())


class ClientSimulatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_rejects_non_read_commands(self) -> None:
        client = KC761Client("127.0.0.1", 1)
        with self.assertRaises(ValueError):
            await client.async_request(0x66)
        with self.assertRaises(ValueError):
            await client.async_request(0x52)

    async def test_client_pending_close_wrong_types_and_routes(self) -> None:
        client = KC761Client("127.0.0.1", 1)
        loop = asyncio.get_running_loop()
        pending = loop.create_future()
        client._pending[1] = PendingTransaction(0x53, frozenset({0xA2}), pending)
        await client._route_frame(RawFrame(1, 0xA2, 81, b"bad", b"bad"))
        self.assertEqual(client.protocol_errors, 1)
        pending.set_result(None)
        await client._route_frame(RawFrame(1, 0xFE, 4, b"", b"\x01\xfe\x04\x00", known=False))
        client.on_push = lambda decoded: None
        await client._route_frame(RawFrame(2, 0xFE, 4, b"", b"\x02\xfe\x04\x00", known=False))
        client._pending.clear()
        client._pending[3] = PendingTransaction(0x53, frozenset({0xA2}), loop.create_future())
        await client._route_frame(RawFrame(3, 0xA2, 81, b"bad", b"bad"))
        await client.async_close()

        bad_status = KC761Client("127.0.0.1", 1)
        bad_status.async_request = AsyncMock(return_value=DecodedFrame(RawFrame(0, 0xA2, 81, b"", b""), "status", None))
        with self.assertRaises(TransactionError):
            await bad_status.async_get_status()
        bad_info = KC761Client("127.0.0.1", 1)
        bad_info.async_request = AsyncMock(
            return_value=DecodedFrame(RawFrame(0, 0xA5, 100, b"", b""), "device_info", None)
        )
        with self.assertRaises(TransactionError):
            await bad_info.async_get_device_info()
        bad_calibration = KC761Client("127.0.0.1", 1)
        bad_calibration.async_request = AsyncMock(return_value=object())
        with self.assertRaises(TransactionError):
            await bad_calibration.async_get_calibration()
        bad_spectrum = KC761Client("127.0.0.1", 1)
        bad_spectrum.async_request = AsyncMock(return_value=object())
        with self.assertRaises(TransactionError):
            await bad_spectrum.async_get_spectrum(0)

    async def test_pending_spectrum_fragment_validation_edges(self) -> None:
        loop = asyncio.get_running_loop()
        no_source = PendingTransaction(
            CMD_GET_MC_DATA,
            frozenset({FLAG_MC_DATA_QUERY}),
            loop.create_future(),
        )
        with self.assertRaises(TransactionError):
            no_source.add_spectrum_segment(SpectrumSegment(0, 0, 1, (1,), FLAG_MC_DATA_QUERY))

        pending = PendingTransaction(
            CMD_GET_MC_DATA,
            frozenset({FLAG_MC_DATA_QUERY}),
            loop.create_future(),
            spectrum_source=0,
        )
        with self.assertRaises(TransactionError):
            pending.add_spectrum_segment(SpectrumSegment(1, 0, 1, (1,), FLAG_MC_DATA_QUERY))
        with self.assertRaises(TransactionError):
            pending.add_spectrum_segment(SpectrumSegment(0, 2048, 1, (1,), FLAG_MC_DATA_QUERY))

        self.assertIsNone(
            pending.add_spectrum_segment(
                SpectrumSegment(0, 2047, 1, (7, 8), FLAG_MC_DATA_QUERY)
            )
        )
        self.assertEqual(pending.spectrum_filled, 1)
        with self.assertRaises(TransactionError):
            pending.add_spectrum_segment(SpectrumSegment(0, 2047, 1, (9,), FLAG_MC_DATA_QUERY))
        self.assertEqual(pending.spectrum_filled, 1)

        complete = PendingTransaction(
            CMD_GET_MC_DATA,
            frozenset({FLAG_MC_DATA_QUERY}),
            loop.create_future(),
            spectrum_source=0,
        )
        first = tuple(range(16))
        self.assertIsNone(
            complete.add_spectrum_segment(SpectrumSegment(0, 0, 1, first, FLAG_MC_DATA_QUERY))
        )
        self.assertIsNone(
            complete.add_spectrum_segment(SpectrumSegment(0, 0, 1, first, FLAG_MC_DATA_QUERY))
        )
        self.assertEqual(complete.spectrum_filled, 16)
        result = None
        for offset in range(16, 2048, 16):
            result = complete.add_spectrum_segment(
                SpectrumSegment(
                    0,
                    offset,
                    1,
                    tuple(range(offset, offset + 16)),
                    FLAG_MC_DATA_QUERY,
                )
            )
        self.assertIsInstance(result, SpectrumData)
        self.assertEqual(complete.spectrum_filled, 2048)
        self.assertEqual(result.counts, tuple(range(2048)))
        no_source.future.cancel()
        pending.future.cancel()
        complete.future.cancel()

    async def test_client_connect_fast_path_and_stale_reader_cleanup(self) -> None:
        client = KC761Client("127.0.0.1", 1)
        transport = MagicMock()
        transport.connected = False
        transport.async_close = AsyncMock()
        transport.async_connect = AsyncMock()
        client.transport = transport
        stale_task = asyncio.create_task(asyncio.sleep(10))
        client._reader_task = stale_task

        def create_task(coroutine, name):
            del name
            coroutine.close()
            return asyncio.create_task(asyncio.sleep(10))

        client.task_creator = create_task
        await client.async_connect()
        self.assertTrue(stale_task.cancelled())
        transport.async_close.assert_awaited_once()
        transport.async_connect.assert_awaited_once()
        await client.async_close()

        fast = KC761Client("127.0.0.1", 1)
        fast.transport = transport
        transport.connected = True
        fast._reader_task = asyncio.create_task(asyncio.sleep(10))
        await fast.async_connect()
        transport.async_connect.assert_awaited_once()
        await fast.async_close()

    async def test_client_request_and_reader_exception_branches(self) -> None:
        client = KC761Client("127.0.0.1", 1)
        with self.assertRaises(ValueError):
            await client.async_request(CMD_GET_STA, source=0)

        timeout_client = KC761Client("127.0.0.1", 1, request_timeout=0.001)
        timeout_transport = MagicMock()
        timeout_transport.connected = True
        timeout_transport.async_write = AsyncMock()
        timeout_transport.async_close = AsyncMock()
        timeout_client.transport = timeout_transport
        timeout_client._reader_task = asyncio.create_task(asyncio.sleep(10))
        with self.assertRaises(TimeoutError):
            await timeout_client.async_set_device_time(1)

        bad_client = KC761Client("127.0.0.1", 1)
        bad_transport = MagicMock()
        bad_transport.connected = True
        bad_transport.async_close = AsyncMock()
        bad_transport.async_write = AsyncMock()
        bad_client.transport = bad_transport
        bad_client._reader_task = asyncio.create_task(asyncio.sleep(10))

        async def set_bad_result(_request: bytes) -> None:
            pending = next(iter(bad_client._pending.values()))
            pending.future.set_result(
                DecodedFrame(RawFrame(0, FLAG_ACK, 8, b"", b""), "ack", None)
            )

        bad_transport.async_write.side_effect = set_bad_result
        with self.assertRaises(TransactionError):
            await bad_client.async_set_device_time(1)
        await bad_client.async_close()

        for read_result in (b"",):
            reader_client = KC761Client("127.0.0.1", 1)
            reader_transport = MagicMock()
            reader_transport.async_read = AsyncMock(return_value=read_result)
            reader_transport.async_close = AsyncMock()
            reader_client.transport = reader_transport
            await reader_client._reader_loop()
            reader_transport.async_close.assert_awaited_once()

        generic_client = KC761Client("127.0.0.1", 1)
        generic_transport = MagicMock()
        generic_transport.async_read = AsyncMock(side_effect=RuntimeError("reader"))
        generic_transport.async_close = AsyncMock()
        generic_client.transport = generic_transport
        await generic_client._reader_loop()
        self.assertEqual(generic_client.protocol_errors, 1)

        error_client = KC761Client("127.0.0.1", 1)
        error_client.parser = MagicMock()
        error_client.parser.errors = 0
        error_client.parser.feed.return_value = []
        error_client.parser.flush.return_value = []
        error_client.consecutive_protocol_errors = MAX_CONSECUTIVE_PROTOCOL_ERRORS
        error_transport = MagicMock()
        error_transport.async_read = AsyncMock(return_value=b"bad")
        error_transport.async_close = AsyncMock()
        error_client.transport = error_transport
        await error_client._reader_loop()
        error_client.parser.feed.assert_called_once_with(b"bad")

    async def test_client_routes_transaction_error_and_done_future(self) -> None:
        frame = RawFrame(0, FLAG_MC_DATA_QUERY, 81, b"", b"")
        client = KC761Client("127.0.0.1", 1)
        loop = asyncio.get_running_loop()
        conflict = PendingTransaction(
            CMD_GET_MC_DATA,
            frozenset({FLAG_MC_DATA_QUERY}),
            loop.create_future(),
            spectrum_source=0,
        )
        conflict.spectrum_counts[0] = 1
        conflict.spectrum_filled = 1
        client._pending[0] = conflict
        decoded_segment = SpectrumSegment(0, 0, 1, (2,), FLAG_MC_DATA_QUERY)
        with patch.object(
            client_module,
            "decode_frame",
            return_value=DecodedFrame(frame, "spectrum", decoded_segment),
        ):
            await client._route_frame(frame)
        self.assertIsInstance(conflict.future.exception(), TransactionError)

        done = loop.create_future()
        done.set_result(None)
        client._pending[1] = PendingTransaction(
            CMD_GET_STA,
            frozenset({FLAG_STATUS_QUERY}),
            done,
        )
        status_frame = RawFrame(1, FLAG_STATUS_QUERY, 81, b"", b"")
        with patch.object(
            client_module,
            "decode_frame",
            return_value=DecodedFrame(status_frame, "status", None),
        ):
            await client._route_frame(status_frame)
        self.assertEqual(client.responses, 1)

        threshold = KC761Client("127.0.0.1", 1)
        threshold.consecutive_protocol_errors = MAX_CONSECUTIVE_PROTOCOL_ERRORS - 1
        with self.assertRaises(ConnectionError):
            await threshold._route_frame(RawFrame(0, FLAG_STATUS_QUERY, 81, b"bad", b"bad"))

        upload_received = []

        async def on_upload(decoded) -> None:
            upload_received.append(decoded.kind)

        upload_client = KC761Client("127.0.0.1", 1, on_push=on_upload)
        upload = FrameParser().feed(build_status_frame(2, upload=True))[0]
        await upload_client._route_frame(upload)
        self.assertEqual(upload_received, ["status"])

    def test_auto_upload_spectrum_assembler_round_boundaries_and_duplicates(self) -> None:
        assembler = SpectrumUploadAssembler()
        assembler.begin_round(0)
        self.assertEqual(len(assembler.counts), 2048)
        self.assertIsNone(
            assembler.add(SpectrumSegment(0, -1, 1, (1,), FLAG_MC_DATA_UPLOAD))
        )
        self.assertIsNone(assembler.source)
        assembler.begin_round(0)
        self.assertIsNone(
            assembler.add(SpectrumSegment(1, 0, 1, tuple(range(512)), FLAG_MC_DATA_UPLOAD))
        )
        first = tuple(range(1024, 1536))
        self.assertIsNone(assembler.add(SpectrumSegment(0, 1024, 1, first, FLAG_MC_DATA_UPLOAD)))
        self.assertEqual(assembler.segment_count, 1)
        self.assertIsNone(assembler.add(SpectrumSegment(0, 1024, 1, first, FLAG_MC_DATA_UPLOAD)))
        self.assertEqual(assembler.segment_count, 1)
        self.assertIsNone(
            assembler.add(SpectrumSegment(0, 1024, 1, tuple(range(999, 1511)), FLAG_MC_DATA_UPLOAD))
        )
        self.assertIsNone(assembler.source)

        assembler.begin_round(0)
        self.assertIsNone(
            assembler.add(SpectrumSegment(0, 1536, 1, tuple(range(1536, 2048)), FLAG_MC_DATA_UPLOAD))
        )
        self.assertIsNone(
            assembler.add(SpectrumSegment(0, 0, 1, tuple(range(512)), FLAG_MC_DATA_UPLOAD))
        )
        self.assertIsNone(
            assembler.add(SpectrumSegment(0, 512, 1, tuple(range(512, 1024)), FLAG_MC_DATA_UPLOAD))
        )
        complete = assembler.add(SpectrumSegment(0, 1024, 1, first, FLAG_MC_DATA_UPLOAD))
        self.assertIsNotNone(complete)
        assert complete is not None
        self.assertEqual(complete.source, 0)
        self.assertEqual(complete.segment_count, 4)
        self.assertEqual(complete.counts, tuple(range(2048)))
        self.assertTrue(assembler.complete)
        self.assertIsNone(
            assembler.add(SpectrumSegment(0, 0, 1, tuple(range(512)), FLAG_MC_DATA_UPLOAD))
        )
        assembler.begin_round(1)
        self.assertIsNone(assembler.add(SpectrumSegment(0, 0, 1, (1,), FLAG_MC_DATA_UPLOAD)))
        self.assertIsNone(assembler.add(SpectrumSegment(1, 0, 1, (1,), FLAG_MC_DATA_QUERY)))
        self.assertFalse(assembler.complete)

    async def test_client_only_sends_read_commands(self) -> None:
        async with Kc761Simulator() as simulator:
            client = KC761Client(*simulator.address)
            try:
                info = await client.async_get_device_info()
                status = await client.async_get_status()
            finally:
                await client.async_close()
        self.assertEqual(info.device_id, "7601-0000-000001")
        self.assertEqual(status.battery_percent, 87)
        self.assertTrue(set(simulator.received_commands) <= {0x53, 0x54})
        self.assertEqual(simulator.received_syncs, [0, 1])
        self.assertEqual(simulator.forbidden_commands, [])
        self.assertEqual(simulator.invalid_requests, [])

    async def test_client_spectrum_calibration_and_writes(self) -> None:
        scenario = SimulatorScenario(sensor_types=(2, 8, 5), status_kwargs={"sensor_status": 0x04})
        async with Kc761Simulator(scenario) as simulator:
            client = KC761Client(*simulator.address, request_timeout=2)
            try:
                spectrum = await client.async_get_spectrum(0)
                calibration = await client.async_get_calibration()
                await client.async_set_state(sensor_status=0x14, auto_upload_status=1)
                await client.async_set_device_time(1_735_689_601)
                await client.async_clear_statistics(spectrum_mask=1)
                status = await client.async_get_status()
            finally:
                await client.async_close()
        self.assertIsInstance(spectrum, SpectrumData)
        self.assertEqual(len(spectrum.counts), 2048)
        self.assertEqual(spectrum.counts[1024], 1024)
        self.assertEqual(spectrum.segment_count, 4)
        self.assertEqual(calibration.energy_kev(0, 2047), 2047)
        self.assertEqual(status.sensor_status, 0x14)
        self.assertEqual(status.auto_upload_status, 1)
        self.assertEqual(simulator.clear_requests, [(1, 0, 0)])
        self.assertEqual(
            simulator.received_commands,
            [CMD_GET_MC_DATA, CMD_GET_CAL_DATA, CMD_SET_STA, CMD_SET_DEVICE_TIME, CMD_SET_CLEAR, CMD_GET_STA],
        )
        self.assertEqual(simulator.forbidden_commands, [])

    async def test_client_rejects_failed_or_mismatched_ack(self) -> None:
        async with Kc761Simulator(SimulatorScenario(ack_status=1)) as simulator:
            client = KC761Client(*simulator.address)
            with self.assertRaises(TransactionError):
                await client.async_set_device_time(1)
            await client.async_close()
        async with Kc761Simulator(SimulatorScenario(ack_command_override=CMD_SET_CLEAR)) as simulator:
            client = KC761Client(*simulator.address)
            with self.assertRaises(TransactionError):
                await client.async_set_device_time(1)
            await client.async_close()

    async def test_client_serializes_concurrent_reads(self) -> None:
        scenario = SimulatorScenario(response_delay=0.02)
        async with Kc761Simulator(scenario) as simulator:
            client = KC761Client(*simulator.address)
            maximum_pending = 0
            original_write = client.transport.async_write

            async def tracked_write(data: bytes) -> None:
                nonlocal maximum_pending
                maximum_pending = max(maximum_pending, len(client._pending))
                await original_write(data)

            client.transport.async_write = tracked_write
            try:
                status, info = await asyncio.gather(
                    client.async_get_status(),
                    client.async_get_device_info(),
                )
            finally:
                await client.async_close()
        self.assertEqual(status.battery_percent, 87)
        self.assertEqual(info.device_id, "7601-0000-000001")
        self.assertEqual(maximum_pending, 1)

    async def test_fragmented_response_and_push(self) -> None:
        scenario = SimulatorScenario(fragment_size=1, push_upload=True, coalesce_push=True)
        async with Kc761Simulator(scenario) as simulator:
            client = KC761Client(*simulator.address, request_timeout=2)
            try:
                status = await client.async_get_status()
                await asyncio.sleep(0)
            finally:
                await client.async_close()
        self.assertEqual(status.battery_percent, 87)
        self.assertGreaterEqual(client.push_frames, 1)

    async def test_simulator_auto_upload_spectrum_pushes_reordered_a1(self) -> None:
        received = []
        scenario = SimulatorScenario(
            push_upload=True,
            auto_upload_spectrum=True,
            auto_upload_reverse_order=True,
            auto_upload_duplicate_first=True,
            spectrum_fragment_size=512,
        )
        async with Kc761Simulator(scenario) as simulator:
            client = KC761Client(
                *simulator.address,
                request_timeout=2,
                on_push=lambda decoded: received.append(decoded),
            )
            try:
                await client.async_get_status()
                await asyncio.sleep(0.01)
            finally:
                await client.async_close()
        self.assertEqual(received[0].frame.flag, 0xA3)
        segments = [item.data for item in received[1:] if item.frame.flag == FLAG_MC_DATA_UPLOAD]
        self.assertEqual([segment.offset for segment in segments], [1536, 1536, 1024, 512, 0])
        self.assertTrue(all(segment.source == 0 for segment in segments))

    async def test_half_frame_closes_pending_request(self) -> None:
        async with Kc761Simulator(SimulatorScenario(close_mid_frame=True)) as simulator:
            client = KC761Client(*simulator.address, request_timeout=0.5)
            with self.assertRaises((ConnectionError, TimeoutError)):
                await client.async_get_status()
            await asyncio.sleep(0)
            self.assertFalse(client.connected)
            await client.async_close()

    async def test_graceful_eof_reconnects_on_next_request(self) -> None:
        scenario = SimulatorScenario(close_after_response=True)
        async with Kc761Simulator(scenario) as simulator:
            client = KC761Client(*simulator.address, request_timeout=0.5)
            try:
                info = await client.async_get_device_info()
                await asyncio.sleep(0.05)
                self.assertFalse(client.connected)
                status = await client.async_get_status()
            finally:
                await client.async_close()
        self.assertEqual(info.device_id, "7601-0000-000001")
        self.assertEqual(status.battery_percent, 87)
        self.assertEqual(simulator.connections, 2)
        self.assertEqual(simulator.received_commands, [0x54, 0x53])

    async def test_response_timeout_and_close_pending(self) -> None:
        scenario = SimulatorScenario(response_delay=0.3)
        async with Kc761Simulator(scenario) as simulator:
            client = KC761Client(*simulator.address, request_timeout=0.05)
            with self.assertRaises(TimeoutError):
                await client.async_get_status()
            await client.async_close()

        async with Kc761Simulator(SimulatorScenario(response_delay=1)) as simulator:
            client = KC761Client(*simulator.address, request_timeout=2)
            task = asyncio.create_task(client.async_get_status())
            await asyncio.sleep(0.01)
            await client.async_close()
            with self.assertRaises(ConnectionError):
                await task

    async def test_timed_out_request_closes_connection(self) -> None:
        received: list[str] = []
        scenario = SimulatorScenario(response_delay=0.1)
        async with Kc761Simulator(scenario) as simulator:
            client = KC761Client(
                *simulator.address,
                request_timeout=0.03,
                on_push=lambda decoded: received.append(decoded.kind),
            )
            try:
                with self.assertRaises(TimeoutError):
                    await client.async_get_status()
                self.assertFalse(client.connected)
                self.assertEqual(client.push_frames, 0)
                self.assertEqual(received, [])
            finally:
                await client.async_close()

    async def test_unmatched_query_response_is_not_routed_as_push(self) -> None:
        received: list[str] = []
        client = KC761Client(
            "127.0.0.1",
            1,
            on_push=lambda decoded: received.append(decoded.kind),
        )
        frame = FrameParser().feed(build_status_frame(7))[0]
        await client._route_frame(frame)
        self.assertEqual(client.late_responses, 1)
        self.assertEqual(client.push_frames, 0)
        self.assertEqual(received, [])

    async def test_parser_errors_are_exposed_by_client(self) -> None:
        scenario = SimulatorScenario(malformed_length=1)
        async with Kc761Simulator(scenario) as simulator:
            client = KC761Client(*simulator.address, request_timeout=0.05)
            try:
                with self.assertRaises((ConnectionError, TimeoutError)):
                    await client.async_get_device_info()
                self.assertGreater(client.parser.errors, 0)
                self.assertEqual(client.protocol_errors, client.parser.errors)
            finally:
                await client.async_close()

    async def test_push_callback_and_bad_frame_counter(self) -> None:
        received: list[str] = []

        async def callback(decoded: object) -> None:
            received.append(type(decoded).__name__)

        client = KC761Client("127.0.0.1", 1, on_push=callback)
        await client._route_frame(RawFrame(1, 0xA2, 81, b"bad", b"bad"))
        self.assertEqual(client.protocol_errors, 1)
        upload = FrameParser().feed(build_status_frame(2, upload=True))[0]
        await client._route_frame(upload)
        self.assertEqual(received, ["DecodedFrame"])
        await client._route_frame(RawFrame(2, 0xFE, 4, b"", b"\x02\xfe\x04\x00", known=False))
        self.assertEqual(received, ["DecodedFrame"])
        self.assertEqual(client.unhandled_frames, 1)

    def test_sync_allocator_exhaustion(self) -> None:
        client = KC761Client("127.0.0.1", 1)
        loop = asyncio.new_event_loop()
        try:
            futures = {
                index: PendingTransaction(0x53, frozenset({0xA2}), loop.create_future())
                for index in range(256)
            }
            client._pending.update(futures)
            with self.assertRaises(TransactionError):
                client._allocate_sync()
            for pending in futures.values():
                pending.future.cancel()
        finally:
            loop.close()
