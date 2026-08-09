"""Incremental KC761 TCP frame parser."""

from __future__ import annotations

from collections import deque

from ..const import (
    FLAG_ACK,
    FLAG_CAL_DATA,
    FLAG_DEVICE_INFO,
    FLAG_MC_DATA_QUERY,
    FLAG_MC_DATA_UPLOAD,
    FLAG_STATUS_QUERY,
    FLAG_STATUS_UPLOAD,
    FLAG_STREAM_DATA,
    MAX_BUFFER_LENGTH,
    MAX_FRAME_LENGTH,
)
from .models import FrameError, RawFrame


FIXED_LENGTHS = {
    FLAG_STATUS_QUERY: 81,
    FLAG_STATUS_UPLOAD: 81,
    FLAG_DEVICE_INFO: 100,
    FLAG_CAL_DATA: 150,
    FLAG_ACK: 6,
}


class FrameParser:
    """Parse frames from arbitrary TCP chunks.

    The candidate profile treats ``BAG_LEN`` as the complete frame length,
    including the four-byte SYNC/FLAG/LEN header.  This interpretation is
    explicit and remains hardware-unverified until a real capture confirms it.
    """

    def __init__(
        self,
        *,
        max_frame_length: int = MAX_FRAME_LENGTH,
        max_buffer_length: int = MAX_BUFFER_LENGTH,
    ) -> None:
        self.max_frame_length = max_frame_length
        self.max_buffer_length = max_buffer_length
        self._buffer = bytearray()
        self.errors = 0
        self.dropped_bytes = 0
        self.recent_errors: deque[str] = deque(maxlen=20)

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def feed(self, data: bytes) -> list[RawFrame]:
        """Feed bytes and return all complete, bounded frames."""
        if data:
            self._buffer.extend(data)
        if len(self._buffer) > self.max_buffer_length:
            overflow = len(self._buffer) - self.max_buffer_length
            del self._buffer[:overflow]
            self._record_error(f"buffer_limit:{overflow}")

        frames: list[RawFrame] = []
        while len(self._buffer) >= 4:
            sync = self._buffer[0]
            flag = self._buffer[1]
            bag_len = int.from_bytes(self._buffer[2:4], "little")
            if bag_len < 4 or bag_len > self.max_frame_length:
                self._resync(f"length:{bag_len}")
                continue
            if len(self._buffer) < bag_len:
                break

            raw = bytes(self._buffer[:bag_len])
            expected = FIXED_LENGTHS.get(flag)
            if expected is not None and bag_len != expected:
                self._resync(f"fixed_length:{flag:#x}:{bag_len}!={expected}")
                continue
            if flag in (FLAG_MC_DATA_QUERY, FLAG_MC_DATA_UPLOAD):
                if bag_len < 9 or (bag_len - 9) % 2:
                    self._resync(f"mc_length:{bag_len}")
                    continue
            elif flag == FLAG_STREAM_DATA:
                if bag_len < 4 or (bag_len - 4) % 2:
                    self._resync(f"stream_length:{bag_len}")
                    continue

            del self._buffer[:bag_len]
            frames.append(
                RawFrame(
                    sync=sync,
                    flag=flag,
                    bag_len=bag_len,
                    payload=raw[4:],
                    raw=raw,
                    known=flag in FIXED_LENGTHS
                    or flag
                    in {
                        FLAG_MC_DATA_QUERY,
                        FLAG_MC_DATA_UPLOAD,
                        FLAG_STATUS_QUERY,
                        FLAG_STATUS_UPLOAD,
                        FLAG_STREAM_DATA,
                    },
                )
            )
        return frames

    def flush(self) -> None:
        """Drop an incomplete frame at connection close."""
        if self._buffer:
            self._record_error(f"incomplete:{len(self._buffer)}")
            self._buffer.clear()

    def _resync(self, reason: str) -> None:
        if self._buffer:
            del self._buffer[0]
            self.dropped_bytes += 1
        self._record_error(reason)

    def _record_error(self, reason: str) -> None:
        self.errors += 1
        self.recent_errors.append(reason)


def frame_bytes(sync: int, flag: int, payload: bytes = b"") -> bytes:
    """Build a candidate frame with a little-endian complete length."""
    bag_len = 4 + len(payload)
    if not 0 <= sync <= 0xFF or not 0 <= flag <= 0xFF:
        raise FrameError("sync/flag outside byte range")
    if bag_len > 0xFFFF:
        raise FrameError("frame exceeds uint16 length")
    return bytes((sync, flag)) + bag_len.to_bytes(2, "little") + payload
