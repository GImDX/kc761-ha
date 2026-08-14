"""Offline protocol and simulator tests."""

from __future__ import annotations

import asyncio
import math
import struct
import unittest
from unittest.mock import AsyncMock, MagicMock

from custom_components.kc761.const import CMD_GET_DEVICE_INFO, CMD_GET_STA, FLAG_STATUS_QUERY
from custom_components.kc761.protocol.client import KC761Client
from custom_components.kc761.protocol.codec import (
    HARDWARE_VERIFIED,
    build_device_info_frame,
    build_status_frame,
    decode_device_info,
    decode_frame,
    decode_status,
    encode_read_request,
    model_name,
    sensor_name,
    DecodedFrame,
)
from custom_components.kc761.protocol.framing import FrameParser, frame_bytes
from custom_components.kc761.protocol.models import (
    DecodeError,
    RawFrame,
    TransactionError,
    as_public_dict,
    format_fw_version,
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
        self.assertGreaterEqual(parser.errors, 3)
        parser.feed(b"")
        parser._resync("empty")

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

    def test_invalid_battery_is_unknown(self) -> None:
        raw = build_status_frame(1, battery_percent=101)
        data = decode_status(FrameParser().feed(raw)[0])
        self.assertIsNone(data.battery_percent)

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
        self.assertEqual(info.cumulative[0].spectrum_runtime, 3600)
        self.assertEqual(info.cumulative[2].cumulative_dose_equivalent, 6.0)
        self.assertFalse(info.hardware_verified)
        self.assertEqual(build_device_info_frame(5), MANUAL_DEVICE_INFO_FRAME)

    def test_decode_classifies_nonsemantic_known_flags(self) -> None:
        frame = FrameParser().feed(frame_bytes(1, 0xA6, b"\x00" * 146))[0]
        self.assertEqual(decode_frame(frame).kind, "calibration")

    def test_request_rejects_write_and_unknown_commands(self) -> None:
        self.assertEqual(encode_read_request(1, CMD_GET_STA), b"\x00\x53\x01\x00")
        self.assertEqual(
            encode_read_request(0xFF, CMD_GET_DEVICE_INFO), b"\x00\x54\xff\x00"
        )
        with self.assertRaises(ValueError):
            encode_read_request(1, 0x66)

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

    def test_device_id_and_public_helpers(self) -> None:
        empty = bytearray(build_device_info_frame(1)[4:])
        empty[32:48] = b"\x00" * 16
        with self.assertRaises(DecodeError):
            decode_device_info(FrameParser().feed(frame_bytes(1, 0xA5, bytes(empty)))[0])
        non_ascii = bytearray(build_device_info_frame(1)[4:])
        non_ascii[32] = 0xFF
        with self.assertRaises(DecodeError):
            decode_device_info(FrameParser().feed(frame_bytes(1, 0xA5, bytes(non_ascii)))[0])
        control = bytearray(build_device_info_frame(1)[4:])
        control[32] = 1
        with self.assertRaises(DecodeError):
            decode_device_info(FrameParser().feed(frame_bytes(1, 0xA5, bytes(control)))[0])
        info = decode_device_info(FrameParser().feed(build_device_info_frame(1))[0])
        status = decode_status(FrameParser().feed(build_status_frame(1))[0])
        self.assertEqual(format_fw_version(180), "V1.80")
        self.assertEqual(model_name(255), "Unknown (255)")
        self.assertEqual(sensor_name(255), "Unknown (0xff)")
        self.assertEqual(as_public_dict(info)["device_id_suffix"], "0001")
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
            transport = TcpTransport("127.0.0.1", 1, connect_timeout=0.01, read_timeout=0.01)
            with self.assertRaises(TransportError):
                await transport.async_read()
            with self.assertRaises(TransportError):
                await transport.async_write(b"x")
            await transport.async_close()

        asyncio.run(run())

    def test_transport_error_paths(self) -> None:
        async def run() -> None:
            transport = TcpTransport("127.0.0.1", 1, connect_timeout=0.01, read_timeout=0.01)
            with self.assertRaises(TransportError):
                await transport.async_connect()

            transport.reader = MagicMock()
            transport.reader.read = AsyncMock(side_effect=OSError("read"))
            with self.assertRaises(TransportError):
                await transport.async_read()

            transport.writer = MagicMock()
            transport.writer.is_closing.return_value = False
            transport.writer.drain = AsyncMock(side_effect=OSError("write"))
            with self.assertRaises(TransportError):
                await transport.async_write(b"x")

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
        client._pending[1] = (frozenset({0xA2}), pending)
        await client._route_frame(RawFrame(1, 0xA2, 81, b"bad", b"bad"))
        self.assertEqual(client.protocol_errors, 1)
        pending.set_result(None)
        await client._route_frame(RawFrame(1, 0xFE, 4, b"", b"\x01\xfe\x04\x00", known=False))
        client.on_push = lambda decoded: None
        await client._route_frame(RawFrame(2, 0xFE, 4, b"", b"\x02\xfe\x04\x00", known=False))
        client._pending.clear()
        client._pending[3] = (frozenset({0xA2}), loop.create_future())
        await client._route_frame(RawFrame(3, 0xA2, 81, b"bad", b"bad"))
        await client.async_close()

        bad_status = KC761Client("127.0.0.1", 1)
        bad_status.async_request = AsyncMock(
            return_value=DecodedFrame(RawFrame(0, 0xA2, 81, b"", b""), "status", None)
        )
        with self.assertRaises(TransactionError):
            await bad_status.async_get_status()
        bad_info = KC761Client("127.0.0.1", 1)
        bad_info.async_request = AsyncMock(
            return_value=DecodedFrame(RawFrame(0, 0xA5, 100, b"", b""), "device_info", None)
        )
        with self.assertRaises(TransactionError):
            await bad_info.async_get_device_info()

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

    async def test_half_frame_closes_pending_request(self) -> None:
        async with Kc761Simulator(SimulatorScenario(close_mid_frame=True)) as simulator:
            client = KC761Client(*simulator.address, request_timeout=0.5)
            with self.assertRaises((ConnectionError, TimeoutError)):
                await client.async_get_status()
            await client.async_close()

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

    async def test_push_callback_and_bad_frame_counter(self) -> None:
        received: list[str] = []

        async def callback(decoded: object) -> None:
            received.append(type(decoded).__name__)

        client = KC761Client("127.0.0.1", 1, on_push=callback)
        await client._route_frame(RawFrame(1, 0xA2, 81, b"bad", b"bad"))
        self.assertEqual(client.protocol_errors, 1)
        await client._route_frame(RawFrame(2, 0xFE, 4, b"", b"\x02\xfe\x04\x00", known=False))
        self.assertEqual(received, ["DecodedFrame"])

    def test_sync_allocator_exhaustion(self) -> None:
        client = KC761Client("127.0.0.1", 1)
        loop = asyncio.new_event_loop()
        try:
            futures = {index: (frozenset({0xA2}), loop.create_future()) for index in range(256)}
            client._pending.update(futures)
            with self.assertRaises(TransactionError):
                client._allocate_sync()
            for _, future in futures.values():
                future.cancel()
        finally:
            loop.close()
