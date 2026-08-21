"""Structured spectrum entity payload, archive, retention, and CSV tests."""

from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from custom_components.kc761.spectrum import SpectrumArchive, SpectrumSnapshot


def snapshot(*, minute: int = 0, source: int = 0) -> SpectrumSnapshot:
    counts = tuple(range(2048))
    energy = tuple(channel * 0.5 for channel in range(2048))
    return SpectrumSnapshot.create(
        source=source,
        acquired_at=datetime(2026, 8, 21, 0, minute, tzinfo=UTC),
        accumulation_seconds=3600 + minute,
        counts=counts,
        energy_kev=energy,
        segment_count=4,
        calibration_profile="factory:2",
    )


class SpectrumSnapshotTests(unittest.TestCase):
    def test_structured_attributes_and_csv(self) -> None:
        value = snapshot()
        self.assertEqual(value.channel_name, "gamma")
        self.assertEqual(value.total_counts, sum(range(2048)))
        attributes = value.entity_attributes()
        self.assertEqual(attributes["point_count"], 2048)
        self.assertEqual(attributes["points"][7], [7, 3.5, 7])
        csv_text = value.to_csv()
        self.assertTrue(csv_text.startswith("source,channel_name,acquired_at"))
        self.assertIn(",2047,1023.50000000,2047\r\n", csv_text)
        restored = SpectrumSnapshot.from_storage(value.to_storage())
        self.assertEqual(restored, value)

    def test_snapshot_validation(self) -> None:
        with self.assertRaises(ValueError):
            SpectrumSnapshot.create(
                source=3,
                acquired_at=datetime.now(UTC),
                accumulation_seconds=1,
                counts=(0,) * 2048,
                energy_kev=(0.0,) * 2048,
                segment_count=1,
                calibration_profile="x",
            )
        with self.assertRaises(ValueError):
            SpectrumSnapshot.create(
                source=0,
                acquired_at=datetime.now(),
                accumulation_seconds=1,
                counts=(0,) * 2048,
                energy_kev=(0.0,) * 2048,
                segment_count=1,
                calibration_profile="x",
            )
        with self.assertRaises(ValueError):
            SpectrumSnapshot.create(
                source=0,
                acquired_at=datetime.now(UTC),
                accumulation_seconds=1,
                counts=(0,),
                energy_kev=(0.0,),
                segment_count=1,
                calibration_profile="x",
            )


class SpectrumArchiveTests(unittest.TestCase):
    def test_save_list_load_and_prune(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = SpectrumArchive(Path(directory), maximum=2)
            values = [snapshot(minute=index, source=index % 3) for index in range(3)]
            for value in values:
                archive.save(value)
            metadata = archive.list_metadata()
            self.assertEqual(len(metadata), 2)
            self.assertEqual(metadata[0]["snapshot_id"], values[2].snapshot_id)
            self.assertEqual(archive.load(values[1].snapshot_id), values[1])
            self.assertFalse((Path(directory) / f"{values[0].snapshot_id}.json.gz").exists())

    def test_rejects_path_traversal_and_skips_corruption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            archive = SpectrumArchive(path)
            archive.save(snapshot())
            (path / "20260821T010000Z-0-aaaaaaaaaaaa.json.gz").write_bytes(b"bad")
            self.assertEqual(len(archive.list_metadata()), 1)
            with self.assertRaises(ValueError):
                archive.load("../secret")

            invalid = snapshot(minute=2).to_storage()
            invalid["snapshot_id"] = "bad"
            with self.assertRaises(ValueError):
                SpectrumSnapshot.from_storage(invalid)

    def test_skips_non_object_storage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            archive = SpectrumArchive(path)
            path.mkdir(exist_ok=True)
            invalid = path / "20260821T010000Z-0-bbbbbbbbbbbb.json.gz"
            with gzip.open(invalid, "wt", encoding="utf-8") as handle:
                json.dump([], handle)
            self.assertEqual(archive.list_metadata(), [])
