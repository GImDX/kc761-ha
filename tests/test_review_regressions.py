"""Offline regressions for the Phase 5 review findings."""

import asyncio
import gzip
import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.kc761.config_flow import KC761ConfigFlow
from custom_components.kc761 import coordinator as coordinator_module
from custom_components.kc761.protocol.client import KC761Client
from custom_components.kc761.protocol.codec import DecodedFrame, build_ack_frame, decode_frame
from custom_components.kc761.protocol.framing import FrameParser
from custom_components.kc761.protocol.models import RawFrame, TransactionError
from custom_components.kc761.protocol.transport import TransportError
from custom_components.kc761.spectrum import SpectrumArchive
from tests.test_ha_runtime import FakeClient, make_entry, make_hass, make_info, make_status
from tests.test_spectrum import snapshot


@pytest.mark.parametrize("rebuild", [False, True])
def test_truncated_gzip_isolated_without_removing_original(tmp_path, rebuild):
    archive = SpectrumArchive(tmp_path)
    damaged, healthy = snapshot(), snapshot(minute=1)
    archive.save(damaged)
    archive.save(healthy)
    path = tmp_path / f"{damaged.snapshot_id}.json.gz"
    truncated = path.read_bytes()[:-5]
    path.write_bytes(truncated)
    if rebuild:
        (tmp_path / ".metadata.json").unlink()
        archive = SpectrumArchive(tmp_path)
    assert [item["snapshot_id"] for item in archive.list_metadata()] == [healthy.snapshot_id]
    assert archive.load(healthy.snapshot_id) == healthy
    assert path.read_bytes() == truncated


@pytest.mark.parametrize("operation", ["list", "save"])
def test_invalid_utf8_sidecar_rebuilds_from_unchanged_archives(tmp_path, operation):
    original, added = snapshot(), snapshot(minute=1)
    SpectrumArchive(tmp_path).save(original)
    path = tmp_path / f"{original.snapshot_id}.json.gz"
    original_bytes = path.read_bytes()
    (tmp_path / ".metadata.json").write_bytes(b"\xff")
    archive = SpectrumArchive(tmp_path)
    if operation == "save":
        archive.save(added)
    ids = {item["snapshot_id"] for item in archive.list_metadata()}
    assert ids == ({original.snapshot_id, added.snapshot_id} if operation == "save" else {original.snapshot_id})
    assert path.read_bytes() == original_bytes


def test_invalid_deflate_stream_is_isolated(tmp_path):
    damaged, healthy = snapshot(), snapshot(minute=1)
    archive = SpectrumArchive(tmp_path)
    archive.save(damaged)
    archive.save(healthy)
    compressed = bytearray(gzip.compress(json.dumps(damaged.to_storage()).encode("utf-8")))
    compressed[10] = 7  # A reserved DEFLATE block type, after the standard gzip header.
    path = tmp_path / f"{damaged.snapshot_id}.json.gz"
    path.write_bytes(compressed)
    assert [item["snapshot_id"] for item in archive.list_metadata()] == [healthy.snapshot_id]
    assert path.read_bytes() == compressed


def test_config_preflight_requires_status_as_well_as_info():
    async def exercise():
        client = MagicMock()
        client.async_connect = AsyncMock()
        client.async_get_device_info = AsyncMock(return_value=make_info())
        client.async_get_status = AsyncMock(side_effect=TimeoutError("offline status"))
        client.async_close = AsyncMock()
        flow = KC761ConfigFlow()
        flow._new_flow_client = MagicMock(return_value=client)
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        flow.async_create_entry = MagicMock()
        flow.async_set_unique_id = AsyncMock()
        assert (await flow.async_step_tcp({"host": "offline.invalid", "port": 7601}))["type"] == "form"
        assert flow.async_show_form.call_args.kwargs["errors"] == {"base": "cannot_connect"}
        flow.async_create_entry.assert_not_called()
        flow.async_set_unique_id.assert_not_awaited()
        client.async_close.assert_awaited_once()

    asyncio.run(exercise())


