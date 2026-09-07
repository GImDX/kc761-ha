"""Structured KC761 spectrum snapshots and bounded gzip archive."""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from tempfile import mkstemp
from threading import RLock
from typing import Any

from .const import (
    SPECTRUM_COMPLETION_BASIS_LEGACY,
    SPECTRUM_COMPLETION_BASIS_MAX_BOUND,
    SPECTRUM_LENGTH_CONFIDENCE_LEGACY,
    SPECTRUM_LENGTH_CONFIDENCE_OBSERVED,
    SPECTRUM_STATUS_COMPLETE,
)
from .const import CHANNEL_KEYS, MAX_SAVED_SPECTRA, SPECTRUM_CHANNEL_COUNTS

_SAFE_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-2]-[0-9a-f]{12}$")
_ARCHIVE_SUFFIX = ".json.gz"
_INDEX_FILENAME = ".metadata.json"
_INDEX_VERSION = 1
_ARCHIVE_ERRORS = (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError)


def _legacy_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Add explicit legacy provenance to pre-rc7 archive index entries."""
    normalized = dict(metadata)
    normalized.setdefault("spectrum_status", SPECTRUM_STATUS_COMPLETE)
    normalized.setdefault("length_confidence", SPECTRUM_LENGTH_CONFIDENCE_LEGACY)
    normalized.setdefault("completion_basis", SPECTRUM_COMPLETION_BASIS_LEGACY)
    normalized.setdefault("received_count", normalized.get("point_count"))
    normalized.setdefault("received_span", normalized.get("point_count"))
    normalized.setdefault("padding_count", 0)
    return normalized


@dataclass(frozen=True, slots=True)
class SpectrumSnapshot:
    """One complete spectrum plus its energy labels and acquisition metadata."""

    snapshot_id: str
    source: int
    acquired_at: datetime
    accumulation_seconds: int | None
    counts: tuple[int, ...]
    energy_kev: tuple[float, ...]
    segment_count: int
    calibration_profile: str
    spectrum_status: str = SPECTRUM_STATUS_COMPLETE
    length_confidence: str = SPECTRUM_LENGTH_CONFIDENCE_OBSERVED
    completion_basis: str = SPECTRUM_COMPLETION_BASIS_MAX_BOUND
    received_count: int | None = None
    received_span: int | None = None
    padding_count: int = 0
    _total_counts: int = field(init=False, repr=False, compare=False)
    _metadata: dict[str, Any] = field(init=False, repr=False, compare=False)
    _entity_attributes: dict[str, Any] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Build immutable-snapshot payloads once instead of per state update."""
        total_counts = sum(self.counts)
        received_count = len(self.counts) if self.received_count is None else self.received_count
        received_span = len(self.counts) if self.received_span is None else self.received_span
        metadata = {
            "snapshot_id": self.snapshot_id,
            "source": self.source,
            "channel": self.channel_name,
            "acquired_at": self.acquired_at.isoformat(),
            "accumulation_seconds": self.accumulation_seconds,
            "point_count": len(self.counts),
            "total_counts": total_counts,
            "segment_count": self.segment_count,
            "calibration_profile": self.calibration_profile,
            "spectrum_status": self.spectrum_status,
            "length_confidence": self.length_confidence,
            "completion_basis": self.completion_basis,
            "received_count": received_count,
            "received_span": received_span,
            "padding_count": self.padding_count,
        }
        points = [
            [channel, round(self.energy_kev[channel], 8), self.counts[channel]]
            for channel in range(len(self.counts))
        ]
        # Persist the effective values on the immutable object as well as in
        # the derived metadata.  This keeps a newly-created snapshot equal to
        # the same snapshot after a storage round-trip where the lengths are
        # explicitly present.
        object.__setattr__(self, "received_count", received_count)
        object.__setattr__(self, "received_span", received_span)
        object.__setattr__(self, "_total_counts", total_counts)
        object.__setattr__(self, "_metadata", metadata)
        object.__setattr__(self, "_entity_attributes", {**metadata, "points": points})

    @classmethod
    def create(
        cls,
        *,
        source: int,
        acquired_at: datetime,
        accumulation_seconds: int | None,
        counts: tuple[int, ...],
        energy_kev: tuple[float, ...],
        segment_count: int,
        calibration_profile: str,
        spectrum_status: str = SPECTRUM_STATUS_COMPLETE,
        length_confidence: str = SPECTRUM_LENGTH_CONFIDENCE_OBSERVED,
        completion_basis: str = SPECTRUM_COMPLETION_BASIS_MAX_BOUND,
        received_count: int | None = None,
        received_span: int | None = None,
        padding_count: int = 0,
    ) -> SpectrumSnapshot:
        """Validate fields and create a content-addressed archive identifier."""
        if not 0 <= source <= 2:
            raise ValueError("spectrum source outside 0..2")
        if len(counts) not in SPECTRUM_CHANNEL_COUNTS or len(energy_kev) != len(counts):
            raise ValueError("spectrum must contain 1024, 2048, or 4096 count and energy values")
        if any(count < 0 for count in counts):
            raise ValueError("spectrum counts must be non-negative")
        if acquired_at.tzinfo is None:
            raise ValueError("spectrum acquisition time must be timezone-aware")
        acquired_at = acquired_at.astimezone(UTC)
        digest = hashlib.sha256()
        digest.update(bytes((source,)))
        digest.update(acquired_at.isoformat().encode())
        for count in counts:
            digest.update(int(count).to_bytes(8, "little"))
        snapshot_id = f"{acquired_at:%Y%m%dT%H%M%SZ}-{source}-{digest.hexdigest()[:12]}"
        return cls(
            snapshot_id=snapshot_id,
            source=source,
            acquired_at=acquired_at,
            accumulation_seconds=accumulation_seconds,
            counts=counts,
            energy_kev=energy_kev,
            segment_count=segment_count,
            calibration_profile=calibration_profile,
            spectrum_status=spectrum_status,
            length_confidence=length_confidence,
            completion_basis=completion_basis,
            received_count=received_count,
            received_span=received_span,
            padding_count=padding_count,
        )

    @property
    def total_counts(self) -> int:
        return self._total_counts

    @property
    def channel_name(self) -> str:
        return CHANNEL_KEYS[self.source]

    def metadata(self) -> dict[str, Any]:
        """Return bounded metadata suitable for history lists and Recorder."""
        return dict(self._metadata)

    def entity_attributes(self) -> dict[str, Any]:
        """Return one cached structured entity payload; points are excluded from Recorder.

        A snapshot is immutable and Home Assistant treats the returned state
        attributes as read-only.  Keeping this object cached avoids rebuilding
        one point triple for every coordinator state write.
        """
        return self._entity_attributes

    def to_storage(self) -> dict[str, Any]:
        return {
            **self.metadata(),
            "counts": list(self.counts),
            "energy_kev": list(self.energy_kev),
        }

    @classmethod
    def from_storage(cls, value: dict[str, Any]) -> SpectrumSnapshot:
        snapshot_id = str(value["snapshot_id"])
        if not _SAFE_ID.fullmatch(snapshot_id):
            raise ValueError("invalid spectrum snapshot ID")
        source = int(value["source"])
        if not 0 <= source <= 2:
            raise ValueError("stored spectrum source outside 0..2")
        counts = tuple(int(item) for item in value["counts"])
        energy_kev = tuple(float(item) for item in value["energy_kev"])
        if len(counts) not in SPECTRUM_CHANNEL_COUNTS or len(energy_kev) != len(counts):
            raise ValueError("stored spectrum has an invalid length")
        stored_point_count = value.get("point_count")
        if stored_point_count is not None and (
            not isinstance(stored_point_count, int) or stored_point_count != len(counts)
        ):
            raise ValueError("stored spectrum point count does not match payload")
        snapshot = cls(
            snapshot_id=snapshot_id,
            source=source,
            acquired_at=datetime.fromisoformat(str(value["acquired_at"])).astimezone(UTC),
            accumulation_seconds=(
                None if value.get("accumulation_seconds") is None else int(value["accumulation_seconds"])
            ),
            counts=counts,
            energy_kev=energy_kev,
            segment_count=int(value["segment_count"]),
            calibration_profile=str(value["calibration_profile"]),
            spectrum_status=str(value.get("spectrum_status", SPECTRUM_STATUS_COMPLETE)),
            length_confidence=str(
                value.get("length_confidence", SPECTRUM_LENGTH_CONFIDENCE_LEGACY)
            ),
            completion_basis=str(value.get("completion_basis", SPECTRUM_COMPLETION_BASIS_LEGACY)),
            received_count=(
                None if value.get("received_count") is None else int(value["received_count"])
            ),
            received_span=(None if value.get("received_span") is None else int(value["received_span"])),
            padding_count=int(value.get("padding_count", 0)),
        )
        return snapshot

    def to_csv(self) -> str:
        """Return an RFC 4180-compatible CSV with complete spectrum metadata."""
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\r\n")
        writer.writerow(
            (
                "source",
                "channel_name",
                "acquired_at",
                "accumulation_seconds",
                "channel_address",
                "energy_kev",
                "counts",
                "spectrum_status",
                "length_confidence",
                "completion_basis",
                "received_count",
                "received_span",
                "padding_count",
            )
        )
        acquired_at = self.acquired_at.isoformat()
        for channel, (energy, counts) in enumerate(zip(self.energy_kev, self.counts, strict=True)):
            writer.writerow(
                (
                    self.source,
                    self.channel_name,
                    acquired_at,
                    self.accumulation_seconds if self.accumulation_seconds is not None else "",
                    channel,
                    f"{energy:.8f}",
                    counts,
                    self.spectrum_status,
                    self.length_confidence,
                    self.completion_basis,
                    self.received_count if self.received_count is not None else len(self.counts),
                    self.received_span if self.received_span is not None else len(self.counts),
                    self.padding_count,
                )
            )
        return output.getvalue()


