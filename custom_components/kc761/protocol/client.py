"""Serialized read-only KC761 client and response router."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from ..const import (
    CMD_GET_DEVICE_INFO,
    CMD_GET_STA,
    FLAG_DEVICE_INFO,
    FLAG_MC_DATA_UPLOAD,
    FLAG_STATUS_QUERY,
    FLAG_STATUS_UPLOAD,
    FLAG_STREAM_DATA,
    FORBIDDEN_WRITE_COMMANDS,
    MAX_CONSECUTIVE_PROTOCOL_ERRORS,
    MAX_FRAME_LENGTH,
    READ_COMMANDS,
    REQUEST_TIMEOUT,
)
from .codec import DecodedFrame, decode_frame, encode_read_request
from .framing import FrameParser
from .models import DeviceInfo, RawFrame, StatusData, TransactionError
from .transport import TcpTransport, TransportError

PushCallback = Callable[[DecodedFrame], Awaitable[None] | None]
TaskCreator = Callable[[Coroutine[Any, Any, None], str], asyncio.Task[None]]
QUERY_RESPONSE_FLAGS = frozenset({FLAG_STATUS_QUERY, FLAG_DEVICE_INFO})
PUSH_FLAGS = frozenset({FLAG_MC_DATA_UPLOAD, FLAG_STATUS_UPLOAD, FLAG_STREAM_DATA})


def _asyncio_task_creator(coroutine: Coroutine[Any, Any, None], name: str) -> asyncio.Task[None]:
    return asyncio.create_task(coroutine, name=name)


class KC761Client:
    """One TCP connection with SYNC-matched read transactions."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        request_timeout: float = REQUEST_TIMEOUT,
        on_push: PushCallback | None = None,
        task_creator: TaskCreator = _asyncio_task_creator,
    ) -> None:
        self.host = host
        self.port = port
        self.request_timeout = request_timeout
        self.on_push = on_push
        self.task_creator = task_creator
        self.transport = TcpTransport(host, port)
        self.parser = FrameParser(max_frame_length=MAX_FRAME_LENGTH)
        self._reader_task: asyncio.Task[None] | None = None
        self._connection_lock = asyncio.Lock()
        self._pending: dict[int, tuple[frozenset[int], asyncio.Future[DecodedFrame]]] = {}
        self._retired: dict[int, float] = {}
        self._next_sync = 0
        self._closed = False
        self._parser_errors_seen = 0
        self.protocol_errors = 0
        self.data_errors = 0
        self.consecutive_protocol_errors = 0
        self.responses = 0
        self.push_frames = 0
        self.late_responses = 0
        self.unhandled_frames = 0
        self.last_frame: RawFrame | None = None

    @property
    def connected(self) -> bool:
        return self.transport.connected and self._reader_task is not None and not self._reader_task.done()

    async def async_connect(self) -> None:
        async with self._connection_lock:
            self._closed = False
            if self.connected:
                return
            stale_task, self._reader_task = self._reader_task, None
            if stale_task is not None and not stale_task.done():
                stale_task.cancel()
                try:
                    await stale_task
                except asyncio.CancelledError:
                    pass
            await self.transport.async_close()
            await self.transport.async_connect()
            self.consecutive_protocol_errors = 0
            self._reader_task = self.task_creator(self._reader_loop(), "kc761-reader")

    async def async_close(self) -> None:
        async with self._connection_lock:
            self._closed = True
            self._fail_pending(TransportError("KC761 client closed"))
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
        expected = frozenset({FLAG_STATUS_QUERY}) if command == CMD_GET_STA else frozenset({FLAG_DEVICE_INFO})
        loop = asyncio.get_running_loop()
        future: asyncio.Future[DecodedFrame] = loop.create_future()
        self._pending[sync] = (expected, future)
        try:
            await self.transport.async_write(encode_read_request(sync, command))
            return await asyncio.wait_for(future, self.request_timeout)
        except TimeoutError as err:
            await self.async_close()
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
                frames = self.parser.feed(chunk)
                self._capture_parser_errors()
                for frame in frames:
                    await self._route_frame(frame)
                if not frames and self.consecutive_protocol_errors >= MAX_CONSECUTIVE_PROTOCOL_ERRORS:
                    raise TransportError("too many consecutive KC761 protocol errors")
        except asyncio.CancelledError:
            raise
        except (TransportError, OSError):
            self._fail_pending(TransportError("KC761 connection lost"))
        except Exception:
            self._record_protocol_errors()
            self._fail_pending(TransportError("KC761 reader failed"))
        finally:
            self.parser.flush()
            self._capture_parser_errors()
            await self.transport.async_close()
            if self._reader_task is asyncio.current_task():
                self._reader_task = None

    async def _route_frame(self, frame: RawFrame) -> None:
        self.last_frame = frame
        try:
            decoded = decode_frame(frame)
        except Exception as err:
            self._record_protocol_errors()
            if self.consecutive_protocol_errors >= MAX_CONSECUTIVE_PROTOCOL_ERRORS:
                raise TransportError("too many consecutive KC761 decode errors") from err
            return
        self.consecutive_protocol_errors = 0
        if isinstance(decoded.data, StatusData):
            self.data_errors += len(decoded.data.validation_errors)
        pending = self._pending.get(frame.sync)
        if pending is not None and frame.flag in pending[0]:
            self.responses += 1
            if not pending[1].done():
                pending[1].set_result(decoded)
            return
        if frame.flag in QUERY_RESPONSE_FLAGS:
            self.late_responses += 1
            return
        if frame.flag in PUSH_FLAGS:
            self.push_frames += 1
            if self.on_push is not None:
                callback_result = self.on_push(decoded)
                if callback_result is not None:
                    await callback_result
            return
        self.unhandled_frames += 1

    def _capture_parser_errors(self) -> None:
        delta = self.parser.errors - self._parser_errors_seen
        if delta > 0:
            self._parser_errors_seen = self.parser.errors
            self._record_protocol_errors(delta)

    def _record_protocol_errors(self, count: int = 1) -> None:
        self.protocol_errors += count
        self.consecutive_protocol_errors += count

    def _fail_pending(self, error: Exception) -> None:
        for _, future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