def make_coordinator(client=None):
    hass, entry = make_hass(), make_entry(options={"enable_device_controls": True})
    with patch.object(coordinator_module.DataUpdateCoordinator, "__init__", return_value=None):
        coordinator = coordinator_module.KC761Coordinator(hass, entry)
    if client is not None:
        coordinator.client = client
        coordinator._device_info = client.info
        coordinator._snapshot = coordinator_module.KC761Snapshot(client.info, client.status, datetime.now(UTC))
    coordinator.async_set_updated_data = MagicMock()
    return coordinator


def test_concurrent_state_changes_merge_confirmed_status_under_one_lock():
    async def exercise():
        first_write, release = asyncio.Event(), asyncio.Event()

        class StatefulClient(FakeClient):
            async def async_set_state(self, **values):
                await super().async_set_state(**values)
                if len(self.state_writes) == 1:
                    first_write.set()
                    await release.wait()
                self.status = replace(self.status, **values)

        fake = StatefulClient(status=make_status(volume_status=0))
        coordinator = make_coordinator(fake)
        first = asyncio.create_task(coordinator.async_set_volume_flag(2, True))
        await first_write.wait()
        second = asyncio.create_task(coordinator.async_set_volume_flag(3, True))
        await asyncio.sleep(0)
        assert len(fake.state_writes) == 1
        release.set()
        await asyncio.gather(first, second)
        assert fake.state_writes == [{"volume_status": 4}, {"volume_status": 12}]
        assert coordinator.snapshot.status.volume_status == 12
        assert coordinator.async_set_updated_data.call_count == 2

    asyncio.run(exercise())


@pytest.mark.parametrize("phase", ["connect", "info", "status", "restore"])
def test_cancelled_reconfigure_closes_unadopted_candidate(phase):
    async def exercise():
        old = FakeClient()
        await old.async_connect()
        coordinator = make_coordinator(old)
        candidate, restored = FakeClient(), FakeClient()
        reached = asyncio.Event()

        async def pause():
            reached.set()
            await asyncio.Event().wait()

        if phase == "restore":
            candidate.async_get_status = AsyncMock(side_effect=OSError("candidate failed"))
            restored.async_get_device_info = pause
        else:
            setattr(candidate, {"connect": "async_connect", "info": "async_get_device_info",
                                "status": "async_get_status"}[phase], pause)
        coordinator._new_client = MagicMock(side_effect=[candidate, restored])
        task = asyncio.create_task(coordinator.async_reconfigure("replacement.invalid", 7601))
        await reached.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not candidate.connected
        assert candidate.close_calls == 1
        assert coordinator.client is old
        assert coordinator.host == "simulator.invalid"
        if phase == "restore":
            assert restored.close_calls == 1
            assert not restored.connected
        # Ownership remains coherent: the next normal poll restores the old endpoint.
        await coordinator._async_update_data()
        assert old.connected

    asyncio.run(exercise())


def test_reload_uses_ha_owned_task_and_completes_shutdown():
    async def exercise():
        from homeassistant.config_entries import ConfigEntries

        poll_entered, finish_poll = asyncio.Event(), asyncio.Event()

        class PollingClient(FakeClient):
            async def async_get_status(self):
                poll_entered.set()
                await finish_poll.wait()
                return await super().async_get_status()

        client = PollingClient()
        coordinator = make_coordinator(client)
        # An expired info refresh discovers an actual hardware signature change.
        client.info = make_info((2, 8, 5))
        hass, entry = coordinator._hass, coordinator.entry
        tasks = []

        def create_task(coroutine, name):
            task = asyncio.create_task(coroutine, name=name)
            tasks.append(task)
            return task

        async def reload(entry_id):
            assert entry_id == entry.entry_id
            assert asyncio.current_task() not in coordinator._background_tasks
            await coordinator.async_shutdown()
            return True

        hass.async_create_task = create_task
        entries = SimpleNamespace(hass=hass, async_get_known_entry=lambda _: entry, async_reload=reload)
        hass.config_entries.async_schedule_reload.side_effect = lambda value: ConfigEntries.async_schedule_reload(
            entries, value
        )
        with patch.object(coordinator_module.DataUpdateCoordinator, "async_shutdown", AsyncMock()):
            transaction = asyncio.create_task(coordinator.async_set_volume_flag(2, True))
            await poll_entered.wait()
            await asyncio.sleep(0)
            coordinator._schedule_hardware_reload()
            assert len(tasks) == 1
            assert not tasks[0].done(), "shutdown must wait for the in-flight transaction"
            finish_poll.set()
            with pytest.raises(RuntimeError, match="shut down"):
                await asyncio.wait_for(transaction, 1)
            assert await asyncio.wait_for(tasks[0], 1) is True
        assert client.state_writes == []
        assert coordinator.client.closed
        assert not tasks[0].cancelled()
        entry.async_create_background_task.assert_not_called()

    asyncio.run(exercise())


