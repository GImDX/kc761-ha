"""Serialized KC761 client, multi-frame spectrum collector, and response router."""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from ..const import (
    CMD_GET_CAL_DATA,
    CMD_GET_DEVICE_INFO,
    CMD_GET_MC_DATA,
    CMD_GET_STA,
    FLAG_ACK,
    FLAG_CAL_DATA,
    FLAG_DEVICE_INFO,
    FLAG_MC_DATA_QUERY,
    FLAG_MC_DATA_UPLOAD,
    FLAG_STATUS_QUERY,
    FLAG_STATUS_UPLOAD,
    FLAG_STREAM_DATA,
    MAX_SPECTRUM_CHANNEL_COUNT,
    MAX_SPECTRUM_WIRE_SPAN,
    MAX_CONSECUTIVE_PROTOCOL_ERRORS,
    MAX_FRAME_LENGTH,
    READ_COMMANDS,
    REQUEST_TIMEOUT,
    SPECTRUM_CHANNEL_COUNT_OPTIONS,
    SPECTRUM_CHANNEL_COUNT,
    SPECTRUM_COMPLETION_BASIS_MAX_BOUND,
    SPECTRUM_COMPLETION_BASIS_PADDING_CANDIDATE,
    SPECTRUM_COMPLETION_BASIS_REQUEST_WINDOW,
    SPECTRUM_COMPLETION_BASIS_ROUND_BOUNDARY,
    SPECTRUM_LENGTH_CONFIDENCE_INFERRED,
    SPECTRUM_LENGTH_CONFIDENCE_OBSERVED,
    SPECTRUM_STATUS_COMPLETE,
)
from .codec import (
    DecodedFrame,
    decode_frame,
    encode_clear_request,
    encode_read_request,
    encode_set_device_time_request,
    encode_set_state_request,
    validate_write_request,
)
from .framing import FrameParser
from .models import (
    CalibrationData,
    CommandAck,
    DeviceInfo,
    RawFrame,
    SpectrumData,
    SpectrumSegment,
    StatusData,
    TransactionError,
)
from .transport import TcpTransport, Transport, TransportError

PushCallback = Callable[[DecodedFrame], Awaitable[None] | None]
TaskCreator = Callable[[Coroutine[Any, Any, None], str], asyncio.Task[None]]
QUERY_RESPONSE_FLAGS = frozenset(
    {FLAG_MC_DATA_QUERY, FLAG_STATUS_QUERY, FLAG_DEVICE_INFO, FLAG_CAL_DATA, FLAG_ACK}
)
PUSH_FLAGS = frozenset({FLAG_MC_DATA_UPLOAD, FLAG_STATUS_UPLOAD, FLAG_STREAM_DATA})
MAX_FRAME_FLAG_COUNTER_KEYS = 256


def _finalize_spectrum_buffer(
    values: list[int | None],
    *,
    source: int,
    segment_count: int,
    source_flag: int,
    padding_channels: set[int],
    completion_basis: str,
) -> SpectrumData | None:
    """Return a contiguous bounded result without inventing missing bins.

    The protocol has no total-channel field.  A legal trailing 0xFFFF pattern
    is therefore only a candidate boundary; it is never treated as proof that
    a real count was padding.  The caller records the resulting length as
    inferred unless the 4096-channel safety bound itself was reached.
    """
    first_gap = next((index for index, value in enumerate(values) if value is None), len(values))
    if first_gap == 0:
        return None
    if any(value is not None for value in values[first_gap:]):
        # A hole in the received span cannot be represented as a dense
        # SpectrumSnapshot.  Do not zero-fill it or publish a partial array.
        return None

    wire_end = first_gap
    candidate = wire_end
    padding_count = 0
    basis = completion_basis
    if candidate not in SPECTRUM_CHANNEL_COUNT_OPTIONS:
        for supported in reversed(SPECTRUM_CHANNEL_COUNT_OPTIONS):
            if supported >= candidate:
                continue
            suffix = range(supported, candidate)
            if all(index in padding_channels for index in suffix):
                candidate = supported
                padding_count = wire_end - supported
                basis = SPECTRUM_COMPLETION_BASIS_PADDING_CANDIDATE
                break
    if candidate not in SPECTRUM_CHANNEL_COUNT_OPTIONS:
        return None
    if any(values[index] is None for index in range(candidate)):
        return None

    observed_max = candidate == MAX_SPECTRUM_CHANNEL_COUNT and padding_count == 0
    return SpectrumData(
        source=source,
        counts=tuple(int(values[index]) for index in range(candidate)),
        segment_count=segment_count,
        source_flag=source_flag,
        spectrum_status=SPECTRUM_STATUS_COMPLETE,
        length_confidence=(
            SPECTRUM_LENGTH_CONFIDENCE_OBSERVED
            if observed_max
            else SPECTRUM_LENGTH_CONFIDENCE_INFERRED
        ),
        completion_basis=(
            SPECTRUM_COMPLETION_BASIS_MAX_BOUND if observed_max else basis
        ),
        received_count=candidate,
        received_span=wire_end,
        padding_count=padding_count,
    )


