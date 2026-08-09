"""Async TCP transport for KC761."""

from __future__ import annotations

import asyncio

from ..const import CONNECT_TIMEOUT, READ_TIMEOUT


class TransportError(ConnectionError):
    """Transport failed or was closed."""


class TcpTransport:
    """Small asyncio transport with bounded connect/read/write operations."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        connect_timeout: float = CONNECT_TIMEOUT,
        read_timeout: float = READ_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None

    @property
    def connected(self) -> bool:
        return self.writer is not None and not self.writer.is_closing()

    async def async_connect(self) -> None:
        if self.connected:
            return
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.connect_timeout
            )
        except (OSError, TimeoutError) as err:
            self.reader = None
            self.writer = None
            raise TransportError(f"cannot connect to KC761 at {self.host}:{self.port}") from err

    async def async_read(self, size: int = 4096) -> bytes:
        if self.reader is None:
            raise TransportError("transport is not connected")
        try:
            return await asyncio.wait_for(self.reader.read(size), self.read_timeout)
        except (OSError, TimeoutError) as err:
            raise TransportError("KC761 read failed") from err

    async def async_write(self, data: bytes) -> None:
        if self.writer is None or self.writer.is_closing():
            raise TransportError("transport is not connected")
        try:
            self.writer.write(data)
            await asyncio.wait_for(self.writer.drain(), self.read_timeout)
        except (OSError, TimeoutError) as err:
            raise TransportError("KC761 write failed") from err

    async def async_close(self) -> None:
        writer, self.writer = self.writer, None
        self.reader = None
        if writer is None:
            return
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, asyncio.CancelledError):
            pass
