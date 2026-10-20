"""Structured spectrum entity payload, archive, retention, and CSV tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import csv
import gzip
import json
import tempfile
from threading import Barrier, Event
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from custom_components.kc761.const import DOMAIN
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


def consistency_snapshot(point_count: int, *, minute: int) -> SpectrumSnapshot:
    """Build boundary-distinct data for the multi-resolution consistency checks."""
    counts = tuple(point_count * 100_000 + channel * 3 + 7 for channel in range(point_count))
    energy = tuple(point_count / 10 + channel / 4 for channel in range(point_count))
    return SpectrumSnapshot.create(
        source=0,
        acquired_at=datetime(2026, 9, 8, 0, minute, tzinfo=UTC),
        accumulation_seconds=point_count + 17,
        counts=counts,
        energy_kev=energy,
        segment_count=(point_count + 511) // 512,
        calibration_profile=f"factory:{point_count}",
    )


class SpectrumSnapshotTests(unittest.TestCase):
    def test_structured_attributes_and_csv(self) -> None:
        value = snapshot()
        self.assertEqual(value.channel_name, "gamma")
        self.assertEqual(value.total_counts, sum(range(2048)))
        attributes = value.entity_attributes()
        self.assertIs(attributes, value.entity_attributes())
        self.assertIs(attributes["points"], value.entity_attributes()["points"])
        self.assertEqual(attributes["point_count"], 2048)
        self.assertEqual(attributes["points"][7], [7, 3.5, 7])
        csv_text = value.to_csv()
        self.assertTrue(csv_text.startswith("source,channel_name,acquired_at"))
        self.assertIn(
            ",2047,1023.50000000,2047,complete,observed,max_bound,2048,2048,0\r\n",
            csv_text,
        )
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
        with self.assertRaises(ValueError):
            SpectrumSnapshot.create(
                source=0,
                acquired_at=datetime.now(UTC),
                accumulation_seconds=1,
                counts=(-1,) + (0,) * 2047,
                energy_kev=(0.0,) * 2048,
                segment_count=1,
                calibration_profile="x",
            )

    def test_snapshot_accepts_supported_point_counts_and_legacy_storage(self) -> None:
        for point_count in (1024, 2048, 4096):
            value = SpectrumSnapshot.create(
                source=2,
                acquired_at=datetime(2026, 8, 21, 0, 0, tzinfo=UTC),
                accumulation_seconds=12,
                counts=tuple(range(point_count)),
                energy_kev=tuple(channel * 0.5 for channel in range(point_count)),
                segment_count=point_count // 256,
                calibration_profile="factory:2",
            )
            self.assertEqual(value.metadata()["point_count"], point_count)
            self.assertEqual(
                value.entity_attributes()["points"][-1],
                [point_count - 1, (point_count - 1) * 0.5, point_count - 1],
            )
            restored = SpectrumSnapshot.from_storage(value.to_storage())
            self.assertEqual(restored, value)

            legacy = value.to_storage()
            legacy.pop("point_count", None)
            self.assertEqual(SpectrumSnapshot.from_storage(legacy), value)

            mismatch = value.to_storage()
            mismatch["point_count"] = 2048 if point_count != 2048 else 1024
            with self.assertRaises(ValueError):
                SpectrumSnapshot.from_storage(mismatch)

    def test_supported_point_counts_preserve_entity_archive_and_csv_boundaries(self) -> None:
        values = [
            consistency_snapshot(point_count, minute=index)
            for index, point_count in enumerate((1024, 2048, 4096))
        ]
        with tempfile.TemporaryDirectory() as directory:
            archive = SpectrumArchive(Path(directory), maximum=len(values))
            for point_count, value in zip((1024, 2048, 4096), values, strict=True):
                attributes = value.entity_attributes()
                first_count = point_count * 100_000 + 7
                last_count = point_count * 100_000 + (point_count - 1) * 3 + 7
                first_energy = point_count / 10
                last_energy = point_count / 10 + (point_count - 1) / 4
                self.assertEqual(attributes["point_count"], point_count)
                self.assertEqual(len(attributes["points"]), point_count)
                self.assertEqual(attributes["points"][0], [0, first_energy, first_count])
                self.assertEqual(attributes["points"][-1], [point_count - 1, round(last_energy, 8), last_count])
                self.assertEqual(value.metadata()["total_counts"], sum(value.counts))

                rows = list(csv.reader(value.to_csv().splitlines()))
                self.assertEqual(len(rows), point_count + 1)
                self.assertEqual(
                    rows[1][4:7],
                    ["0", f"{first_energy:.8f}", str(first_count)],
                )
                self.assertEqual(
                    rows[-1][4:7],
                    [str(point_count - 1), f"{last_energy:.8f}", str(last_count)],
                )
                self.assertEqual(rows[-1][10:12], [str(point_count), str(point_count)])
                archive.save(value)

            listed = {item["snapshot_id"]: item for item in archive.list_metadata()}
            self.assertEqual(set(listed), {value.snapshot_id for value in values})
            for point_count, value in zip((1024, 2048, 4096), values, strict=True):
                self.assertEqual(listed[value.snapshot_id]["point_count"], point_count)
                self.assertEqual(listed[value.snapshot_id]["total_counts"], sum(value.counts))

            restarted = SpectrumArchive(Path(directory), maximum=len(values))
            restarted_listed = {item["snapshot_id"]: item for item in restarted.list_metadata()}
            self.assertEqual(set(restarted_listed), set(listed))
            for point_count, value in zip((1024, 2048, 4096), values, strict=True):
                restored = restarted.load(value.snapshot_id)
                self.assertEqual(restored, value)
                self.assertEqual(restored.metadata()["point_count"], point_count)
                self.assertEqual(restored.entity_attributes()["points"][0], value.entity_attributes()["points"][0])
                self.assertEqual(restored.entity_attributes()["points"][-1], value.entity_attributes()["points"][-1])

    def test_inferred_metadata_and_legacy_archive_provenance(self) -> None:
        counts = (0xFFFF,) + tuple(range(1, 2048))
        value = SpectrumSnapshot.create(
            source=0,
            acquired_at=datetime(2026, 8, 21, 0, 0, tzinfo=UTC),
            accumulation_seconds=12,
            counts=counts,
            energy_kev=tuple(channel * 0.5 for channel in range(2048)),
            segment_count=5,
            calibration_profile="factory:2",
            spectrum_status="complete",
            length_confidence="inferred",
            completion_basis="padding_candidate",
            received_count=2048,
            received_span=2056,
            padding_count=8,
        )
        metadata = value.metadata()
        self.assertEqual(metadata["point_count"], 2048)
        self.assertEqual(metadata["length_confidence"], "inferred")
        self.assertEqual(metadata["completion_basis"], "padding_candidate")
        self.assertEqual(metadata["received_span"], 2056)
        self.assertEqual(metadata["padding_count"], 8)
        self.assertEqual(value.to_storage()["counts"][0], 0xFFFF)

        legacy = value.to_storage()
        for key in (
            "spectrum_status",
            "length_confidence",
            "completion_basis",
            "received_count",
            "received_span",
            "padding_count",
        ):
            legacy.pop(key)
        restored = SpectrumSnapshot.from_storage(legacy)
        self.assertEqual(restored.length_confidence, "legacy")
        self.assertEqual(restored.completion_basis, "legacy")
        self.assertEqual(restored.received_count, 2048)
        self.assertEqual(restored.received_span, 2048)
        self.assertEqual(restored.counts[0], 0xFFFF)

    def test_storage_validation_and_optional_accumulation(self) -> None:
        value = snapshot()
        invalid_source = value.to_storage()
        invalid_source["source"] = 3
        with self.assertRaises(ValueError):
            SpectrumSnapshot.from_storage(invalid_source)

        invalid_counts = value.to_storage()
        invalid_counts["counts"] = [0]
        with self.assertRaises(ValueError):
            SpectrumSnapshot.from_storage(invalid_counts)

        invalid_energy = value.to_storage()
        invalid_energy["energy_kev"] = [0.0]
        with self.assertRaises(ValueError):
            SpectrumSnapshot.from_storage(invalid_energy)

        optional = SpectrumSnapshot.create(
            source=value.source,
            acquired_at=value.acquired_at,
            accumulation_seconds=None,
            counts=value.counts,
            energy_kev=value.energy_kev,
            segment_count=value.segment_count,
            calibration_profile=value.calibration_profile,
        )
        self.assertIn(",,0,0.00000000,0,complete,observed,max_bound,2048,2048,0\r\n", optional.to_csv())


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

    def test_delete_entry_removes_only_its_snapshot_index_and_temporary_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory)
            archive_root = config_path / ".storage" / DOMAIN
            target = archive_root / "entry-1"
            sibling = archive_root / "entry-2"
            archive = SpectrumArchive(target)
            archive.save(snapshot(minute=1))
            (target / ".orphan.tmp").write_text("partial", encoding="utf-8")
            SpectrumArchive(sibling).save(snapshot(minute=2, source=1))

            self.assertTrue((target / ".metadata.json").exists())
            self.assertEqual(len(list(target.glob("*.json.gz"))), 1)
            archive.delete_entry(config_path, "entry-1")

            self.assertFalse(target.exists())
            self.assertEqual(len(list(sibling.glob("*.json.gz"))), 1)
            self.assertTrue((sibling / ".metadata.json").exists())
            with self.assertRaisesRegex(RuntimeError, "archive is closed"):
                archive.save(snapshot(minute=3))

    def test_delete_entry_treats_missing_directory_as_already_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory)
            archive = SpectrumArchive(config_path / ".storage" / DOMAIN / "missing")
            archive.delete_entry(config_path, "missing")
            self.assertFalse((config_path / ".storage").exists())

    def test_delete_entry_rejects_traversal_mismatched_paths_and_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory)
            archive_root = config_path / ".storage" / DOMAIN
            outside = config_path / "outside"
            outside.mkdir()
            marker = outside / "keep.txt"
            marker.write_text("keep", encoding="utf-8")

            for invalid_entry_id in (None, "", ".", "..", "../outside", "..\\outside", "entry:stream", "entry\x00"):
                with self.assertRaisesRegex(ValueError, "config entry ID"):
                    SpectrumArchive(archive_root / ".." / "outside").delete_entry(
                        config_path,
                        invalid_entry_id,
                    )
            with self.assertRaisesRegex(ValueError, "does not match"):
                SpectrumArchive(outside).delete_entry(config_path, "entry-1")
            self.assertTrue(marker.exists())

            linked_target = archive_root / "entry-1"
            linked_target.mkdir(parents=True)
            with patch.object(
                SpectrumArchive,
                "_is_link_or_junction",
                new=staticmethod(lambda path: path == linked_target),
            ):
                with self.assertRaisesRegex(ValueError, "linked KC761 entry archive"):
                    SpectrumArchive(linked_target).delete_entry(config_path, "entry-1")
            self.assertTrue(marker.exists())

            with patch.object(
                SpectrumArchive,
                "_is_link_or_junction",
                new=staticmethod(lambda path: path == config_path / ".storage"),
            ):
                with self.assertRaisesRegex(ValueError, "linked storage path"):
                    SpectrumArchive(linked_target).delete_entry(config_path, "entry-1")
            self.assertTrue(marker.exists())

            with patch.object(
                SpectrumArchive,
                "_is_link_or_junction",
                new=staticmethod(lambda path: path == archive_root),
            ):
                with self.assertRaisesRegex(ValueError, "linked storage path"):
                    SpectrumArchive(linked_target).delete_entry(config_path, "entry-1")
            self.assertTrue(marker.exists())

            target_file = archive_root / "entry-file"
            target_file.write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not a directory"):
                SpectrumArchive(target_file).delete_entry(config_path, "entry-file")
            self.assertTrue(target_file.exists())

            link_probes = (
                (SimpleNamespace(is_symlink=lambda: True), True),
                (SimpleNamespace(is_symlink=lambda: False, is_junction=lambda: True), True),
                (SimpleNamespace(is_symlink=lambda: False, is_junction=lambda: False), False),
                (SimpleNamespace(is_symlink=lambda: False), False),
            )
            for probe, expected_link in link_probes:
                self.assertEqual(SpectrumArchive._is_link_or_junction(probe), expected_link)

    def test_delete_entry_waits_for_in_progress_save_and_rejects_late_saves(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory)
            target = config_path / ".storage" / DOMAIN / "entry-1"
            archive = SpectrumArchive(target)
            writing_index = Event()
            finish_index = Event()
            write_index = archive._write_index

            def paused_write(index: dict[str, dict[str, object]]) -> None:
                writing_index.set()
                if not finish_index.wait(timeout=5):
                    raise TimeoutError("test did not release the in-progress save")
                write_index(index)

            with patch.object(archive, "_write_index", side_effect=paused_write):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    saving = executor.submit(archive.save, snapshot())
                    self.assertTrue(writing_index.wait(timeout=5))
                    deleting = executor.submit(archive.delete_entry, config_path, "entry-1")
                    self.assertFalse(deleting.done())
                    finish_index.set()
                    saving.result(timeout=5)
                    deleting.result(timeout=5)

            self.assertFalse(target.exists())
            with self.assertRaisesRegex(RuntimeError, "archive is closed"):
                archive.save(snapshot(minute=1))
            self.assertFalse(target.exists())

    def test_index_survives_restart_without_reloading_unchanged_archives(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            values = [snapshot(minute=index) for index in range(2)]
            archive = SpectrumArchive(path)
            for value in values:
                archive.save(value)
            self.assertTrue((path / ".metadata.json").exists())

            restarted = SpectrumArchive(path)
            self.assertEqual(
                [item["snapshot_id"] for item in restarted.list_metadata()],
                [value.snapshot_id for value in reversed(values)],
            )
            with patch.object(
                SpectrumArchive,
                "_load_path",
                side_effect=AssertionError("unchanged list must use the metadata index"),
            ):
                self.assertEqual(restarted.list_metadata()[0]["total_counts"], sum(range(2048)))
            self.assertEqual(restarted.load(values[0].snapshot_id), values[0])

    def test_missing_or_corrupt_index_migrates_old_archives(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            values = [snapshot(minute=index) for index in range(2)]
            path.mkdir(exist_ok=True)
            for value in values:
                with gzip.open(path / f"{value.snapshot_id}.json.gz", "wt", encoding="utf-8") as handle:
                    json.dump(value.to_storage(), handle)

            archive = SpectrumArchive(path)
            self.assertEqual(len(archive.list_metadata()), 2)
            self.assertTrue((path / ".metadata.json").exists())
            (path / ".metadata.json").write_text("{broken", encoding="utf-8")
            restarted = SpectrumArchive(path)
            self.assertEqual(
                [item["snapshot_id"] for item in restarted.list_metadata()],
                [value.snapshot_id for value in reversed(values)],
            )

    def test_changed_corrupt_spectrum_is_removed_from_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            archive = SpectrumArchive(path)
            value = snapshot()
            archive.save(value)
            self.assertEqual(len(archive.list_metadata()), 1)
            (path / f"{value.snapshot_id}.json.gz").write_bytes(b"corrupt")
            self.assertEqual(archive.list_metadata(), [])
            with self.assertRaises((OSError, ValueError)):
                archive.load(value.snapshot_id)

    def test_getter_does_not_touch_filesystem(self) -> None:
        value = snapshot()
        with patch("pathlib.Path.open", side_effect=AssertionError("getter performed I/O")):
            self.assertEqual(value.total_counts, sum(range(2048)))
            self.assertEqual(value.entity_attributes()["points"][2047], [2047, 1023.5, 2047])

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

    def test_concurrent_saves_and_list_are_serialized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            archive = SpectrumArchive(path, maximum=4)
            values = [snapshot(minute=index) for index in range(2)]
            start = Barrier(3)

            def save(value: SpectrumSnapshot) -> None:
                start.wait()
                archive.save(value)

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(save, value) for value in values]
                start.wait()
                for future in futures:
                    future.result()

            listed = archive.list_metadata()
            self.assertEqual(
                {item["snapshot_id"] for item in listed},
                {value.snapshot_id for value in values},
            )
            index = json.loads((path / ".metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(set(index["entries"]), {value.snapshot_id for value in values})
            self.assertEqual(list(path.glob(".*.tmp")), [])

            value = snapshot(minute=2)
            start = Barrier(3)

            def save_again() -> None:
                start.wait()
                archive.save(value)

            def list_again() -> list[dict]:
                start.wait()
                return archive.list_metadata()

            with ThreadPoolExecutor(max_workers=2) as executor:
                save_future = executor.submit(save_again)
                list_future = executor.submit(list_again)
                start.wait()
                save_future.result()
                list_future.result()
            self.assertEqual(archive.list_metadata()[0]["snapshot_id"], value.snapshot_id)

    def test_index_write_failure_keeps_gzip_authoritative_and_retries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            archive = SpectrumArchive(path)
            value = snapshot()
            with patch.object(archive, "_write_index", side_effect=OSError("sidecar unavailable")):
                archive.save(value)
                self.assertTrue((path / f"{value.snapshot_id}.json.gz").exists())
                self.assertEqual(archive.list_metadata()[0]["snapshot_id"], value.snapshot_id)
                self.assertTrue(archive._index_dirty)
            self.assertEqual(archive.load(value.snapshot_id), value)
            with patch.object(archive, "_write_index", wraps=archive._write_index) as write_index:
                self.assertEqual(archive.list_metadata()[0]["snapshot_id"], value.snapshot_id)
                write_index.assert_called_once()
            self.assertFalse(archive._index_dirty)
            self.assertTrue((path / ".metadata.json").exists())

    def test_gzip_failure_is_reported_and_temporary_file_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            archive = SpectrumArchive(path)
            with patch(
                "custom_components.kc761.spectrum.gzip.open",
                side_effect=OSError("gzip unavailable"),
            ):
                with self.assertRaisesRegex(OSError, "gzip unavailable"):
                    archive.save(snapshot())
            self.assertEqual(list(path.glob("*.json.gz")), [])
            self.assertEqual(list(path.glob(".*.tmp")), [])

    def test_index_validation_and_rebuild_edge_cases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            archive = SpectrumArchive(path)
            value = snapshot()
            metadata = value.metadata()
            valid = {"metadata": metadata, "size": 1, "mtime_ns": 1}
            invalid_entries = (
                ("bad", valid),
                (value.snapshot_id, None),
                (value.snapshot_id, {"metadata": None, "size": 1, "mtime_ns": 1}),
                (
                    value.snapshot_id,
                    {"metadata": {**metadata, "snapshot_id": "bad"}, "size": 1, "mtime_ns": 1},
                ),
                (value.snapshot_id, {"metadata": {**metadata, "source": 9}, "size": 1, "mtime_ns": 1}),
                (value.snapshot_id, {"metadata": {**metadata, "channel": "bad"}, "size": 1, "mtime_ns": 1}),
                (
                    value.snapshot_id,
                    {"metadata": {**metadata, "point_count": 1}, "size": 1, "mtime_ns": 1},
                ),
                (
                    value.snapshot_id,
                    {"metadata": {**metadata, "acquired_at": 1}, "size": 1, "mtime_ns": 1},
                ),
                (
                    value.snapshot_id,
                    {"metadata": {**metadata, "total_counts": 1.5}, "size": 1, "mtime_ns": 1},
                ),
                (
                    value.snapshot_id,
                    {"metadata": {**metadata, "segment_count": "1"}, "size": 1, "mtime_ns": 1},
                ),
                (
                    value.snapshot_id,
                    {"metadata": {**metadata, "calibration_profile": None}, "size": 1, "mtime_ns": 1},
                ),
                (value.snapshot_id, {"metadata": metadata, "size": "1", "mtime_ns": 1}),
            )
            for snapshot_id, entry in invalid_entries:
                self.assertFalse(archive._valid_index_entry(snapshot_id, entry))

            for payload in (
                [],
                {"version": 2, "entries": {}},
                {"version": 1, "entries": []},
                {"version": 1, "entries": {"bad": valid}},
            ):
                (path / ".metadata.json").write_text(json.dumps(payload), encoding="utf-8")
                self.assertIsNone(archive._read_index())
            self.assertEqual(SpectrumArchive(path / "missing")._rebuild_index(), {})

    def test_refreshes_new_files_and_removes_missing_or_mismatched_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            archive = SpectrumArchive(path)
            first = snapshot()
            second = snapshot(minute=1)
            archive.save(first)
            with gzip.open(path / f"{second.snapshot_id}.json.gz", "wt", encoding="utf-8") as handle:
                json.dump(second.to_storage(), handle)
            self.assertEqual(len(archive.list_metadata()), 2)
            (path / f"{first.snapshot_id}.json.gz").unlink()
            self.assertEqual(len(archive.list_metadata()), 1)

            mismatch = path / "20260821T010000Z-0-aaaaaaaaaaaa.json.gz"
            with gzip.open(mismatch, "wt", encoding="utf-8") as handle:
                json.dump(second.to_storage(), handle)
            self.assertEqual(len(archive.list_metadata()), 1)