@dataclass(slots=True)
class PendingTransaction:
    """One serialized transaction, optionally collecting spectrum fragments."""

    command: int
    expected_flags: frozenset[int]
    future: asyncio.Future[DecodedFrame | SpectrumData]
    spectrum_source: int | None = None
    spectrum_channel_count: int = SPECTRUM_CHANNEL_COUNT
    adaptive_spectrum: bool = False
    spectrum_counts: list[int | None] = field(
        default_factory=lambda: [None] * SPECTRUM_CHANNEL_COUNT
    )
    spectrum_filled: int = 0
    spectrum_segments: int = 0
    padding_channels: set[int] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.command == CMD_GET_MC_DATA:
            if self.adaptive_spectrum:
                self.spectrum_channel_count = MAX_SPECTRUM_WIRE_SPAN
            elif self.spectrum_channel_count not in SPECTRUM_CHANNEL_COUNT_OPTIONS:
                raise ValueError("unsupported spectrum channel count")
            self.spectrum_counts = [None] * self.spectrum_channel_count

    def add_spectrum_segment(self, segment: SpectrumSegment) -> SpectrumData | None:
        """Add one fragment, finalizing only after a bound or window boundary.

        A 4096-bin prefix is not emitted immediately: N=228/N=86 packet
        layouts may legally append a bounded padding suffix.  Waiting until
        the request window (or until such a suffix arrives) preserves that
        evidence without imposing an unbounded wait.
        """
        if self.spectrum_source is None or segment.source != self.spectrum_source:
            raise TransactionError("spectrum source does not match request")
        if segment.offset < 0 or segment.offset >= self.spectrum_channel_count:
            raise TransactionError("spectrum segment starts outside supported range")
        if segment.offset + len(segment.counts) > self.spectrum_channel_count:
            raise TransactionError("spectrum segment exceeds configured channel count")
        if not segment.counts:
            raise TransactionError("spectrum segment is empty")
        added_new_channel = False
        for relative_index, count in enumerate(segment.counts):
            channel = segment.offset + relative_index
            existing = self.spectrum_counts[channel]
            if existing is None:
                self.spectrum_counts[channel] = count
                self.spectrum_filled += 1
                added_new_channel = True
                if count == 0xFFFF * segment.ratio:
                    self.padding_channels.add(channel)
            elif existing != count:
                raise TransactionError("overlapping spectrum fragments disagree")
        if added_new_channel:
            self.spectrum_segments += 1
        if not self.adaptive_spectrum:
            if self.spectrum_filled < self.spectrum_channel_count:
                return None
            return SpectrumData(
                source=self.spectrum_source,
                counts=tuple(int(count) for count in self.spectrum_counts),
                segment_count=self.spectrum_segments,
                source_flag=FLAG_MC_DATA_QUERY,
            )
        if self.spectrum_filled <= MAX_SPECTRUM_CHANNEL_COUNT:
            return None
        result = _finalize_spectrum_buffer(
            self.spectrum_counts,
            source=self.spectrum_source,
            segment_count=self.spectrum_segments,
            source_flag=FLAG_MC_DATA_QUERY,
            padding_channels=self.padding_channels,
            completion_basis=SPECTRUM_COMPLETION_BASIS_MAX_BOUND,
        )
        if result is None:
            raise TransactionError("spectrum reached bound with a missing channel")
        return result

    def finish_spectrum_window(self) -> SpectrumData | None:
        """Finalize an adaptive A0 response at the bounded request window."""
        if not self.adaptive_spectrum or self.spectrum_source is None:
            return None
        return _finalize_spectrum_buffer(
            self.spectrum_counts,
            source=self.spectrum_source,
            segment_count=self.spectrum_segments,
            source_flag=FLAG_MC_DATA_QUERY,
            padding_channels=self.padding_channels,
            completion_basis=SPECTRUM_COMPLETION_BASIS_REQUEST_WINDOW,
        )


