"""Deterministic asyncio KC761 TCP simulator for protocol and HA tests."""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass, field

from custom_components.kc761.const import (
    CMD_GET_CAL_DATA,
    CMD_GET_DEVICE_INFO,
    CMD_GET_MC_DATA,
    CMD_GET_STA,
    CMD_SET_CLEAR,
    CMD_SET_DEVICE_TIME,
    CMD_SET_REVISED_DATA,
    CMD_SET_STA,
    FORBIDDEN_WRITE_COMMANDS,
)
from custom_components.kc761.protocol.codec import (
    build_device_info_frame,
    build_ack_frame,
    build_calibration_frame,
    build_spectrum_frame,
    build_status_frame,
)
from custom_components.kc761.protocol.framing import frame_bytes


@dataclass(slots=True)
class SimulatorScenario:
    """Fault and payload knobs used by tests."""

    model: int = 12
    sensor_types: tuple[int, int, int] = (2, 0, 0)
    device_id: str = "7601-0000-000001"
    fragment_size: int = 0
    response_delay: float = 0.0
    close_mid_frame: bool = False
    malformed_length: int | None = None
    unknown_prefix: bytes = b""
    coalesce_push: bool = False
    push_upload: bool = False
    close_after_response: bool = False
    status_kwargs: dict[str, object] = field(default_factory=dict)
    ack_status: int = 0
    ack_command_override: int | None = None
    spectrum_fragment_size: int = 512
    spectrum_reverse_order: bool = False


class Kc761Simulator:
    """A local-only fake KC761 TCP server."""

    def __init__(self, scenario: SimulatorScenario | None = None) -> None:
        self.scenario = scenario or SimulatorScenario()
        self.server: asyncio.AbstractServer | None = None
        self.host = "127.0.0.1"
        self.port = 0
        self.received_commands: list[int] = []
        self.received_syncs: list[int] = []
        self.forbidden_commands: list[int] = []
        self.invalid_requests: list[bytes] = []
        self.write_requests: list[bytes] = []
        self.clear_requests: list[tuple[int, int, int]] = []
        self.connections = 0

    @property
    def address(self) -> tuple[str, int]:
        return self.host, self.port

    async def __aenter__(self) -> Kc761Simulator:
        self.server = await asyncio.start_server(self._handle_client, self.host, 0)
        socket = self.server.sockets[0]
        self.port = int(socket.getsockname()[1])
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.async_close()

    async def async_close(self) -> None:
        if self.server is None:
            return
        self.server.close()
        await self.server.wait_closed()
        self.server = None

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.connections += 1
        try:
            while True:
                request = await self._read_request(reader)
                command, sync = request[1], request[2]
                self.received_syncs.append(sync)
                self.received_commands.append(command)
                if request[0] != 0 or request[-1] != 0:
                    self.invalid_requests.append(request)
                    writer.close()
                    await writer.wait_closed()
                    return
                if command in FORBIDDEN_WRITE_COMMANDS or command not in {
                    CMD_GET_MC_DATA,
                    CMD_GET_STA,
                    CMD_GET_DEVICE_INFO,
                    CMD_GET_CAL_DATA,
                    CMD_SET_STA,
                    CMD_SET_DEVICE_TIME,
                    CMD_SET_CLEAR,
                }:
                    self.forbidden_commands.append(command)
                    writer.close()
                    await writer.wait_closed()
                    return
                if command == CMD_GET_STA:
                    response = build_status_frame(
                        sync,
                        **self.scenario.status_kwargs,
                    )
                elif command == CMD_GET_DEVICE_INFO:
                    response = build_device_info_frame(
                        sync,
                        model=self.scenario.model,
                        sensor_types=self.scenario.sensor_types,
                        device_id=self.scenario.device_id,
                    )
                elif command == CMD_GET_CAL_DATA:
                    response = build_calibration_frame(sync)
                elif command == CMD_GET_MC_DATA:
                    response = self._build_spectrum_response(sync, request[3])
                else:
                    self.write_requests.append(request)
                    self._apply_write(command, request)
                    ack_command = self.scenario.ack_command_override
                    response = build_ack_frame(
                        sync,
                        command if ack_command is None else ack_command,
                        status=self.scenario.ack_status,
                    )
                if self.scenario.push_upload and self.scenario.coalesce_push:
                    response = (
                        build_status_frame(
                            sync,
                            upload=True,
                            auto_upload_status=1,
                            **self.scenario.status_kwargs,
                        )
                        + response
                    )
                if self.scenario.unknown_prefix:
                    response = self.scenario.unknown_prefix + response
                if self.scenario.malformed_length is not None:
                    response = (
                        response[:2]
                        + self.scenario.malformed_length.to_bytes(2, "little")
                        + response[4:]
                    )
                if self.scenario.response_delay:
                    await asyncio.sleep(self.scenario.response_delay)
                if self.scenario.close_mid_frame:
                    cut = max(1, len(response) // 2)
                    writer.write(response[:cut])
                    await writer.drain()
                    writer.close()
                    await writer.wait_closed()
                    return
                await self._send(writer, response)
                if self.scenario.close_after_response:
                    writer.close()
                    await writer.wait_closed()
                    return
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            if not writer.is_closing():
                writer.close()
                await writer.wait_closed()

    async def _send(self, writer: asyncio.StreamWriter, data: bytes) -> None:
        step = self.scenario.fragment_size
        if step <= 0:
            writer.write(data)
            await writer.drain()
            return
        for offset in range(0, len(data), step):
            writer.write(data[offset : offset + step])
            await writer.drain()
            await asyncio.sleep(0)

    async def _read_request(self, reader: asyncio.StreamReader) -> bytes:
        prefix = await reader.readexactly(2)
        command = prefix[1]
        length = {
            CMD_GET_MC_DATA: 5,
            CMD_GET_STA: 4,
            CMD_GET_DEVICE_INFO: 4,
            CMD_GET_CAL_DATA: 4,
            CMD_SET_STA: 8,
            CMD_SET_DEVICE_TIME: 8,
            CMD_SET_REVISED_DATA: 65,
            CMD_SET_CLEAR: 7,
        }.get(command, 4)
        return prefix + await reader.readexactly(length - 2)

    def _build_spectrum_response(self, sync: int, source: int) -> bytes:
        step = self.scenario.spectrum_fragment_size
        offsets = list(range(0, 2048, step))
        if self.scenario.spectrum_reverse_order:
            offsets.reverse()
        return b"".join(
            build_spectrum_frame(
                sync,
                source=source,
                offset=offset,
                counts=tuple(offset + index for index in range(min(step, 2048 - offset))),
            )
            for offset in offsets
        )

    def _apply_write(self, command: int, request: bytes) -> None:
        if command == CMD_SET_STA:
            names = ("sensor_status", "volume_status", "led_screen_status", "auto_upload_status")
            for name, value in zip(names, request[3:7], strict=True):
                if value != 0xFF:
                    self.scenario.status_kwargs[name] = value
        elif command == CMD_SET_DEVICE_TIME:
            self.scenario.status_kwargs["device_time"] = struct.unpack_from("<I", request, 3)[0]
        elif command == CMD_SET_CLEAR:
            self.clear_requests.append((request[3], request[4], request[5]))


def unknown_frame(sync: int = 0, flag: int = 0xFE, payload: bytes = b"x") -> bytes:
    """Build a bounded unknown FLAG frame for parser tests."""
    return frame_bytes(sync, flag, payload)
