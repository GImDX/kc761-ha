"""Serialized read-only KC761 client and response router."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from ..const import (
    CMD_GET_DEVICE_INFO,
    CMD_GET_STA,
    FLAG_DEVICE_INFO,
    FLAG_STATUS_QUERY,
    FORBIDDEN_WRITE_COMMANDS,
    MAX_FRAME_LENGTH,
    READ_COMMANDS,
)
from .codec import DecodedFrame, decode_frame, encode_read_request
from .framing import FrameParser
from .models import DeviceInfo, RawFrame, StatusData, TransactionError
from .transport import TcpTransport, TransportError

PushCallback = Callable[[DecodedFrame], Awaitable[None] | None]


class KC761Client:
    """One TCP connection with SYNC-matched read transactions."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        request_timeout: float = 8.0,
        on_push: PushCallback | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.request_timeout = request_timeout
        self.on_push = on_push
        self.transport = TcpTransport(host, port, read_timeout=request_timeout)
        self.parser = FrameParser(max_frame_length=MAX_FRAME_LENGTH)
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[int, tuple[frozenset[int], asyncio.Future[DecodedFrame]]] = {}
        self._retired: dict[int, float] = {}
        self._next_sync = 0
        self._closed = False
        self.protocol_errors = 0
        self.responses = 0
        self.push_frames = 0
        self.last_frame: RawFrame | None = None

    @property
    def connected(self) -> bool:
        return self.transport.connected

    async def async_connect(self) -> None:
        self._closed = False
        await self.transport.async_connect()
        self._reader_task = asyncio.create_task(self._reader_loop(), name="kc761-reader")

    async def async_close(self) -> None:
        self._closed = True
        for _, future in self._pending.values():
            if not future.done():
                future.set_exception(TransportError("KC761 client closed"))
        self._pending.clear()
        task, self._reader_task = self._reader_task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self.transport.async_close()

    async def async_request(self, command: int) -> DecodedFrame:
        """Send one supported read command and await its matching response."""
        if command in FORBIDDEN_WRITE_COMMANDS:
            raise ValueError(f"write command blocked by read-only RC: {command:#x}")
        if command not in READ_COMMANDS:
            raise ValueError(f"unsupported command: {command:#x}")
        if not self.connected:
            await self.async_connect()
        sync = self._allocate_sync()
        expected = (
            frozenset({FLAG_STATUS_QUERY})
            if command == CMD_GET_STA
            else frozenset({FLAG_DEVICE_INFO})
        )
        loop = asyncio.get_running_loop()
        future: asyncio.Future[DecodedFrame] = loop.create_future()
        self._pending[sync] = (expected, future)
        try:
            await self.transport.async_write(encode_read_request(sync, command))
            return await asyncio.wait_for(future, self.request_timeout)
        except TimeoutError as err:
            raise TimeoutError(f"KC761 command {command:#x} timed out") from err
        finally:
            self._pending.pop(sync, None)
            self._retired[sync] = time.monotonic() + self.request_timeout

    async def async_get_status(self) -> StatusData:
        result = await self.async_request(CMD_GET_STA)
        if not isinstance(result.data, StatusData):
            raise TransactionError("status response did not decode")
        return result.data

    async def async_get_device_info(self) -> DeviceInfo:
        result = await self.async_request(CMD_GET_DEVICE_INFO)
        if not isinstance(result.data, DeviceInfo):
            raise TransactionError("device info response did not decode")
        return result.data

    def _allocate_sync(self) -> int:
        now = time.monotonic()
        self._retired = {key: expiry for key, expiry in self._retired.items() if expiry > now}
        for _ in range(256):
            sync = self._next_sync
            self._next_sync = (self._next_sync + 1) & 0xFF
            if sync not in self._pending and sync not in self._retired:
                return sync
        raise TransactionError("all SYNC values are currently reserved")

    async def _reader_loop(self) -> None:
        try:
            while not self._closed:
                chunk = await self.transport.async_read()
                if not chunk:
                    raise TransportError("KC761 closed the TCP connection")
                for frame in self.parser.feed(chunk):
                    await self._route_frame(frame)
        except asyncio.CancelledError:
            raise
        except (TransportError, OSError):
            for _, future in self._pending.values():
                if not future.done():
                    future.set_exception(TransportError("KC761 connection lost"))
        finally:
            self.parser.flush()

    async def _route_frame(self, frame: RawFrame) -> None:
        self.last_frame = frame
        try:
            decoded = decode_frame(frame)
        except Exception:
            self.protocol_errors += 1
            return
        pending = self._pending.get(frame.sync)
        if pending is not None and frame.flag in pending[0]:
            self.responses += 1
            if not pending[1].done():
                pending[1].set_result(decoded)
            return
        self.push_frames += 1
        if self.on_push is not None:
            callback_result = self.on_push(decoded)
            if callback_result is not None:
                await callback_result