@dataclass(slots=True)
class SpectrumUploadAssembler:
    """Bounded assembler for one A3/A1 automatic-upload spectrum round.

    The retained ``channel_counts`` argument is compatibility-only.  Runtime
    target lengths are discovered from the current round and are never forced
    by an OptionsFlow value.
    """

    channel_counts: tuple[int, int, int] | None = None
    channel_count: int = field(init=False, default=MAX_SPECTRUM_WIRE_SPAN)
    source: int | None = None
    counts: list[int | None] = field(default_factory=list)
    segment_count: int = 0
    filled: int = 0
    complete: bool = False
    padding_channels: set[int] = field(default_factory=set)
    round_id: int = 0
    last_result: SpectrumData | None = None
    last_issue: str | None = None

    def __post_init__(self) -> None:
        # Accept old constructor arguments so existing callers can migrate,
        # but intentionally do not use them as a target length.
        if self.channel_counts is not None and len(self.channel_counts) != 3:
            raise ValueError("spectrum channel counts must contain three entries")
        self.channel_count = MAX_SPECTRUM_WIRE_SPAN
        self.counts = [None] * self.channel_count

    def begin_round(self, source: int) -> SpectrumData | None:
        """Close the prior A3 round, then start a new source-isolated round."""
        previous = self.finish_round()
        self.reset()
        self.round_id += 1
        if 0 <= source <= 2:
            self.source = source
        return previous

    def reset(self) -> None:
        """Discard all partial data without retaining an unbounded fragment log."""
        self.source = None
        self.channel_count = MAX_SPECTRUM_WIRE_SPAN
        self.counts = [None] * self.channel_count
        self.segment_count = 0
        self.filled = 0
        self.complete = False
        self.padding_channels.clear()

    def add(self, segment: SpectrumSegment) -> SpectrumData | None:
        """Add one A1 segment, tolerating reordering and exact duplicates."""
        if self.complete or self.source is None:
            return None
        if segment.source_flag != FLAG_MC_DATA_UPLOAD or segment.source != self.source:
            return None
        if segment.offset < 0 or segment.offset >= self.channel_count:
            self.last_issue = "segment_out_of_bounds"
            self.reset()
            return None
        if segment.offset + len(segment.counts) > self.channel_count:
            self.last_issue = "segment_out_of_bounds"
            self.reset()
            return None
        if not segment.counts:
            self.last_issue = "empty_segment"
            self.reset()
            return None
        end = segment.offset + len(segment.counts)
        if end <= segment.offset:
            return None
        for channel in range(segment.offset, end):
            existing = self.counts[channel]
            incoming = segment.counts[channel - segment.offset]
            if existing is not None and existing != incoming:
                self.last_issue = "overlapping_segment_conflict"
                self.reset()
                return None
        added_new_channel = False
        for channel in range(segment.offset, end):
            if self.counts[channel] is None:
                added_new_channel = True
                self.counts[channel] = segment.counts[channel - segment.offset]
                self.filled += 1
                if segment.counts[channel - segment.offset] == 0xFFFF * segment.ratio:
                    self.padding_channels.add(channel)
        if added_new_channel:
            self.segment_count += 1
        if self.filled <= MAX_SPECTRUM_CHANNEL_COUNT:
            return None
        result = _finalize_spectrum_buffer(
            self.counts,
            source=self.source,
            segment_count=self.segment_count,
            source_flag=FLAG_MC_DATA_UPLOAD,
            padding_channels=self.padding_channels,
            completion_basis=SPECTRUM_COMPLETION_BASIS_MAX_BOUND,
        )
        if result is None:
            self.last_issue = "bound_reached_with_gap"
            self.reset()
            return None
        self.complete = True
        self.last_result = result
        return result

    def finish_round(self) -> SpectrumData | None:
        """Finalize a round at its explicit A3 boundary."""
        if self.source is None or self.filled == 0 or self.complete:
            return None
        result = _finalize_spectrum_buffer(
            self.counts,
            source=self.source,
            segment_count=self.segment_count,
            source_flag=FLAG_MC_DATA_UPLOAD,
            padding_channels=self.padding_channels,
            completion_basis=SPECTRUM_COMPLETION_BASIS_ROUND_BOUNDARY,
        )
        if result is None:
            self.last_issue = "round_incomplete_or_noncontiguous"
            return None
        self.last_result = result
        self.complete = True
        return result

    def state(self) -> dict[str, int | str | None]:
        """Return bounded assembler state for diagnostics."""
        span = next(
            (index for index in range(len(self.counts) - 1, -1, -1) if self.counts[index] is not None),
            -1,
        )
        return {
            "round_id": self.round_id,
            "source": self.source,
            "filled": self.filled,
            "received_span": span + 1,
            "segment_count": self.segment_count,
            "last_issue": self.last_issue,
        }