class SpectrumArchive:
    """A bounded per-config-entry archive outside Home Assistant Recorder."""

    def __init__(self, directory: Path, *, maximum: int = MAX_SAVED_SPECTRA) -> None:
        self.directory = directory
        self.maximum = maximum
        self._index: dict[str, dict[str, Any]] | None = None
        self._index_dirty = False
        self._lock = RLock()

    def save(self, snapshot: SpectrumSnapshot) -> None:
        """Atomically save gzip data; a sidecar failure is retried without hiding data."""
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            index = self._ensure_index()
            destination = self._path(snapshot.snapshot_id)
            temporary_fd, temporary_name = mkstemp(
                prefix=f".{destination.name}.", suffix=".tmp", dir=self.directory
            )
            os.close(temporary_fd)
            temporary = Path(temporary_name)
            try:
                with gzip.open(temporary, "wt", encoding="utf-8", newline="\n") as handle:
                    json.dump(snapshot.to_storage(), handle, ensure_ascii=False, separators=(",", ":"))
                os.replace(temporary, destination)
            finally:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
            index[snapshot.snapshot_id] = self._index_entry(destination, snapshot.metadata())
            self._prune(index)
            self._persist_index(index)

    def list_metadata(self) -> list[dict[str, Any]]:
        """Return newest-first metadata while skipping unreadable private files."""
        with self._lock:
            if not self.directory.exists():
                self._index = {}
                return []
            index = self._ensure_index()
            changed = self._refresh_index(index)
            if changed or self._index_dirty:
                self._persist_index(index)
            return [
                _legacy_metadata(index[snapshot_id]["metadata"])
                for snapshot_id in sorted(index, reverse=True)
            ]

    def load(self, snapshot_id: str) -> SpectrumSnapshot:
        with self._lock:
            return self._load_path(self._path(snapshot_id))

    def _path(self, snapshot_id: str) -> Path:
        if not _SAFE_ID.fullmatch(snapshot_id):
            raise ValueError("invalid spectrum snapshot ID")
        return self.directory / f"{snapshot_id}.json.gz"

    @staticmethod
    def _load_path(path: Path) -> SpectrumSnapshot:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise ValueError("stored spectrum root is not an object")
        return SpectrumSnapshot.from_storage(value)

    @property
    def _index_path(self) -> Path:
        return self.directory / _INDEX_FILENAME

    def _ensure_index(self) -> dict[str, dict[str, Any]]:
        if self._index is not None:
            return self._index
        index = self._read_index()
        rebuilt = index is None
        if index is None:
            index = self._rebuild_index()
        self._index = index
        if rebuilt:
            self._persist_index(index)
        return index

    def _read_index(self) -> dict[str, dict[str, Any]] | None:
        try:
            with self._index_path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, TypeError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict) or value.get("version") != _INDEX_VERSION:
            return None
        entries = value.get("entries")
        if not isinstance(entries, dict):
            return None
        normalized: dict[str, dict[str, Any]] = {}
        for snapshot_id, entry in entries.items():
            if not isinstance(snapshot_id, str) or not self._valid_index_entry(snapshot_id, entry):
                return None
            normalized[snapshot_id] = {
                **entry,
                "metadata": _legacy_metadata(entry["metadata"]),
            }
        return normalized

    def _rebuild_index(self) -> dict[str, dict[str, Any]]:
        """Migrate old archives or recover from a missing/corrupt index once."""
        index: dict[str, dict[str, Any]] = {}
        if not self.directory.exists():
            return index
        for path in self.directory.glob(f"*{_ARCHIVE_SUFFIX}"):
            snapshot_id = path.name[: -len(_ARCHIVE_SUFFIX)]
            if not _SAFE_ID.fullmatch(snapshot_id):
                continue
            try:
                snapshot = self._load_path(path)
                if snapshot.snapshot_id != snapshot_id:
                    continue
                index[snapshot_id] = self._index_entry(path, snapshot.metadata())
            except _ARCHIVE_ERRORS:
                continue
        return index

    def _refresh_index(self, index: dict[str, dict[str, Any]]) -> bool:
        """Refresh changed/new files without decompressing unchanged snapshots."""
        changed = False
        seen: set[str] = set()
        for path in self.directory.glob(f"*{_ARCHIVE_SUFFIX}"):
            snapshot_id = path.name[: -len(_ARCHIVE_SUFFIX)]
            if not _SAFE_ID.fullmatch(snapshot_id):
                continue
            try:
                stat = path.stat()
            except OSError:
                index.pop(snapshot_id, None)
                changed = True
                continue
            entry = index.get(snapshot_id)
            if (
                entry is not None
                and entry.get("size") == stat.st_size
                and entry.get("mtime_ns") == stat.st_mtime_ns
            ):
                seen.add(snapshot_id)
                continue
            try:
                snapshot = self._load_path(path)
                if snapshot.snapshot_id != snapshot_id:
                    raise ValueError("stored spectrum ID does not match filename")
                updated_entry = self._index_entry(path, snapshot.metadata())
            except _ARCHIVE_ERRORS:
                if snapshot_id in index:
                    del index[snapshot_id]
                    changed = True
                continue
            index[snapshot_id] = updated_entry
            seen.add(snapshot_id)
            changed = True
        for snapshot_id in tuple(index):
            if snapshot_id not in seen and not self._path(snapshot_id).exists():
                del index[snapshot_id]
                changed = True
        return changed

    @staticmethod
    def _index_entry(path: Path, metadata: dict[str, Any]) -> dict[str, Any]:
        stat = path.stat()
        return {
            "metadata": dict(metadata),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }

    @staticmethod
    def _valid_index_entry(snapshot_id: str, entry: Any) -> bool:
        if not _SAFE_ID.fullmatch(snapshot_id) or not isinstance(entry, dict):
            return False
        metadata = entry.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("snapshot_id") != snapshot_id:
            return False
        if metadata.get("source") not in (0, 1, 2):
            return False
        if metadata.get("channel") not in CHANNEL_KEYS:
            return False
        if metadata.get("point_count") not in SPECTRUM_CHANNEL_COUNTS:
            return False
        if not isinstance(metadata.get("acquired_at"), str):
            return False
        if not isinstance(metadata.get("total_counts"), int):
            return False
        if not isinstance(metadata.get("segment_count"), int):
            return False
        if not isinstance(metadata.get("calibration_profile"), str):
            return False
        return isinstance(entry.get("size"), int) and isinstance(entry.get("mtime_ns"), int)

    def _write_index(self, index: dict[str, dict[str, Any]]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary_fd, temporary_name = mkstemp(
            prefix=f".{_INDEX_FILENAME}.", suffix=".tmp", dir=self.directory
        )
        os.close(temporary_fd)
        temporary = Path(temporary_name)
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(
                    {"version": _INDEX_VERSION, "entries": index},
                    handle,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._index_path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def _persist_index(self, index: dict[str, dict[str, Any]]) -> bool:
        try:
            self._write_index(index)
        except OSError:
            self._index_dirty = True
            return False
        self._index_dirty = False
        return True

    def _prune(self, index: dict[str, dict[str, Any]]) -> None:
        if len(index) <= self.maximum:
            return
        for snapshot_id in sorted(index, reverse=True)[self.maximum :]:
            try:
                self._path(snapshot_id).unlink()
            except FileNotFoundError:
                pass
            del index[snapshot_id]