@pytest.mark.parametrize("operation", ["poll", "state", "time", "clear", "capture", "reconfigure"])
def test_shutdown_rejects_queued_connection_work(operation):
    async def exercise():
        coordinator = make_coordinator(FakeClient())
        coordinator.destructive_actions_enabled = True
        actions = {
            "poll": coordinator._async_update_data,
            "state": lambda: coordinator.async_set_auto_upload(False),
            "time": coordinator.async_sync_device_time,
            "clear": lambda: coordinator.async_clear_statistics(0, "spectrum"),
            "capture": coordinator.async_capture_spectrum,
            "reconfigure": lambda: coordinator.async_reconfigure("offline.invalid", 7601),
        }
        await coordinator._write_lock.acquire()
        queued = asyncio.create_task(actions[operation]())
        await asyncio.sleep(0)
        with patch.object(coordinator_module.DataUpdateCoordinator, "async_shutdown", AsyncMock()):
            shutdown = asyncio.create_task(coordinator.async_shutdown())
            await asyncio.sleep(0)
            coordinator._write_lock.release()
            with pytest.raises((RuntimeError, coordinator_module.UpdateFailed)):
                await queued
            await asyncio.wait_for(shutdown, 1)
        assert coordinator.client.connects == 0
        assert coordinator.client.closed

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", [None, "validator", "malformed", "connection_changed", "cancel"])
def test_write_identity_is_validated_per_connection_before_mock_transport(failure):
    """Only in-memory transport and validator callbacks; no sockets or device access."""
    async def exercise():
        transport = MagicMock()
        transport.connected = False

        async def read():
            await asyncio.Event().wait()

        async def connect():
            transport.connected = True

        async def close():
            transport.connected = False

        transport.async_read = read
        transport.async_connect = AsyncMock(side_effect=connect)
        transport.async_close = AsyncMock(side_effect=close)
        validator = MagicMock(side_effect=ValueError("validation failed") if failure == "validator" else None)
        client = KC761Client("offline.invalid", 0, transport=transport, device_info_validator=validator)

        async def info(_command):
            if failure == "malformed":
                return None
            if failure == "cancel":
                raise asyncio.CancelledError
            if failure == "connection_changed":
                client._connection_generation += 1
            return DecodedFrame(RawFrame(0, 0xA5, 0, b"", b""), "device_info", make_info())

        client._async_request_locked = AsyncMock(side_effect=info)

        async def write(_request):
            for sync, pending in client._pending.items():
                frame = FrameParser().feed(build_ack_frame(sync, pending.command))[0]
                pending.future.set_result(decode_frame(frame))

        transport.async_write = AsyncMock(side_effect=write)
        try:
            if failure is not None:
                expected = {"validator": ValueError, "malformed": TransactionError,
                            "connection_changed": TransportError, "cancel": asyncio.CancelledError}[failure]
                with pytest.raises(expected):
                    await client.async_set_state(auto_upload_status=0)
                transport.async_write.assert_not_awaited()
                assert not client.connected
            else:
                await client.async_set_state(auto_upload_status=0)
                await client.async_set_state(auto_upload_status=0)
                assert validator.call_count == 1
                await client.async_close()
                await client.async_set_state(auto_upload_status=0)
                assert validator.call_count == 2
                assert transport.async_write.await_count == 3
        finally:
            await client.async_close()

    asyncio.run(exercise())
