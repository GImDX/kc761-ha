"""Deterministic asyncio KC761 TCP simulator for protocol/HA tests.

The simulator is intentionally test-only.  It records commands so tests can
prove that an RC client never sends write operations or spectrum requests.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from custom_components.kc761.const import (
    CMD_GET_DEVICE_INFO,
    CMD_GET_STA,
    FORBIDDEN_WRITE_COMMANDS,
)
from custom_components.kc761.protocol.codec import (
    build_device_info_frame,
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
                request = await reader.readexactly(4)
                sync, command = request[0], request[1]
                self.received_syncs.append(sync)
                self.received_commands.append(command)
                if command in FORBIDDEN_WRITE_COMMANDS or command not in {
                    CMD_GET_STA,
                    CMD_GET_DEVICE_INFO,
                }:
                    self.forbidden_commands.append(command)
                    writer.close()
                    await writer.wait_closed()
                    return
                response = (
                    build_status_frame(
                        sync,
                        **self.scenario.status_kwargs,
                    )
                    if command == CMD_GET_STA
                    else build_device_info_frame(
                        sync,
                        model=self.scenario.model,
                        sensor_types=self.scenario.sensor_types,
                        device_id=self.scenario.device_id,
                    )
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


def unknown_frame(sync: int = 0, flag: int = 0xFE, payload: bytes = b"x") -> bytes:
    """Build a bounded unknown FLAG frame for parser tests."""
    return frame_bytes(sync, flag, payload)
