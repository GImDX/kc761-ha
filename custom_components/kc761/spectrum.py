"""Structured KC761 spectrum snapshots and bounded gzip archive."""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .const import CHANNEL_KEYS, MAX_SAVED_SPECTRA, SPECTRUM_CHANNEL_COUNT

_SAFE_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-2]-[0-9a-f]{12}$")


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
    ) -> SpectrumSnapshot:
        """Validate fields and create a content-addressed archive identifier."""
        if not 0 <= source <= 2:
            raise ValueError("spectrum source outside 0..2")
        if len(counts) != SPECTRUM_CHANNEL_COUNT or len(energy_kev) != len(counts):
            raise ValueError("spectrum must contain exactly 2048 count and energy values")
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
        )

    @property
    def total_counts(self) -> int:
        return sum(self.counts)

    @property
    def channel_name(self) -> str:
        return CHANNEL_KEYS[self.source]

    def metadata(self) -> dict[str, Any]:
        """Return bounded metadata suitable for history lists and Recorder."""
        return {
            "snapshot_id": self.snapshot_id,
            "source": self.source,
            "channel": self.channel_name,
            "acquired_at": self.acquired_at.isoformat(),
            "accumulation_seconds": self.accumulation_seconds,
            "point_count": len(self.counts),
            "total_counts": self.total_counts,
            "segment_count": self.segment_count,
            "calibration_profile": self.calibration_profile,
        }

    def entity_attributes(self) -> dict[str, Any]:
        """Return one structured entity payload; points are excluded from Recorder."""
        result = self.metadata()
        result["points"] = [
            [channel, round(self.energy_kev[channel], 8), self.counts[channel]]
            for channel in range(len(self.counts))
        ]
        return result

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
        snapshot = cls(
            snapshot_id=snapshot_id,
            source=int(value["source"]),
            acquired_at=datetime.fromisoformat(str(value["acquired_at"])).astimezone(UTC),
            accumulation_seconds=(
                None if value.get("accumulation_seconds") is None else int(value["accumulation_seconds"])
            ),
            counts=tuple(int(item) for item in value["counts"]),
            energy_kev=tuple(float(item) for item in value["energy_kev"]),
            segment_count=int(value["segment_count"]),
            calibration_profile=str(value["calibration_profile"]),
        )
        if len(snapshot.counts) != SPECTRUM_CHANNEL_COUNT or len(snapshot.energy_kev) != len(snapshot.counts):
            raise ValueError("stored spectrum has an invalid length")
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
                )
            )
        return output.getvalue()


class SpectrumArchive:
    """A bounded per-config-entry archive outside Home Assistant Recorder."""

    def __init__(self, directory: Path, *, maximum: int = MAX_SAVED_SPECTRA) -> None:
        self.directory = directory
        self.maximum = maximum

    def save(self, snapshot: SpectrumSnapshot) -> None:
        """Atomically save one gzip JSON file and prune only this archive directory."""
        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self._path(snapshot.snapshot_id)
        temporary = destination.with_name(f".{destination.name}.tmp")
        with gzip.open(temporary, "wt", encoding="utf-8", newline="\n") as handle:
            json.dump(snapshot.to_storage(), handle, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporary, destination)
        self._prune()

    def list_metadata(self) -> list[dict[str, Any]]:
        """Return newest-first metadata while skipping unreadable private files."""
        result: list[dict[str, Any]] = []
        for path in sorted(self.directory.glob("*.json.gz"), reverse=True) if self.directory.exists() else []:
            try:
                result.append(self._load_path(path).metadata())
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
        return result

    def load(self, snapshot_id: str) -> SpectrumSnapshot:
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

    def _prune(self) -> None:
        paths = sorted(self.directory.glob("*.json.gz"), reverse=True)
        for path in paths[self.maximum :]:
            path.unlink()