def _asyncio_task_creator(coroutine: Coroutine[Any, Any, None], name: str) -> asyncio.Task[None]:
    return asyncio.create_task(coroutine, name=name)


class KC761Client:
    """One byte-stream connection with SYNC-matched read transactions."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        transport: Transport | None = None,
        request_timeout: float = REQUEST_TIMEOUT,
        on_push: PushCallback | None = None,
        task_creator: TaskCreator = _asyncio_task_creator,
        spectrum_channel_counts: tuple[int, int, int] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.request_timeout = request_timeout
        self.on_push = on_push
        self.task_creator = task_creator
        if spectrum_channel_counts is not None and len(spectrum_channel_counts) != 3:
            raise ValueError("spectrum channel counts must contain three entries")
        # Retain the public attribute for diagnostics/older callers, but do
        # not use persisted values to choose the runtime target length.
        self.spectrum_channel_counts = (MAX_SPECTRUM_CHANNEL_COUNT,) * 3
        self.transport: Transport = transport or TcpTransport(host, port)
        self.parser = FrameParser(max_frame_length=MAX_FRAME_LENGTH)
        self._reader_task: asyncio.Task[None] | None = None
        self._connection_lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()
        self._pending: dict[int, PendingTransaction] = {}
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
        self._client_instance_token = secrets.token_hex(16)
        # Keep only bounded, payload-free receive evidence.  This counter is
        # intentionally lifetime-scoped: reconnects and diagnostics reads do
        # not clear it.  A new KC761Client starts a new counter scope.
        self._frame_flag_counts: dict[int, int] = {}

    @property
    def connected(self) -> bool:
        return self.transport.connected and self._reader_task is not None and not self._reader_task.done()

    @property
    def received_frame_flags(self) -> dict[int, int]:
        """Return an isolated snapshot of complete frames routed by FLAG."""
        return dict(self._frame_flag_counts)

    @property
    def client_instance_token(self) -> str:
        """Return the ephemeral token identifying this client lifetime only."""
        return self._client_instance_token

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

    async def async_request(self, command: int, *, source: int | None = None) -> DecodedFrame | SpectrumData:
        """Send one supported read command and await its matching response."""
        if command not in READ_COMMANDS:
            raise ValueError(f"unsupported command: {command:#x}")
        if command == CMD_GET_MC_DATA and (source is None or not 0 <= source <= 2):
            raise ValueError("spectrum source must be in 0..2")
        if command != CMD_GET_MC_DATA and source is not None:
            raise ValueError("source is only valid for spectrum reads")
        async with self._request_lock:
            return await self._async_request_locked(command, source=source)

    async def _async_request_locked(
        self, command: int, *, source: int | None = None
    ) -> DecodedFrame | SpectrumData:
        """Execute one read while holding the per-connection request lock."""
        if not self.connected:
            await self.async_connect()
        sync = self._allocate_sync()
        expected = {
            CMD_GET_MC_DATA: frozenset({FLAG_MC_DATA_QUERY}),
            CMD_GET_STA: frozenset({FLAG_STATUS_QUERY}),
            CMD_GET_DEVICE_INFO: frozenset({FLAG_DEVICE_INFO}),
            CMD_GET_CAL_DATA: frozenset({FLAG_CAL_DATA}),
        }[command]
        loop = asyncio.get_running_loop()
        future: asyncio.Future[DecodedFrame | SpectrumData] = loop.create_future()
        channel_count = (
            MAX_SPECTRUM_WIRE_SPAN
            if command == CMD_GET_MC_DATA and source is not None
            else SPECTRUM_CHANNEL_COUNT
        )
        self._pending[sync] = PendingTransaction(
            command,
            expected,
            future,
            source,
            channel_count,
            command == CMD_GET_MC_DATA,
        )
        try:
            await self.transport.async_write(encode_read_request(sync, command, source=source))
            return await asyncio.wait_for(asyncio.shield(future), self.request_timeout)
        except TimeoutError as err:
            if command == CMD_GET_MC_DATA:
                adaptive_result = self._pending[sync].finish_spectrum_window()
                if adaptive_result is not None:
                    self.responses += 1
                    return adaptive_result
            await self.async_close()
            raise TimeoutError(f"KC761 command {command:#x} timed out") from err
        finally:
            self._pending.pop(sync, None)
            self._retired[sync] = time.monotonic() + self.request_timeout

    async def async_get_status(self) -> StatusData:
        result = await self.async_request(CMD_GET_STA)
        if not isinstance(result, DecodedFrame) or not isinstance(result.data, StatusData):
            raise TransactionError("status response did not decode")
        return result.data

    async def async_get_device_info(self) -> DeviceInfo:
        result = await self.async_request(CMD_GET_DEVICE_INFO)
        if not isinstance(result, DecodedFrame) or not isinstance(result.data, DeviceInfo):
            raise TransactionError("device info response did not decode")
        return result.data

    async def async_get_calibration(self) -> CalibrationData:
        result = await self.async_request(CMD_GET_CAL_DATA)
        if not isinstance(result, DecodedFrame) or not isinstance(result.data, CalibrationData):
            raise TransactionError("calibration response did not decode")
        return result.data

    async def async_get_spectrum(self, source: int) -> SpectrumData:
        result = await self.async_request(CMD_GET_MC_DATA, source=source)
        if not isinstance(result, SpectrumData):
            raise TransactionError("spectrum response did not complete")
        return result

    async def async_set_state(
        self,
        *,
        sensor_status: int = 0xFF,
        volume_status: int = 0xFF,
        led_screen_status: int = 0xFF,
        auto_upload_status: int = 0xFF,
    ) -> CommandAck:
        return await self._async_write(
            lambda sync: encode_set_state_request(
                sync,
                sensor_status=sensor_status,
                volume_status=volume_status,
                led_screen_status=led_screen_status,
                auto_upload_status=auto_upload_status,
            )
        )

    async def async_set_device_time(self, epoch: int) -> CommandAck:
        return await self._async_write(lambda sync: encode_set_device_time_request(sync, epoch))

    async def async_clear_statistics(
        self,
        *,
        spectrum_mask: int = 0,
        dose_curve_mask: int = 0,
        cumulative_dose_mask: int = 0,
    ) -> CommandAck:
        return await self._async_write(
            lambda sync: encode_clear_request(
                sync,
                spectrum_mask=spectrum_mask,
                dose_curve_mask=dose_curve_mask,
                cumulative_dose_mask=cumulative_dose_mask,
            )
        )

    async def _async_write(self, request_builder: Callable[[int], bytes]) -> CommandAck:
        """Serialize one supported write, validate its AA response, and return it."""
        async with self._request_lock:
            if not self.connected:
                await self.async_connect()
            sync = self._allocate_sync()
            request = request_builder(sync)
            command = validate_write_request(request)
            loop = asyncio.get_running_loop()
            future: asyncio.Future[DecodedFrame | SpectrumData] = loop.create_future()
            self._pending[sync] = PendingTransaction(command, frozenset({FLAG_ACK}), future)
            try:
                await self.transport.async_write(request)
                result = await asyncio.wait_for(future, self.request_timeout)
            except TimeoutError as err:
                await self.async_close()
                raise TimeoutError(f"KC761 command {command:#x} timed out") from err
            finally:
                self._pending.pop(sync, None)
                self._retired[sync] = time.monotonic() + self.request_timeout
            if not isinstance(result, DecodedFrame) or not isinstance(result.data, CommandAck):
                raise TransactionError("write response did not decode as ACK")
            if not result.data.successful:
                raise TransactionError(f"KC761 command {command:#x} failed")
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
        self._record_frame_flag(frame.flag)
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
        if pending is not None and frame.flag in pending.expected_flags:
            try:
                if isinstance(decoded.data, SpectrumSegment):
                    completed = pending.add_spectrum_segment(decoded.data)
                    if completed is None:
                        return
                    result: DecodedFrame | SpectrumData = completed
                else:
                    if isinstance(decoded.data, CommandAck) and decoded.data.command != pending.command:
                        raise TransactionError("ACK command does not match request")
                    result = decoded
            except TransactionError as err:
                self._record_protocol_errors()
                if not pending.future.done():
                    pending.future.set_exception(err)
                return
            self.responses += 1
            if not pending.future.done():
                pending.future.set_result(result)
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

    def _record_frame_flag(self, flag: int) -> None:
        """Count one complete routed frame without retaining payload or ID data."""
        if isinstance(flag, bool) or not isinstance(flag, int) or not 0 <= flag <= 0xFF:
            return
        if flag not in self._frame_flag_counts and len(self._frame_flag_counts) >= MAX_FRAME_FLAG_COUNTER_KEYS:
            return
        self._frame_flag_counts[flag] = self._frame_flag_counts.get(flag, 0) + 1

    def _capture_parser_errors(self) -> None:
        delta = self.parser.errors - self._parser_errors_seen
        if delta > 0:
            self._parser_errors_seen = self.parser.errors
            self._record_protocol_errors(delta)

    def _record_protocol_errors(self, count: int = 1) -> None:
        self.protocol_errors += count
        self.consecutive_protocol_errors += count

    def _fail_pending(self, error: Exception) -> None:
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.set_exception(error)
        self._pending.clear()
