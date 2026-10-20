"""Home Assistant runtime coverage without pytest-specific HA plugins.

The module is skipped when Home Assistant is unavailable on the developer
machine.  The target HA image supplies the real package and runs these tests
with ordinary pytest; every coroutine is driven with ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

pytest.importorskip("homeassistant")

from homeassistant.exceptions import ConfigEntryNotReady  # noqa: E402
from homeassistant.helpers import entity_registry as entity_registry_module  # noqa: E402
from custom_components.kc761 import (  # noqa: E402
    CONFIG_SCHEMA,
    async_migrate_entry,
    async_reload_entry,
    async_remove_entry,
    async_setup,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.kc761 import button as button_module  # noqa: E402
from custom_components.kc761 import binary_sensor as binary_sensor_module  # noqa: E402
from custom_components.kc761 import config_flow as config_flow_module  # noqa: E402
from custom_components.kc761 import coordinator as coordinator_module  # noqa: E402
from custom_components.kc761 import diagnostics as diagnostics_module  # noqa: E402
from custom_components.kc761 import select as select_module  # noqa: E402
from custom_components.kc761 import sensor as sensor_module  # noqa: E402
from custom_components.kc761 import switch as switch_module  # noqa: E402
from custom_components.kc761 import websocket_api as websocket_module  # noqa: E402
from custom_components.kc761.const import (  # noqa: E402
    BLE_VERIFIED_SERVICE_UUID,
    CONF_BLE_ADDRESS,
    CONF_BLE_SERVICE_UUID,
    CONF_ENABLE_DESTRUCTIVE_ACTIONS,
    CONF_ENABLE_DEVICE_CONTROLS,
    CONF_GAMMA_SPECTRUM_CHANNELS,
    CONF_NEUTRON_SPECTRUM_CHANNELS,
    CONF_PIN_SPECTRUM_CHANNELS,
    CONF_TRANSPORT,
    DEFAULT_HOST,
    DOMAIN,
    TRANSPORT_BLE,
    TRANSPORT_TCP,
)
from custom_components.kc761.protocol.codec import (  # noqa: E402
    build_calibration_frame,
    build_device_info_frame,
    build_spectrum_frame,
    build_status_frame,
    decode_calibration,
    decode_device_info,
    decode_frame,
    decode_status,
)
from custom_components.kc761.protocol.framing import FrameParser  # noqa: E402
from custom_components.kc761.protocol.models import CumulativeChannel, RawFrame, SpectrumData  # noqa: E402
from custom_components.kc761.spectrum import SpectrumArchive, SpectrumSnapshot  # noqa: E402
from tests.test_spectrum import consistency_snapshot  # noqa: E402


def run(coro):
    """Run a coroutine without pytest-asyncio."""
    return asyncio.run(coro)


async def invoke_websocket_handler(handler, hass, connection, message):
    """Call the undecorated async handler; HA decorator behavior is tested upstream."""
    function = inspect.unwrap(handler)
    result = function(hass, connection, message)
    if inspect.isawaitable(result):
        await result


def make_info(sensor_types: tuple[int, int, int] = (2, 0, 0), **kwargs):
    frame = FrameParser().feed(build_device_info_frame(1, sensor_types=sensor_types, **kwargs))[0]
    return decode_device_info(frame)


def make_status(*, sentinel: bool = False, sensor_status: int = 0, **kwargs):
    channels = ((-1, -1, -1, -1, -1, -1),) * 3 if sentinel else None
    frame = FrameParser().feed(build_status_frame(1, channels=channels, sensor_status=sensor_status, **kwargs))[0]
    return decode_status(frame)


def make_calibration():
    frame = FrameParser().feed(build_calibration_frame(1))[0]
    return decode_calibration(frame)


def make_entry(*, options: dict | None = None, unique_id: str = "7601-0000-000001"):
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.version = 1
    entry.unique_id = unique_id
    entry.data = {
        "host": "simulator.invalid",
        "port": 7601,
        "device_id": "7601-0000-000001",
    }
    entry.options = options or {}
    entry.runtime_data = None
    entry.add_update_listener = MagicMock(return_value=lambda: None)
    entry.async_on_unload = MagicMock()
    entry.async_create_background_task.side_effect = (
        lambda hass, coroutine, name: asyncio.create_task(coroutine, name=name)
    )
    return entry


def make_hass():
    hass = MagicMock()
    hass.data = {}
    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    hass.config_entries.async_reload = AsyncMock()
    hass.bus = MagicMock()
    hass.bus.async_listen_once = MagicMock(return_value=lambda: None)
    hass.http.async_register_static_paths = AsyncMock()
    hass.config.path.side_effect = lambda *parts: str(Path(".test-storage").joinpath(*parts))
    hass.async_add_executor_job.side_effect = lambda target, *args: asyncio.to_thread(target, *args)
    hass.async_create_task = lambda coroutine, *, name=None: asyncio.create_task(coroutine, name=name)
    return hass


def make_decoded_status(*, upload: bool = True):
    frame = FrameParser().feed(build_status_frame(1, upload=upload))[0]
    return decode_frame(frame)


def make_decoded_spectrum(*, source: int, offset: int, counts: tuple[int, ...]):
    frame = FrameParser().feed(
        build_spectrum_frame(1, source=source, offset=offset, counts=counts, upload=True)
    )[0]
    return decode_frame(frame)


class FakeClient:
    """Small fake used to exercise coordinator branches without sockets."""

    def __init__(self, info=None, status=None, *, error: Exception | None = None, **kwargs):
        del kwargs
        self.info = info or make_info()
        self.status = status or make_status()
        self.error = error
        self.connected = False
        self.protocol_errors = 0
        self.last_frame = None
        self.closed = False
        self.close_calls = 0
        self.connects = 0
        self.info_calls = 0
        self.status_calls = 0
        self.calibration_calls = 0
        self.spectrum_calls: list[int] = []
        self.state_writes: list[dict] = []
        self.time_writes: list[int] = []
        self.clear_writes: list[dict] = []

    async def async_connect(self):
        self.connects += 1
        if self.error:
            raise self.error
        self.connected = True

    async def async_close(self):
        self.close_calls += 1
        self.closed = True
        self.connected = False

    async def async_get_device_info(self):
        self.info_calls += 1
        if self.error:
            raise self.error
        return self.info

    async def async_get_status(self):
        self.status_calls += 1
        if self.error:
            raise self.error
        return self.status

    async def async_get_calibration(self):
        self.calibration_calls += 1
        return make_calibration()

    async def async_get_spectrum(self, source: int):
        self.spectrum_calls.append(source)
        return SpectrumData(source, tuple(range(2048)), 4, 0xA0)

    async def async_set_state(self, **values):
        self.state_writes.append(values)

    async def async_set_device_time(self, epoch: int):
        self.time_writes.append(epoch)

    async def async_clear_statistics(self, **masks):
        self.clear_writes.append(masks)


class StrictSingleClient(FakeClient):
    """Fake TCP endpoint that rejects overlapping active connections."""

    def __init__(self, owner, label, **kwargs):
        super().__init__(**kwargs)
        self.owner = owner
        self.label = label

    async def async_connect(self):
        if self.owner["active"]:
            raise AssertionError(f"overlapping connection: {self.label}")
        self.owner["events"].append(f"{self.label}:connect")
        await super().async_connect()
        if self.connected:
            self.owner["active"] += 1

    async def async_close(self):
        self.owner["events"].append(f"{self.label}:close")
        if self.connected:
            self.owner["active"] -= 1
        await super().async_close()


def test_frontend_build_tracks_exact_bytes_and_registers_once(tmp_path: Path) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    card = frontend / "kc761-spectrum-card.js"
    urls = []
    for content in (b"same-version\n", b"same-version\n", b"same-version\r\n"):
        card.write_bytes(content)
        hass = make_hass()
        with (
            patch("custom_components.kc761.__file__", str(tmp_path / "__init__.py")),
            patch("custom_components.kc761.websocket_api.async_register") as register,
            patch("homeassistant.components.frontend.add_extra_js_url") as add_url,
        ):
            assert run(async_setup(hass, {}))
            assert run(async_setup(hass, {}))
        register.assert_called_once_with(hass)
        add_url.assert_called_once_with(
            hass, "/kc761/kc761-spectrum-card.js?v=0.2.0&build=" + hashlib.sha256(content).hexdigest()
        )
        hass.async_add_executor_job.assert_called_once()
        assert hass.async_add_executor_job.call_args.args[1] == card
        routes = hass.http.async_register_static_paths.await_args.args[0]
        assert routes[0].url_path == "/kc761/kc761-spectrum-card.js"
        assert routes[0].path == str(card)
        assert routes[0].cache_headers is True
        assert "lovelace" not in hass.data, "automatic module registration does not create resource records"
        urls.append(add_url.call_args.args[1])
    assert urls[0] == urls[1], "unchanged contents have a stable loading address"
    assert urls[1] != urls[2], "even a byte-only change invalidates the old loading address"


def test_frontend_missing_asset_does_not_register_an_unfingerprinted_url(tmp_path: Path) -> None:
    hass = make_hass()
    with (
        patch("custom_components.kc761.__file__", str(tmp_path / "__init__.py")),
        patch("custom_components.kc761.websocket_api.async_register") as register,
        patch("homeassistant.components.frontend.add_extra_js_url") as add_url,
        pytest.raises(FileNotFoundError),
    ):
        run(async_setup(hass, {}))
    register.assert_not_called()
    add_url.assert_not_called()
    hass.http.async_register_static_paths.assert_not_awaited()
    assert not hass.data.get("kc761_api_registered")


def test_entry_setup_unload_reload_and_failure(tmp_path: Path) -> None:
    hass = make_hass()
    hass.config.path.side_effect = lambda *parts: str(tmp_path.joinpath(*parts))
    assert callable(CONFIG_SCHEMA)
    with (
        patch("custom_components.kc761.websocket_api.async_register") as register,
        patch("homeassistant.components.frontend.add_extra_js_url") as add_extra_js_url,
    ):
        assert run(async_setup(hass, {})) is True
        assert run(async_setup(hass, {})) is True
    register.assert_called_once_with(hass)
    add_extra_js_url.assert_called_once_with(
        hass,
        "/kc761/kc761-spectrum-card.js?v=0.2.0&build="
        + hashlib.sha256(
            (Path(__file__).parents[1] / "custom_components/kc761/frontend/kc761-spectrum-card.js").read_bytes()
        ).hexdigest(),
    )
    hass.http.async_register_static_paths.assert_awaited_once()
    static_paths = hass.http.async_register_static_paths.await_args.args[0]
    assert {path.url_path for path in static_paths} == {
        "/kc761/kc761-spectrum-card.js",
        "/kc761/echarts.esm.min.js",
    }
    assert hass.data[DOMAIN] == {}
    entry = make_entry()
    saved_archive_marker = tmp_path / ".storage" / DOMAIN / entry.entry_id / "saved.json.gz"
    saved_archive_marker.parent.mkdir(parents=True)
    saved_archive_marker.write_bytes(b"preserve across reload and unload")
    fake = MagicMock()
    fake.async_config_entry_first_refresh = AsyncMock()
    fake.async_start = AsyncMock()
    fake.async_shutdown = AsyncMock()
    with patch("custom_components.kc761.coordinator.KC761Coordinator", return_value=fake):
        assert run(async_setup_entry(hass, entry)) is True
    assert entry.runtime_data is fake
    fake.async_start.assert_awaited_once()
    hass.config_entries.async_forward_entry_setups.assert_awaited_once()
    hass.bus.async_listen_once.assert_called_once()
    run(async_reload_entry(hass, entry))
    hass.config_entries.async_reload.assert_awaited_once_with(entry.entry_id)
    assert saved_archive_marker.exists()
    assert run(async_unload_entry(hass, entry)) is True
    fake.async_shutdown.assert_awaited_once()
    assert saved_archive_marker.exists()

    hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)
    assert run(async_unload_entry(hass, entry)) is False
    broken = MagicMock()
    broken.async_config_entry_first_refresh = AsyncMock(side_effect=RuntimeError("broken"))
    broken.async_start = AsyncMock()
    broken.async_shutdown = AsyncMock()
    with patch("custom_components.kc761.coordinator.KC761Coordinator", return_value=broken):
        with pytest.raises(ConfigEntryNotReady):
            run(async_setup_entry(hass, make_entry()))
    broken.async_shutdown.assert_awaited_once()

    cancelled = MagicMock()
    cancelled.async_config_entry_first_refresh = AsyncMock(side_effect=asyncio.CancelledError)
    cancelled.async_start = AsyncMock()
    cancelled.async_shutdown = AsyncMock()
    with patch(
        "custom_components.kc761.coordinator.KC761Coordinator",
        return_value=cancelled,
    ):
        with pytest.raises(asyncio.CancelledError):
            run(async_setup_entry(make_hass(), make_entry()))
    cancelled.async_shutdown.assert_awaited_once()

    forward_hass = make_hass()
    forward_hass.config_entries.async_forward_entry_setups = AsyncMock(side_effect=RuntimeError("platform failed"))
    forward_entry = make_entry()
    forward_failure = MagicMock()
    forward_failure.async_config_entry_first_refresh = AsyncMock()
    forward_failure.async_start = AsyncMock()
    forward_failure.async_shutdown = AsyncMock()
    with patch(
        "custom_components.kc761.coordinator.KC761Coordinator",
        return_value=forward_failure,
    ):
        with pytest.raises(RuntimeError, match="platform failed"):
            run(async_setup_entry(forward_hass, forward_entry))
    forward_failure.async_shutdown.assert_awaited_once()
    assert forward_entry.entry_id not in forward_hass.data[DOMAIN]

    forward_cancel_hass = make_hass()
    forward_cancel_hass.config_entries.async_forward_entry_setups = AsyncMock(side_effect=asyncio.CancelledError)
    forward_cancel_entry = make_entry()
    forward_cancelled = MagicMock()
    forward_cancelled.async_config_entry_first_refresh = AsyncMock()
    forward_cancelled.async_start = AsyncMock()
    forward_cancelled.async_shutdown = AsyncMock()
    with patch(
        "custom_components.kc761.coordinator.KC761Coordinator",
        return_value=forward_cancelled,
    ):
        with pytest.raises(asyncio.CancelledError):
            run(async_setup_entry(forward_cancel_hass, forward_cancel_entry))
    forward_cancelled.async_shutdown.assert_awaited_once()
    assert forward_cancel_entry.entry_id not in forward_cancel_hass.data[DOMAIN]

    migration_entry = make_entry(unique_id="7601_0000_000001")
    migration_entry.version = 1
    hass.config_entries.async_update_entry = MagicMock()
    registry = MagicMock()
    entity_ids = {
        ("binary_sensor", "7601-0000-000001_auto_upload_enabled"): "binary_sensor.old",
        ("binary_sensor", "7601-0000-000001_gamma_spectrum_accumulating_status"): "binary_sensor.new",
        ("sensor", "7601-0000-000001_device_time"): "sensor.device_time",
        ("sensor", "7601-0000-000001_clock_offset"): "sensor.clock_offset",
        ("sensor", "7601-0000-000001_coprocessor_firmware"): "sensor.coprocessor",
        ("sensor", "7601-0000-000001_gamma_avg_dose_rate"): "sensor.gamma_avg_dose_rate",
        ("sensor", "7601-0000-000001_gamma_raw_cps"): "sensor.gamma_raw_cps",
    }
    registry.async_get_entity_id.side_effect = (
        lambda domain, platform, unique_id: entity_ids.get((domain, unique_id))
    )
    registry.async_get.side_effect = lambda entity_id: {
        "sensor.device_time": SimpleNamespace(
            disabled_by=entity_registry_module.RegistryEntryDisabler.INTEGRATION
        ),
        "sensor.clock_offset": SimpleNamespace(
            disabled_by=entity_registry_module.RegistryEntryDisabler.USER
        ),
        "sensor.coprocessor": SimpleNamespace(
            disabled_by=entity_registry_module.RegistryEntryDisabler.INTEGRATION
        ),
        "sensor.gamma_avg_dose_rate": SimpleNamespace(
            disabled_by=entity_registry_module.RegistryEntryDisabler.INTEGRATION
        ),
    }.get(entity_id)
    with patch("custom_components.kc761.er.async_get", return_value=registry):
        assert run(async_migrate_entry(hass, migration_entry)) is True
    assert registry.async_remove.call_args_list == [
        call("binary_sensor.old"),
        call("sensor.gamma_raw_cps"),
    ]
    assert call("binary_sensor.new") not in registry.async_remove.call_args_list
    assert registry.async_update_entity.call_args_list == [
        call(
            "sensor.device_time",
            entity_category=None,
            disabled_by=None,
        ),
        call("sensor.clock_offset", entity_category=None),
        call(
            "sensor.coprocessor",
            entity_category=None,
            disabled_by=None,
        ),
        call(
            "sensor.gamma_avg_dose_rate",
            entity_category=None,
            disabled_by=None,
        ),
    ]
    hass.config_entries.async_update_entry.assert_called_once_with(
        migration_entry,
        unique_id="7601-0000-000001",
        version=7,
    )
    migration_entry.version = 8
    assert run(async_migrate_entry(hass, migration_entry)) is False

    raw_entry = make_entry(options={"create_raw_entities": True})
    raw_entry.version = 3
    raw_registry = MagicMock()
    raw_registry.async_get_entity_id.side_effect = (
        lambda domain, platform, unique_id: (
            "sensor.gamma_raw_cps"
            if unique_id == "7601-0000-000001_gamma_raw_cps"
            else None
        )
    )
    raw_registry.async_get.return_value = SimpleNamespace(
        disabled_by=entity_registry_module.RegistryEntryDisabler.INTEGRATION
    )
    with patch("custom_components.kc761.er.async_get", return_value=raw_registry):
        assert run(async_migrate_entry(hass, raw_entry)) is True
    raw_registry.async_update_entity.assert_called_once_with(
        "sensor.gamma_raw_cps",
        entity_category=None,
        disabled_by=None,
    )


def test_remove_entry_without_runtime_data_deletes_only_its_archive(tmp_path: Path) -> None:
    async def exercise() -> None:
        hass = make_hass()
        hass.config.path.side_effect = lambda *parts: str(tmp_path.joinpath(*parts))
        entry = make_entry()
        archive_root = tmp_path / ".storage" / DOMAIN
        target = archive_root / entry.entry_id
        sibling = archive_root / "entry-2"
        SpectrumArchive(target).save(consistency_snapshot(1024, minute=12))
        (target / ".unfinished.tmp").write_bytes(b"partial save")
        SpectrumArchive(sibling).save(consistency_snapshot(1024, minute=13))

        await async_remove_entry(hass, entry)

        assert not target.exists()
        assert len(list(sibling.glob("*.json.gz"))) == 1
        assert (sibling / ".metadata.json").exists()
        hass.async_add_executor_job.assert_called_once()
        assert hass.async_add_executor_job.call_args.args[0].__name__ == "delete_entry"

    run(exercise())


def test_remove_entry_stops_coordinator_left_by_failed_unload(tmp_path: Path) -> None:
    async def exercise() -> None:
        hass = make_hass()
        hass.config.path.side_effect = lambda *parts: str(tmp_path.joinpath(*parts))
        entry = make_entry()
        target = tmp_path / ".storage" / DOMAIN / entry.entry_id
        archive = SpectrumArchive(target)
        archive.save(consistency_snapshot(1024, minute=14))
        residual = SimpleNamespace(async_shutdown=AsyncMock(), _archive=archive)
        entry.runtime_data = residual
        sibling_coordinator = object()
        hass.data[DOMAIN] = {entry.entry_id: residual, "sibling-entry": sibling_coordinator}
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)

        assert await async_unload_entry(hass, entry) is False
        await async_remove_entry(hass, entry)

        residual.async_shutdown.assert_awaited_once()
        assert not target.exists()
        assert hass.data[DOMAIN] == {"sibling-entry": sibling_coordinator}

    run(exercise())


def test_remove_entry_retries_transient_shutdown_error_and_drains_archive(tmp_path: Path) -> None:
    async def exercise() -> None:
        hass = make_hass()
        hass.config.path.side_effect = lambda *parts: str(tmp_path.joinpath(*parts))
        entry = make_entry()
        fake = FakeClient(info=make_info((2, 8, 5)), status=make_status())
        with patch.object(coordinator_module.DataUpdateCoordinator, "__init__", return_value=None):
            with patch.object(coordinator_module, "KC761Client", return_value=fake):
                coordinator = coordinator_module.KC761Coordinator(hass, entry)

        target = tmp_path / ".storage" / DOMAIN / entry.entry_id
        coordinator._archive = SpectrumArchive(target)
        coordinator._archive.save(consistency_snapshot(1024, minute=15))
        hass.data[DOMAIN] = {entry.entry_id: coordinator, "sibling-entry": object()}
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)
        close_attempts = 0

        async def executor_job(target_function, *args):
            nonlocal close_attempts
            if getattr(target_function, "__name__", None) == "close":
                close_attempts += 1
                if close_attempts == 1:
                    raise OSError("transient archive close failure")
            return await asyncio.to_thread(target_function, *args)

        hass.async_add_executor_job.side_effect = executor_job
        base_shutdown = AsyncMock()
        with patch.object(coordinator_module.DataUpdateCoordinator, "async_shutdown", base_shutdown):
            assert await async_unload_entry(hass, entry) is False
            await async_remove_entry(hass, entry)

        assert close_attempts == 2
        assert coordinator._shutdown_complete is True
        assert coordinator._client_closed is True
        assert fake.close_calls == 1
        assert not target.exists()
        assert "sibling-entry" in hass.data[DOMAIN]
        assert base_shutdown.await_count == 2
        await coordinator.async_shutdown()
        assert close_attempts == 2
        with pytest.raises(RuntimeError, match="shut down"):
            await coordinator.async_list_saved_spectra()
        with pytest.raises(RuntimeError, match="shut down"):
            await coordinator.async_get_saved_spectrum("bad")

    run(exercise())


def test_remove_entry_still_cleans_after_repeated_shutdown_errors(tmp_path: Path, caplog) -> None:
    async def exercise() -> None:
        hass = make_hass()
        hass.config.path.side_effect = lambda *parts: str(tmp_path.joinpath(*parts))
        entry = make_entry()
        target = tmp_path / ".storage" / DOMAIN / entry.entry_id
        SpectrumArchive(target).save(consistency_snapshot(1024, minute=16))
        wrong_archive = tmp_path / "wrong-archive"
        wrong_archive.mkdir()
        wrong_marker = wrong_archive / "preserve.txt"
        wrong_marker.write_text("still owned elsewhere", encoding="utf-8")
        residual = SimpleNamespace(
            async_shutdown=AsyncMock(side_effect=RuntimeError("stop failed")),
            _archive=SpectrumArchive(wrong_archive),
        )
        sibling_coordinator = object()
        hass.data[DOMAIN] = {entry.entry_id: residual, "sibling-entry": sibling_coordinator}

        await async_remove_entry(hass, entry)

        assert residual.async_shutdown.await_count == 2
        assert not target.exists()
        assert wrong_marker.exists()
        assert hass.data[DOMAIN] == {"sibling-entry": sibling_coordinator}

    run(exercise())
    assert any("after retry" in record.getMessage() for record in caplog.records)


def test_remove_entry_records_archive_filesystem_errors(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    async def exercise() -> None:
        hass = make_hass()
        hass.config.path.side_effect = lambda *parts: str(tmp_path.joinpath(*parts))
        entry = make_entry()
        archive = SpectrumArchive(tmp_path / ".storage" / DOMAIN / entry.entry_id)
        residual = SimpleNamespace(async_shutdown=AsyncMock(), _archive=archive)
        sibling_coordinator = object()
        hass.data[DOMAIN] = {entry.entry_id: residual, "sibling-entry": sibling_coordinator}
        with patch.object(SpectrumArchive, "delete_entry", side_effect=OSError("disk unavailable")):
            with pytest.raises(OSError, match="disk unavailable"):
                await async_remove_entry(hass, entry)
        residual.async_shutdown.assert_awaited_once()
        assert hass.data[DOMAIN] == {"sibling-entry": sibling_coordinator}

    run(exercise())
    assert any(
        "Failed to remove spectrum archive" in record.getMessage()
        and "entry-1" in record.getMessage()
        for record in caplog.records
    )


def test_config_entry_v5_tcp_migration_and_version_guards() -> None:
    hass = make_hass()
    entry = make_entry()
    entry.version = 5
    legacy_data = dict(entry.data)
    registry = MagicMock()
    registry.async_get_entity_id.return_value = None

    with patch("custom_components.kc761.er.async_get", return_value=registry):
        assert run(async_migrate_entry(hass, entry)) is True

    assert entry.data == legacy_data
    assert "transport" not in entry.data
    hass.config_entries.async_update_entry.assert_called_once_with(
        entry,
        unique_id="7601-0000-000001",
        version=7,
    )

    hass.config_entries.async_update_entry.reset_mock()
    entry.version = 7
    assert run(async_migrate_entry(hass, entry)) is True
    hass.config_entries.async_update_entry.assert_not_called()

    entry.version = 8
    assert run(async_migrate_entry(hass, entry)) is False
    hass.config_entries.async_update_entry.assert_not_called()


def test_legacy_entry_defaults_to_tcp_without_loading_ble_transport() -> None:
    hass = make_hass()
    entry = make_entry()
    fake_client = FakeClient()

    with patch.object(coordinator_module.DataUpdateCoordinator, "__init__", return_value=None):
        with patch.object(coordinator_module, "KC761Client", return_value=fake_client) as client:
            with patch.object(coordinator_module, "BleTransport") as ble:
                coordinator = coordinator_module.KC761Coordinator(hass, entry)

    assert coordinator.transport_type == TRANSPORT_TCP
    ble.assert_not_called()
    assert client.call_args.args[:2] == ("simulator.invalid", 7601)
    assert "transport" not in client.call_args.kwargs


def test_spectrum_channel_options_are_explicit_and_bounded() -> None:
    assert coordinator_module._spectrum_channel_counts(
        {
            CONF_GAMMA_SPECTRUM_CHANNELS: "1024",
            CONF_NEUTRON_SPECTRUM_CHANNELS: 2048,
            CONF_PIN_SPECTRUM_CHANNELS: "4096",
        }
    ) == (1024, 2048, 4096)
    assert coordinator_module._spectrum_channel_counts(
        {
            CONF_GAMMA_SPECTRUM_CHANNELS: 512,
            CONF_NEUTRON_SPECTRUM_CHANNELS: "invalid",
            CONF_PIN_SPECTRUM_CHANNELS: None,
        }
    ) == (2048, 2048, 2048)


def test_config_flow_success_error_duplicate_reconfigure_and_options() -> None:
    async def exercise() -> None:
        info = make_info()
        status = make_status()

        class Client:
            def __init__(self, *args, **kwargs):
                del args, kwargs

            async def async_connect(self):
                return None

            async def async_get_device_info(self):
                return info

            async def async_get_status(self):
                return status

            async def async_close(self):
                return None

        flow = config_flow_module.KC761ConfigFlow()
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()
        assert (await flow.async_step_user())["type"] == "form"
        with patch.object(config_flow_module, "KC761Client", Client):
            result = await flow.async_step_user({"host": "localhost", "port": 7601})
        assert result["type"] == "create_entry"
        flow.async_set_unique_id.assert_awaited_once_with("7601-0000-000001")

        class FailingClient(Client):
            async def async_connect(self):
                raise OSError("offline")

        failed = config_flow_module.KC761ConfigFlow()
        failed.async_show_form = MagicMock(return_value={"type": "form"})
        with patch.object(config_flow_module, "KC761Client", FailingClient):
            result = await failed.async_step_user({"host": "offline", "port": 7601})
        assert result["type"] == "form"

        duplicate = config_flow_module.KC761ConfigFlow()
        duplicate.async_show_form = MagicMock(return_value={"type": "form"})
        duplicate.async_set_unique_id = AsyncMock()
        duplicate._abort_if_unique_id_configured = MagicMock(side_effect=RuntimeError("duplicate"))
        with patch.object(config_flow_module, "KC761Client", Client):
            with pytest.raises(RuntimeError):
                await duplicate.async_step_user({"host": "localhost", "port": 7601})

        entry = make_entry()
        entry.unique_id = "7601-0000-000001"
        hass = make_hass()
        hass.config_entries.async_get_entry.return_value = entry
        hass.config_entries.async_get_known_entry.return_value = entry
        reconfigure = config_flow_module.KC761ConfigFlow()
        reconfigure.hass = hass
        reconfigure.context = {"entry_id": entry.entry_id}
        reconfigure.async_show_form = MagicMock(return_value={"type": "form"})
        reconfigure.async_abort = MagicMock(return_value={"type": "abort"})
        assert (await reconfigure.async_step_reconfigure())["type"] == "form"
        with patch.object(config_flow_module, "KC761Client", Client):
            result = await reconfigure.async_step_reconfigure({"host": "new", "port": 7602})
        assert result["type"] == "abort"
        reconfigure.async_abort.assert_called_once_with(reason="reconfigure_successful")
        updated_data = hass.config_entries.async_update_entry.call_args.kwargs["data"]
        assert updated_data == {
            "device_id": "7601-0000-000001",
            "transport": "tcp",
            "host": "new",
            "port": 7602,
        }
        hass.config_entries.async_schedule_reload.assert_not_called()

        entry.data = {
            "device_id": "7601-0000-000001",
            "transport": "tcp",
            "host": "old",
            "port": 7601,
            "ble_address": "AA:BB:CC:DD:EE:FF",
            "ble_service_uuid": "12345678-1234-5678-1234-567812345678",
        }
        ble_reconfigure = config_flow_module.KC761ConfigFlow()
        ble_reconfigure.hass = hass
        ble_reconfigure.context = {"entry_id": entry.entry_id}
        ble_reconfigure.async_abort = MagicMock(return_value={"type": "abort"})
        with patch.object(config_flow_module, "KC761Client", Client):
            result = await ble_reconfigure.async_step_reconfigure(
                {"transport": "ble", "ble_address": "11:22:33:44:55:66"}
            )
        assert result["type"] == "abort"
        updated_ble_data = hass.config_entries.async_update_entry.call_args.kwargs["data"]
        assert updated_ble_data == {
            "device_id": "7601-0000-000001",
            "transport": "ble",
            "ble_address": "11:22:33:44:55:66",
            CONF_BLE_SERVICE_UUID: BLE_VERIFIED_SERVICE_UUID,
        }
        hass.config_entries.async_schedule_reload.assert_not_called()

        loaded_coordinator = object.__new__(coordinator_module.KC761Coordinator)
        loaded_coordinator.async_reconfigure = AsyncMock()
        hass.data[DOMAIN] = {entry.entry_id: loaded_coordinator}
        loaded = config_flow_module.KC761ConfigFlow()
        loaded.hass = hass
        loaded.context = {"entry_id": entry.entry_id}
        loaded.async_abort = MagicMock(return_value={"type": "abort"})
        with patch.object(config_flow_module, "KC761Client") as temporary_client:
            result = await loaded.async_step_reconfigure({"host": "loaded", "port": 7604})
        assert result["type"] == "abort"
        loaded_coordinator.async_reconfigure.assert_awaited_once_with("loaded", 7604)
        temporary_client.assert_not_called()
        hass.data.clear()

        unknown = config_flow_module.KC761ConfigFlow()
        unknown.hass = hass
        unknown.context = {"entry_id": "missing"}
        unknown.async_abort = MagicMock(return_value={"type": "abort", "reason": "unknown"})
        hass.config_entries.async_get_entry.return_value = None
        assert (await unknown.async_step_reconfigure())["reason"] == "unknown"
        hass.config_entries.async_get_entry.return_value = entry

        class FailingReconfigureClient(Client):
            async def async_get_device_info(self):
                raise OSError("offline")

        failed_reconfigure = config_flow_module.KC761ConfigFlow()
        failed_reconfigure.hass = hass
        failed_reconfigure.context = {"entry_id": entry.entry_id}
        failed_reconfigure.async_show_form = MagicMock(return_value={"type": "form"})
        with patch.object(config_flow_module, "KC761Client", FailingReconfigureClient):
            result = await failed_reconfigure.async_step_reconfigure({"host": "offline", "port": 7602})
        assert result["type"] == "form"

        class MalformedReconfigureClient(Client):
            async def async_get_device_info(self):
                raise ValueError("malformed device information")

        malformed_reconfigure = config_flow_module.KC761ConfigFlow()
        malformed_reconfigure.hass = hass
        malformed_reconfigure.context = {"entry_id": entry.entry_id}
        malformed_reconfigure.async_show_form = MagicMock(return_value={"type": "form"})
        with patch.object(config_flow_module, "KC761Client", MalformedReconfigureClient):
            result = await malformed_reconfigure.async_step_reconfigure({"host": "malformed", "port": 7602})
        assert result["type"] == "form"
        assert malformed_reconfigure.async_show_form.call_args.kwargs["errors"] == {"base": "cannot_connect"}

        mismatch_info = make_info()
        mismatch_info = mismatch_info.__class__(
            device_model=mismatch_info.device_model,
            hardware_version=mismatch_info.hardware_version,
            firmware_version=mismatch_info.firmware_version,
            coprocessor_firmware_version=mismatch_info.coprocessor_firmware_version,
            sensor_types=mismatch_info.sensor_types,
            device_id="other-device",
            cumulative=mismatch_info.cumulative,
            source_flag=mismatch_info.source_flag,
            raw=mismatch_info.raw,
            hardware_verified=False,
        )

        class MismatchClient(Client):
            async def async_get_device_info(self):
                return mismatch_info

        mismatch = config_flow_module.KC761ConfigFlow()
        mismatch.hass = hass
        mismatch.context = {"entry_id": entry.entry_id}
        mismatch.async_show_form = MagicMock(return_value={"type": "form"})
        with patch.object(config_flow_module, "KC761Client", MismatchClient):
            result = await mismatch.async_step_reconfigure({"host": "new", "port": 7602})
        assert result["type"] == "form"

        options = config_flow_module.KC761ConfigFlow.async_get_options_flow(entry)
        options.hass = hass
        options.handler = entry.entry_id
        options.async_show_form = MagicMock(return_value={"type": "form"})
        assert (await options.async_step_init())["type"] == "form"
        option_schema = options.async_show_form.call_args.kwargs["data_schema"]
        assert "dose_display_unit" not in {
            getattr(key, "key", key) for key in option_schema.schema
        }
        options.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        option_values = {
            "status_interval": 5,
            "info_interval": 60,
            "create_raw_entities": True,
            CONF_ENABLE_DEVICE_CONTROLS: True,
            CONF_ENABLE_DESTRUCTIVE_ACTIONS: True,
            "time_sync_enabled": True,
            "time_sync_interval": 24,
            "spectrum_save_enabled": True,
            "spectrum_save_interval": 60,
            "dose_display_unit": "micro",
            CONF_GAMMA_SPECTRUM_CHANNELS: 2048,
            CONF_NEUTRON_SPECTRUM_CHANNELS: 2048,
            CONF_PIN_SPECTRUM_CHANNELS: 2048,
        }
        assert (await options.async_step_init(option_values))["type"] == "create_entry"
        expected_options = {
            key: value
            for key, value in option_values.items()
            if key
            not in {
                CONF_GAMMA_SPECTRUM_CHANNELS,
                CONF_NEUTRON_SPECTRUM_CHANNELS,
                CONF_PIN_SPECTRUM_CHANNELS,
                "dose_display_unit",
            }
        }
        options.async_create_entry.assert_called_once_with(title="", data=expected_options)

        safe_options = config_flow_module.KC761ConfigFlow.async_get_options_flow(entry)
        safe_options.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        unsafe_values = {
            **option_values,
            CONF_ENABLE_DEVICE_CONTROLS: False,
            CONF_ENABLE_DESTRUCTIVE_ACTIONS: True,
            "time_sync_enabled": True,
        }
        assert (await safe_options.async_step_init(unsafe_values))["type"] == "create_entry"
        normalized = safe_options.async_create_entry.call_args.kwargs["data"]
        assert normalized[CONF_ENABLE_DESTRUCTIVE_ACTIONS] is False
        assert normalized["time_sync_enabled"] is False

    run(exercise())


def test_config_flow_transport_specific_steps_and_ble_discovery() -> None:
    async def exercise() -> None:
        hass = make_hass()

        tcp = config_flow_module.KC761ConfigFlow()
        tcp.hass = hass
        tcp.async_show_form = MagicMock(return_value={"type": "form"})
        assert (await tcp.async_step_user()) ["type"] == "form"
        transport_schema = tcp.async_show_form.call_args.kwargs["data_schema"]
        assert {getattr(key, "key", key) for key in transport_schema.schema} == {CONF_TRANSPORT}
        await tcp.async_step_user({CONF_TRANSPORT: "TCP"})
        tcp_schema = tcp.async_show_form.call_args.kwargs["data_schema"]
        assert {getattr(key, "key", key) for key in tcp_schema.schema} == {"host", "port"}

        class FakeBluetooth:
            @staticmethod
            def async_scanner_count(_hass, *, connectable=True):
                assert connectable
                return 1

            @staticmethod
            async def async_request_active_scan(_hass, *, duration):
                assert duration == 5

            @staticmethod
            def async_discovered_service_info(_hass, *, connectable=True):
                assert connectable
                return [SimpleNamespace(address="AA:BB:CC:DD:EE:FF", name="KC761")]

        ble = config_flow_module.KC761ConfigFlow()
        ble.hass = hass
        ble.async_show_form = MagicMock(return_value={"type": "form"})
        with patch.object(config_flow_module, "bluetooth", FakeBluetooth):
            await ble.async_step_user({CONF_TRANSPORT: "BLE"})
        assert ble.async_show_form.call_args.kwargs["step_id"] == "ble"
        ble_schema = ble.async_show_form.call_args.kwargs["data_schema"]
        assert {getattr(key, "key", key) for key in ble_schema.schema} == {
            CONF_BLE_ADDRESS,
            CONF_BLE_SERVICE_UUID,
        }
        ble_selector = next(
            value for key, value in ble_schema.schema.items() if getattr(key, "key", key) == CONF_BLE_ADDRESS
        )
        assert ble_selector.config["custom_value"] is True
        assert ble_selector.config["options"][0]["value"] == "AA:BB:CC:DD:EE:FF"

        no_scanner = config_flow_module.KC761ConfigFlow()
        no_scanner.hass = hass
        no_scanner.async_show_form = MagicMock(return_value={"type": "form"})

        class EmptyBluetooth:
            @staticmethod
            def async_scanner_count(_hass, *, connectable=True):
                return 0

            @staticmethod
            def async_discovered_service_info(_hass, *, connectable=True):
                return []

        with patch.object(config_flow_module, "bluetooth", EmptyBluetooth):
            await no_scanner.async_step_user({CONF_TRANSPORT: "BLE"})
        empty_schema = no_scanner.async_show_form.call_args.kwargs["data_schema"]
        empty_selector = next(
            value for key, value in empty_schema.schema.items() if getattr(key, "key", key) == CONF_BLE_ADDRESS
        )
        assert empty_selector.config["custom_value"] is True
        assert empty_selector.config["options"][0]["value"] == ""

        entry = make_entry()
        entry.data = {"transport": TRANSPORT_TCP, "host": "old", "port": 7601, "device_id": entry.unique_id}
        hass.config_entries.async_get_entry.return_value = entry
        reconfigure = config_flow_module.KC761ConfigFlow()
        reconfigure.hass = hass
        reconfigure.context = {"entry_id": entry.entry_id}
        reconfigure.async_show_form = MagicMock(return_value={"type": "form"})
        await reconfigure.async_step_reconfigure()
        reconfigure_transport_schema = reconfigure.async_show_form.call_args.kwargs["data_schema"]
        assert {getattr(key, "key", key) for key in reconfigure_transport_schema.schema} == {
            CONF_TRANSPORT
        }
        with patch.object(config_flow_module, "bluetooth", EmptyBluetooth):
            await reconfigure.async_step_reconfigure({CONF_TRANSPORT: "BLE"})
        assert reconfigure.async_show_form.call_args.kwargs["step_id"] == "reconfigure_ble"
        reconfigure_ble_schema = reconfigure.async_show_form.call_args.kwargs["data_schema"]
        assert {getattr(key, "key", key) for key in reconfigure_ble_schema.schema} == {
            CONF_BLE_ADDRESS,
            CONF_BLE_SERVICE_UUID,
        }

        invalid = config_flow_module.KC761ConfigFlow()
        invalid.async_show_form = MagicMock(return_value={"type": "form"})
        await invalid.async_step_user({CONF_TRANSPORT: "serial"})
        assert invalid.async_show_form.call_args.kwargs["errors"] == {"base": "invalid_transport"}

        tcp_submit = config_flow_module.KC761ConfigFlow()
        tcp_submit.async_show_form = MagicMock(return_value={"type": "form"})
        tcp_submit._async_validate_and_create = AsyncMock(return_value={"type": "create_entry"})
        assert (await tcp_submit.async_step_tcp({"host": "meter", "port": 7601}))["type"] == "create_entry"
        tcp_submit._async_validate_and_create.assert_awaited_once_with(
            {CONF_TRANSPORT: TRANSPORT_TCP, "host": "meter", "port": 7601}
        )

        ble_submit = config_flow_module.KC761ConfigFlow()
        ble_submit.async_show_form = MagicMock(return_value={"type": "form"})
        ble_submit._async_validate_and_create = AsyncMock(return_value={"type": "create_entry"})
        assert (
            await ble_submit.async_step_ble(
                {CONF_BLE_ADDRESS: "AA:BB:CC:DD:EE:FF", CONF_BLE_SERVICE_UUID: BLE_VERIFIED_SERVICE_UUID}
            )
        )["type"] == "create_entry"

        saved = config_flow_module.KC761ConfigFlow()
        saved.hass = hass
        with patch.object(config_flow_module, "bluetooth", EmptyBluetooth):
            saved_options = await saved._async_ble_address_options("11:22:33:44:55:66")
        assert saved_options[0]["value"] == "11:22:33:44:55:66"
        with patch.object(config_flow_module, "bluetooth", None):
            assert (await saved._async_ble_address_options())[0]["value"] == ""

        reconfigure_invalid = config_flow_module.KC761ConfigFlow()
        reconfigure_invalid.hass = hass
        reconfigure_invalid.context = {"entry_id": entry.entry_id}
        reconfigure_invalid.async_show_form = MagicMock(return_value={"type": "form"})
        await reconfigure_invalid.async_step_reconfigure({CONF_TRANSPORT: "serial"})
        assert reconfigure_invalid.async_show_form.call_args.kwargs["errors"] == {"base": "invalid_transport"}

        reconfigure_tcp = config_flow_module.KC761ConfigFlow()
        reconfigure_tcp.hass = hass
        reconfigure_tcp.context = {"entry_id": entry.entry_id}
        reconfigure_tcp.async_show_form = MagicMock(return_value={"type": "form"})
        reconfigure_tcp._async_reconfigure_endpoint = AsyncMock(return_value={"type": "abort"})
        await reconfigure_tcp.async_step_reconfigure({CONF_TRANSPORT: "TCP"})
        assert reconfigure_tcp.async_show_form.call_args.kwargs["step_id"] == "reconfigure_tcp"
        await reconfigure_tcp.async_step_reconfigure_tcp({"host": "new", "port": 7602})
        reconfigure_tcp._async_reconfigure_endpoint.assert_awaited_once_with(
            entry, {CONF_TRANSPORT: TRANSPORT_TCP, "host": "new", "port": 7602}
        )

        missing_ble_entry = config_flow_module.KC761ConfigFlow()
        missing_ble_entry.hass = hass
        missing_ble_entry.context = {"entry_id": entry.entry_id}
        missing_ble_entry.async_abort = MagicMock(return_value={"type": "abort", "reason": "unknown"})
        hass.config_entries.async_get_entry.return_value = None
        assert (await missing_ble_entry.async_step_reconfigure_ble())["reason"] == "unknown"
        hass.config_entries.async_get_entry.return_value = entry

        reconfigure_ble = config_flow_module.KC761ConfigFlow()
        reconfigure_ble.hass = hass
        reconfigure_ble.context = {"entry_id": entry.entry_id}
        reconfigure_ble.async_show_form = MagicMock(return_value={"type": "form"})
        reconfigure_ble._async_reconfigure_endpoint = AsyncMock(return_value={"type": "abort"})
        with patch.object(config_flow_module, "bluetooth", EmptyBluetooth):
            await reconfigure_ble.async_step_reconfigure_ble()
        assert reconfigure_ble.async_show_form.call_args.kwargs["step_id"] == "reconfigure_ble"
        await reconfigure_ble.async_step_reconfigure_ble({CONF_BLE_ADDRESS: "AA:BB:CC:DD:EE:FF"})
        reconfigure_ble._async_reconfigure_endpoint.assert_awaited_once_with(
            entry, {CONF_TRANSPORT: TRANSPORT_BLE, CONF_BLE_ADDRESS: "AA:BB:CC:DD:EE:FF"}
        )

        no_info = config_flow_module.KC761ConfigFlow()
        no_info.hass = hass
        no_info.context = {"entry_id": entry.entry_id}

        class NoInfoClient:
            async def async_connect(self):
                return None

            async def async_get_device_info(self):
                return None

            async def async_get_status(self):
                return None

            async def async_close(self):
                return None

        with patch.object(no_info, "_new_flow_client", return_value=NoInfoClient()):
            with pytest.raises(ValueError, match="no device information"):
                await no_info._async_validate_reconfigure_endpoint("meter", 7601)

        class MatchingClient(NoInfoClient):
            async def async_get_device_info(self):
                return make_info()

        with patch.object(no_info, "_new_flow_client", return_value=MatchingClient()):
            await no_info._async_validate_reconfigure_endpoint("meter", 7601, expected_device_id="7601-0000-000001")

        assert config_flow_module._connection_schema().schema
        assert config_flow_module._connection_schema({"transport": "tcp", "host": "x"}).schema
        assert config_flow_module._connection_schema({"transport": "ble", CONF_BLE_ADDRESS: "AA"}).schema

    run(exercise())


def test_new_tcp_default_is_isolated_from_reconfigure_saved_host() -> None:
    async def exercise() -> None:
        hass = make_hass()
        new_flow = config_flow_module.KC761ConfigFlow()
        new_flow.hass = hass
        new_flow.async_show_form = MagicMock(return_value={"type": "form"})
        await new_flow.async_step_tcp()
        new_schema = new_flow.async_show_form.call_args.kwargs["data_schema"]
        new_defaults = {str(marker.schema): marker.default() for marker in new_schema.schema}
        assert new_defaults["host"] == DEFAULT_HOST

        entry = make_entry()
        entry.data = {
            "device_id": "7601-0000-000001",
            CONF_TRANSPORT: TRANSPORT_TCP,
            "host": "192.168.8.127",
            "port": 7601,
        }
        hass.config_entries.async_get_entry.return_value = entry
        reconfigure = config_flow_module.KC761ConfigFlow()
        reconfigure.hass = hass
        reconfigure.context = {"entry_id": entry.entry_id}
        reconfigure.async_show_form = MagicMock(return_value={"type": "form"})
        await reconfigure.async_step_reconfigure()
        await reconfigure.async_step_reconfigure({CONF_TRANSPORT: TRANSPORT_TCP})
        saved_schema = reconfigure.async_show_form.call_args.kwargs["data_schema"]
        saved_defaults = {str(marker.schema): marker.default() for marker in saved_schema.schema}
        assert saved_defaults["host"] == "192.168.8.127"

    run(exercise())


def test_coordinator_refresh_push_reconnect_shutdown_reconfigure() -> None:
    async def exercise() -> None:
        hass = make_hass()
        entry = make_entry(options={"status_interval": 1, "info_interval": 1})
        fake = FakeClient()
        with patch.object(coordinator_module.DataUpdateCoordinator, "__init__", return_value=None):
            with patch.object(coordinator_module, "KC761Client", return_value=fake):
                coordinator = coordinator_module.KC761Coordinator(hass, entry)
        coordinator.last_update_success = True
        coordinator.async_set_updated_data = MagicMock()
        background_coroutine = asyncio.sleep(0)
        background_task = coordinator._create_hass_task(
            background_coroutine,
            "kc761-reader-test",
        )
        await background_task
        entry.async_create_background_task.assert_called_once_with(
            hass,
            background_coroutine,
            "kc761-reader-test",
        )
        result = await coordinator._async_update_data()
        assert result.status.battery_percent == 87
        assert fake.info_calls == 1
        assert fake.status_calls == 1
        coordinator.info_interval = 3600
        fake.connected = False
        await coordinator._async_update_data()
        assert coordinator.reconnects == 1
        assert fake.info_calls == 2
        assert fake.status_calls == 2
        coordinator._calibration = make_calibration()
        coordinator._latest_spectrum = None
        await coordinator._async_push(make_decoded_status())
        for offset in (1536, 1024, 512, 0):
            await coordinator._async_push(
                make_decoded_spectrum(
                    source=0,
                    offset=offset,
                    counts=tuple(range(offset, offset + 512)),
                )
            )
        # A1 has no wire EOF marker; the next A3 status closes the previous
        # round and is the first point where 2048 is usable.
        assert coordinator.latest_spectrum is None
        await coordinator._async_push(make_decoded_status())
        assert coordinator.latest_spectrum is not None
        assert coordinator.latest_spectrum.source == 0
        assert coordinator.latest_spectrum.counts == tuple(range(2048))
        assert coordinator.latest_spectrum.segment_count == 4
        completed_snapshot = coordinator.latest_spectrum
        await coordinator._async_push(
            make_decoded_spectrum(source=0, offset=0, counts=tuple(range(512)))
        )
        assert coordinator.latest_spectrum is completed_snapshot

        await coordinator._async_push(make_decoded_status())
        await coordinator._async_push(
            make_decoded_spectrum(source=0, offset=0, counts=tuple(range(512)))
        )
        await coordinator._async_push(make_decoded_status())
        await coordinator._async_push(
            make_decoded_spectrum(source=1, offset=0, counts=tuple(range(512)))
        )
        for offset in (1536, 1024, 512, 0):
            await coordinator._async_push(
                make_decoded_spectrum(
                    source=0,
                    offset=offset,
                    counts=tuple(range(offset, offset + 512)),
                )
            )
        await coordinator._async_push(make_decoded_status())
        assert coordinator.latest_spectrum.counts == tuple(range(2048))
        await coordinator._async_push(make_decoded_status())
        await coordinator._async_push(
            make_decoded_spectrum(source=0, offset=0, counts=tuple(range(512)))
        )
        assert coordinator._auto_upload_spectrum.source == 0
        fake.connected = False
        await coordinator._async_update_data()
        assert coordinator._auto_upload_spectrum.source is None
        await coordinator._async_push(SimpleNamespace(frame=RawFrame(1, 0xA3, 81, b"", b""), data=None))
        coordinator._device_info = None
        await coordinator._async_push(make_decoded_status())
        base_shutdown = AsyncMock()
        with patch.object(
            coordinator_module.DataUpdateCoordinator,
            "async_shutdown",
            base_shutdown,
        ):
            await coordinator.async_shutdown()
            await coordinator.async_shutdown()
        assert fake.closed is True
        assert fake.close_calls == 1
        base_shutdown.assert_awaited_once()
        coordinator._device_info = make_info()
        assert coordinator.hardware_verified is False
        assert coordinator_module._bounded("bad", 5, 10) == 5
        assert coordinator_module._bounded(None, 5, 10) == 5
        assert coordinator_module._bounded(100, 5, 10) == 10

        fresh_client = FakeClient()
        with patch.object(coordinator_module.DataUpdateCoordinator, "__init__", return_value=None):
            with patch.object(coordinator_module, "KC761Client", return_value=fresh_client):
                fresh = coordinator_module.KC761Coordinator(hass, entry)
        fresh._device_info = None
        with patch.object(coordinator_module, "KC761Client", return_value=fresh_client):
            await fresh.async_reconfigure("fresh-host", 7605)
        assert fresh.host == "fresh-host"

        failing = FakeClient(error=OSError("offline"))
        with patch.object(coordinator_module.DataUpdateCoordinator, "__init__", return_value=None):
            with patch.object(coordinator_module, "KC761Client", return_value=failing):
                failing_coordinator = coordinator_module.KC761Coordinator(hass, entry)
        with pytest.raises(coordinator_module.UpdateFailed):
            await failing_coordinator._async_update_data()
        assert failing_coordinator.consecutive_failures == 1
        assert failing_coordinator.last_error == "OSError"
        assert 1 <= failing_coordinator.retry_delay <= 30

        class NoInfoClient(FakeClient):
            async def async_get_device_info(self):
                self.info_calls += 1
                return None

        no_info = NoInfoClient()
        with patch.object(coordinator_module.DataUpdateCoordinator, "__init__", return_value=None):
            with patch.object(coordinator_module, "KC761Client", return_value=no_info):
                no_info_coordinator = coordinator_module.KC761Coordinator(hass, entry)
        with pytest.raises(coordinator_module.UpdateFailed):
            await no_info_coordinator._async_update_data()
        assert no_info_coordinator.last_error == "UpdateFailed"

        old = FakeClient()
        candidate = FakeClient()
        # HA constructs a new coordinator after unload; a shut-down owner
        # must never be reused to establish another connection.
        with patch.object(coordinator_module.DataUpdateCoordinator, "__init__", return_value=None):
            with patch.object(coordinator_module, "KC761Client", return_value=old):
                coordinator = coordinator_module.KC761Coordinator(hass, entry)
        coordinator.async_set_updated_data = MagicMock()
        coordinator._device_info = make_info()
        with patch.object(coordinator_module, "KC761Client", side_effect=[candidate, FakeClient()]):
            await coordinator.async_reconfigure("new-host", 7602)
        assert old.closed is True
        assert coordinator.host == "new-host"
        assert candidate.closed is False
        assert candidate.connected is True

        bad_candidate = FakeClient(info=make_info())
        mismatch = bad_candidate.info.__class__(
            device_model=bad_candidate.info.device_model,
            hardware_version=bad_candidate.info.hardware_version,
            firmware_version=bad_candidate.info.firmware_version,
            coprocessor_firmware_version=bad_candidate.info.coprocessor_firmware_version,
            sensor_types=bad_candidate.info.sensor_types,
            device_id="different",
            cumulative=bad_candidate.info.cumulative,
            source_flag=bad_candidate.info.source_flag,
            raw=bad_candidate.info.raw,
            hardware_verified=False,
        )
        bad_candidate.info = mismatch
        restored = FakeClient()
        with patch.object(coordinator_module, "KC761Client", side_effect=[bad_candidate, restored]):
            with pytest.raises(coordinator_module.DeviceIdentityError):
                await coordinator.async_reconfigure("bad-host", 7603)
        assert bad_candidate.closed is True
        assert restored.closed is False
        assert restored.connected is True
        assert coordinator.host == "new-host"

        reconnect_candidate = FakeClient(error=OSError("offline"))
        reconnect_restore = FakeClient(error=OSError("restore-offline"))
        reconnect_fallback = FakeClient()
        with patch.object(
            coordinator_module,
            "KC761Client",
            side_effect=[reconnect_candidate, reconnect_restore, reconnect_fallback],
        ):
            with pytest.raises(coordinator_module.ReconfigureRestoreError):
                await coordinator.async_reconfigure("error-host", 7604)
        assert reconnect_candidate.closed is True
        assert reconnect_restore.closed is True
        assert coordinator.client is reconnect_fallback
        assert reconnect_fallback.connected is False

        coordinator._device_info = make_info()
        coordinator.last_update_success = False
        coordinator.async_set_updated_data.reset_mock()
        await coordinator._async_push(make_decoded_status())
        coordinator.async_set_updated_data.assert_not_called()

        coordinator.last_update_success = True
        coordinator.async_set_updated_data.reset_mock()
        await coordinator._async_push(make_decoded_status(upload=False))
        coordinator.async_set_updated_data.assert_not_called()

        mismatch_client = FakeClient(info=make_info())
        mismatch_client.info = mismatch_client.info.__class__(
            device_model=mismatch_client.info.device_model,
            hardware_version=mismatch_client.info.hardware_version,
            firmware_version=mismatch_client.info.firmware_version,
            coprocessor_firmware_version=mismatch_client.info.coprocessor_firmware_version,
            sensor_types=mismatch_client.info.sensor_types,
            device_id="different-device",
            cumulative=mismatch_client.info.cumulative,
            source_flag=mismatch_client.info.source_flag,
            raw=mismatch_client.info.raw,
            hardware_verified=False,
        )
        with patch.object(coordinator_module.DataUpdateCoordinator, "__init__", return_value=None):
            with patch.object(coordinator_module, "KC761Client", return_value=mismatch_client):
                mismatch_coordinator = coordinator_module.KC761Coordinator(hass, entry)
        mismatch_coordinator.update_interval = None
        with pytest.raises(coordinator_module.UpdateFailed):
            await mismatch_coordinator._async_update_data()
        assert mismatch_coordinator.identity_mismatches == 1
        assert mismatch_client.closed is True
        assert mismatch_client.status_calls == 0

    run(exercise())


def test_reconfigure_serializes_single_client_and_rolls_back() -> None:
    async def exercise() -> None:
        owner = {"active": 0, "events": []}
        old = StrictSingleClient(owner, "old")
        candidate = StrictSingleClient(owner, "candidate")
        with patch.object(coordinator_module.DataUpdateCoordinator, "__init__", return_value=None):
            with patch.object(coordinator_module, "KC761Client", return_value=old):
                coordinator = coordinator_module.KC761Coordinator(make_hass(), make_entry())
        coordinator.client = old
        old.connected = True
        owner["active"] = 1

        with patch.object(coordinator_module, "KC761Client", return_value=candidate):
            await coordinator.async_reconfigure("candidate-host", 7602)
        assert owner["active"] == 1
        assert owner["events"][:2] == ["old:close", "candidate:connect"]
        assert candidate.connected is True

        failing = StrictSingleClient(owner, "failing", error=OSError("offline"))
        restored = StrictSingleClient(owner, "restored")
        with patch.object(coordinator_module, "KC761Client", side_effect=[failing, restored]):
            with pytest.raises(OSError):
                await coordinator.async_reconfigure("offline-host", 7603)
        assert owner["active"] == 1
        assert failing.closed is True
        assert restored.connected is True
        assert coordinator.host == "candidate-host"

        wrong_info = make_info(device_id="different-device")
        wrong = StrictSingleClient(owner, "wrong", info=wrong_info)
        restored_again = StrictSingleClient(owner, "restored-again")
        with patch.object(coordinator_module, "KC761Client", side_effect=[wrong, restored_again]):
            with pytest.raises(coordinator_module.DeviceIdentityError):
                await coordinator.async_reconfigure("wrong-host", 7604)
        assert owner["active"] == 1
        assert wrong.closed is True
        assert restored_again.connected is True
        with patch.object(coordinator_module.DataUpdateCoordinator, "async_shutdown", new=AsyncMock()):
            await coordinator.async_shutdown()
        assert owner["active"] == 0

    run(exercise())


def test_coordinator_spectrum_controls_archive_and_schedules(tmp_path: Path) -> None:
    async def exercise() -> None:
        hass = make_hass()
        hass.config.path.side_effect = lambda *parts: str(tmp_path.joinpath(*parts))
        entry = make_entry(
            options={
                CONF_ENABLE_DEVICE_CONTROLS: True,
                CONF_ENABLE_DESTRUCTIVE_ACTIONS: True,
                "time_sync_enabled": True,
                "time_sync_interval": 12,
                "spectrum_save_enabled": True,
                "spectrum_save_interval": 30,
            }
        )
        fake = FakeClient(info=make_info((2, 8, 5)), status=make_status(sensor_status=0x1C))
        with patch.object(coordinator_module.DataUpdateCoordinator, "__init__", return_value=None):
            with patch.object(coordinator_module, "KC761Client", return_value=fake):
                coordinator = coordinator_module.KC761Coordinator(hass, entry)
        coordinator._device_info = fake.info
        coordinator._snapshot = coordinator_module.KC761Snapshot(
            fake.info,
            fake.status,
            datetime.now(UTC),
        )
        coordinator.async_request_refresh = AsyncMock()
        coordinator.async_set_updated_data = MagicMock()
        names = []

        def capture_background(coroutine, name):
            names.append(name)
            coroutine.close()

        coordinator._start_background = capture_background
        await coordinator.async_start()
        assert names == ["kc761-initial-spectrum", "kc761-time-sync", "kc761-spectrum-save"]
        assert coordinator.time_sync_interval == 12
        assert coordinator.spectrum_save_interval == 30

        current = await coordinator.async_capture_spectrum(0)
        assert current.source == 0
        assert current.counts[2047] == 2047
        assert current.energy_kev[2047] == 2047
        assert fake.calibration_calls == 1
        assert fake.spectrum_calls == [0]
        assert coordinator.latest_spectrum is current

        saved = await coordinator.async_save_spectrum(1)
        assert saved.source == 1
        listed = await coordinator.async_list_saved_spectra()
        assert listed[0]["snapshot_id"] == saved.snapshot_id
        assert await coordinator.async_get_saved_spectrum(saved.snapshot_id) == saved
        filename, csv_text = await coordinator.async_export_spectrum_csv(saved.snapshot_id)
        assert filename.endswith(".csv")
        assert "channel_address,energy_kev,counts" in csv_text
        csv_target = hass.async_add_executor_job.call_args.args[0]
        assert csv_target.__name__ == "to_csv"
        assert csv_target.__self__.snapshot_id == saved.snapshot_id
        assert csv_text == csv_target.__self__.to_csv()

        async def fail_csv(target, *args):
            del target, args
            raise RuntimeError("csv failed")

        hass.async_add_executor_job.side_effect = fail_csv
        with pytest.raises(RuntimeError, match="csv failed"):
            await coordinator.async_export_spectrum_csv()
        hass.async_add_executor_job.side_effect = (
            lambda target, *args: asyncio.to_thread(target, *args)
        )

        await coordinator.async_set_active_sensor(2)
        assert fake.state_writes[-1]["sensor_status"] == (
            fake.status.sensor_status & ~0x03
        ) | 2
        await coordinator.async_set_spectrum_accumulating(1, False)
        assert fake.state_writes[-1]["sensor_status"] == fake.status.sensor_status & ~(1 << 3)
        await coordinator.async_set_volume(2)
        assert fake.state_writes[-1]["volume_status"] & 0x03 == 2
        await coordinator.async_set_volume_flag(4, True)
        assert fake.state_writes[-1]["volume_status"] & 0x10
        await coordinator.async_set_led_flag(1, False)
        assert not fake.state_writes[-1]["led_screen_status"] & 0x02
        await coordinator.async_set_auto_upload(True)
        assert fake.state_writes[-1]["auto_upload_status"] == 1
        await coordinator.async_sync_device_time()
        assert len(fake.time_writes) == 1
        assert coordinator.last_time_sync is not None
        await coordinator.async_clear_statistics(2, "cumulative_dose")
        assert fake.clear_writes[-1] == {"cumulative_dose_mask": 4}
        assert coordinator.control_commands == 8
        assert coordinator.async_request_refresh.await_count == 2

        with pytest.raises(ValueError):
            await coordinator.async_set_volume(3)
        with pytest.raises(ValueError):
            await coordinator.async_set_active_sensor(3)
        with pytest.raises(ValueError):
            await coordinator.async_clear_statistics(0, "unknown")

        coordinator.device_controls_enabled = False
        with pytest.raises(PermissionError, match="controls are disabled"):
            await coordinator.async_set_auto_upload(True)
        with pytest.raises(PermissionError, match="controls are disabled"):
            await coordinator.async_sync_device_time()
        coordinator.device_controls_enabled = True
        coordinator.destructive_actions_enabled = False
        with pytest.raises(PermissionError, match="destructive actions are disabled"):
            await coordinator.async_clear_statistics(0, "spectrum")

    run(exercise())


def test_websocket_spectrum_and_confirmation_api() -> None:
    async def exercise() -> None:
        hass = make_hass()
        entry = make_entry()
        fake = FakeClient(info=make_info((2, 0, 5)))
        with patch.object(coordinator_module.DataUpdateCoordinator, "__init__", return_value=None):
            with patch.object(coordinator_module, "KC761Client", return_value=fake):
                coordinator = coordinator_module.KC761Coordinator(hass, entry)
        coordinator._device_info = fake.info
        spectrum = SpectrumSnapshot.create(
            source=0,
            acquired_at=datetime.now(UTC),
            accumulation_seconds=12,
            counts=tuple(range(2048)),
            energy_kev=tuple(float(value) for value in range(2048)),
            segment_count=4,
            calibration_profile="factory:2",
        )
        coordinator._latest_spectrum = spectrum
        coordinator.async_list_saved_spectra = AsyncMock(return_value=[spectrum.metadata()])
        coordinator.async_get_saved_spectrum = AsyncMock(return_value=spectrum)
        coordinator.async_capture_spectrum = AsyncMock(return_value=spectrum)
        coordinator.async_save_spectrum = AsyncMock(return_value=spectrum)
        coordinator.async_export_spectrum_csv = AsyncMock(return_value=("spectrum.csv", "a,b\r\n"))
        coordinator.async_clear_statistics = AsyncMock()
        coordinator.device_controls_enabled = True
        coordinator.destructive_actions_enabled = True
        hass.data[DOMAIN] = {entry.entry_id: coordinator}
        connection = MagicMock()
        connection.user.is_admin = True

        with patch.object(websocket_module.websocket_api, "async_register_command") as register:
            websocket_module.async_register(hass)
        assert register.call_count == 7

        await invoke_websocket_handler(
            websocket_module.websocket_info,
            hass, connection, {"id": 1, "type": "kc761/info", "entry_id": entry.entry_id}
        )
        info_result = connection.send_result.call_args.args[1]
        assert info_result["serial_number"] == fake.info.device_id
        assert info_result["device_controls_enabled"] is True
        assert info_result["destructive_actions_enabled"] is True
        await invoke_websocket_handler(
            websocket_module.websocket_spectrum_list,
            hass,
            connection,
            {"id": 2, "type": "kc761/spectrum/list", "entry_id": entry.entry_id},
        )
        await invoke_websocket_handler(
            websocket_module.websocket_spectrum_get,
            hass,
            connection,
            {"id": 3, "type": "kc761/spectrum/get", "entry_id": entry.entry_id},
        )
        await invoke_websocket_handler(
            websocket_module.websocket_spectrum_capture,
            hass,
            connection,
            {"id": 4, "type": "kc761/spectrum/capture", "entry_id": entry.entry_id, "source": 0},
        )
        await invoke_websocket_handler(
            websocket_module.websocket_spectrum_save,
            hass,
            connection,
            {"id": 5, "type": "kc761/spectrum/save", "entry_id": entry.entry_id, "source": 0},
        )
        await invoke_websocket_handler(
            websocket_module.websocket_spectrum_csv,
            hass,
            connection,
            {"id": 6, "type": "kc761/spectrum/csv", "entry_id": entry.entry_id},
        )
        await invoke_websocket_handler(
            websocket_module.websocket_clear,
            hass,
            connection,
            {
                "id": 7,
                "type": "kc761/clear",
                "entry_id": entry.entry_id,
                "source": 0,
                "kind": "spectrum",
            },
        )
        coordinator.async_clear_statistics.assert_awaited_once_with(0, "spectrum")

        await invoke_websocket_handler(
            websocket_module.websocket_info,
            make_hass(), connection, {"id": 8, "type": "kc761/info", "entry_id": "missing"}
        )
        assert connection.send_error.call_args.args[1] == "kc761_error"
        assert "ValueError" in connection.send_error.call_args.args[2]

        hass.data[DOMAIN] = {entry.entry_id: coordinator}
        coordinator._device_info = None
        await invoke_websocket_handler(
            websocket_module.websocket_info,
            hass,
            connection,
            {"id": 9, "type": "kc761/info", "entry_id": entry.entry_id},
        )
        coordinator._device_info = fake.info
        error_handlers = (
            (
                websocket_module.websocket_spectrum_list,
                "async_list_saved_spectra",
                {"id": 10, "type": "kc761/spectrum/list", "entry_id": entry.entry_id},
            ),
            (
                websocket_module.websocket_spectrum_capture,
                "async_capture_spectrum",
                {"id": 11, "type": "kc761/spectrum/capture", "entry_id": entry.entry_id},
            ),
            (
                websocket_module.websocket_spectrum_save,
                "async_save_spectrum",
                {"id": 12, "type": "kc761/spectrum/save", "entry_id": entry.entry_id},
            ),
            (
                websocket_module.websocket_spectrum_csv,
                "async_export_spectrum_csv",
                {"id": 13, "type": "kc761/spectrum/csv", "entry_id": entry.entry_id},
            ),
            (
                websocket_module.websocket_clear,
                "async_clear_statistics",
                {
                    "id": 14,
                    "type": "kc761/clear",
                    "entry_id": entry.entry_id,
                    "source": 0,
                    "kind": "spectrum",
                },
            ),
        )
        for handler, method_name, message in error_handlers:
            setattr(coordinator, method_name, AsyncMock(side_effect=RuntimeError("expected")))
            await invoke_websocket_handler(handler, hass, connection, message)

        coordinator._latest_spectrum = None
        await invoke_websocket_handler(
            websocket_module.websocket_spectrum_get,
            hass,
            connection,
            {"id": 15, "type": "kc761/spectrum/get", "entry_id": entry.entry_id},
        )
        coordinator.async_get_saved_spectrum = AsyncMock(side_effect=RuntimeError("expected"))
        await invoke_websocket_handler(
            websocket_module.websocket_spectrum_get,
            hass,
            connection,
            {
                "id": 16,
                "type": "kc761/spectrum/get",
                "entry_id": entry.entry_id,
                "snapshot_id": spectrum.snapshot_id,
            },
        )

    run(exercise())


def test_supported_point_counts_survive_sensor_and_websocket_round_trips() -> None:
    async def exercise() -> None:
        hass = make_hass()
        entry = make_entry()
        info = make_info((2, 0, 0))
        fake = FakeClient(info=info)
        with patch.object(coordinator_module.DataUpdateCoordinator, "__init__", return_value=None):
            with patch.object(coordinator_module, "KC761Client", return_value=fake):
                coordinator = coordinator_module.KC761Coordinator(hass, entry)
        coordinator._device_info = info
        with tempfile.TemporaryDirectory() as directory:
            coordinator._archive = SpectrumArchive(Path(directory) / "spectrum")
            entry.runtime_data = coordinator
            entities = []
            with patch.object(
                sensor_module.CoordinatorEntity,
                "__init__",
                lambda self, value: setattr(self, "coordinator", value),
            ):
                await sensor_module.async_setup_entry(MagicMock(), entry, lambda values: entities.extend(values))
            spectrum_entity = next(
                entity for entity in entities if isinstance(entity, sensor_module.KC761SpectrumSensor)
            )
            hass.data[DOMAIN] = {entry.entry_id: coordinator}
            connection = MagicMock()
            values = [
                consistency_snapshot(point_count, minute=index)
                for index, point_count in enumerate((1024, 2048, 4096))
            ]
            for value in values:
                coordinator._archive.save(value)

            for point_count, value in zip((1024, 2048, 4096), values, strict=True):
                first_count = point_count * 100_000 + 7
                last_count = point_count * 100_000 + (point_count - 1) * 3 + 7
                first_energy = point_count / 10
                last_energy = point_count / 10 + (point_count - 1) / 4
                coordinator._latest_spectrum = value
                attributes = spectrum_entity.extra_state_attributes
                assert spectrum_entity.native_value == sum(value.counts)
                assert attributes["point_count"] == point_count
                assert len(attributes["points"]) == point_count
                assert attributes["points"][0] == [0, first_energy, first_count]
                assert attributes["points"][-1] == [point_count - 1, round(last_energy, 8), last_count]

                connection.reset_mock()
                await invoke_websocket_handler(
                    websocket_module.websocket_spectrum_list,
                    hass,
                    connection,
                    {"id": point_count, "type": "kc761/spectrum/list", "entry_id": entry.entry_id},
                )
                listed = connection.send_result.call_args.args[1]
                listed_value = next(item for item in listed if item["snapshot_id"] == value.snapshot_id)
                assert listed_value["point_count"] == point_count
                assert listed_value["total_counts"] == sum(value.counts)

                connection.reset_mock()
                await invoke_websocket_handler(
                    websocket_module.websocket_spectrum_get,
                    hass,
                    connection,
                    {"id": point_count + 1, "type": "kc761/spectrum/get", "entry_id": entry.entry_id},
                )
                latest = connection.send_result.call_args.args[1]
                assert latest["point_count"] == point_count
                assert len(latest["points"]) == point_count
                assert latest["points"][0] == [0, first_energy, first_count]
                assert latest["points"][-1] == [point_count - 1, round(last_energy, 8), last_count]

                connection.reset_mock()
                await invoke_websocket_handler(
                    websocket_module.websocket_spectrum_get,
                    hass,
                    connection,
                    {
                        "id": point_count + 2,
                        "type": "kc761/spectrum/get",
                        "entry_id": entry.entry_id,
                        "snapshot_id": value.snapshot_id,
                    },
                )
                restored = connection.send_result.call_args.args[1]
                assert restored["point_count"] == point_count
                assert len(restored["points"]) == point_count
                assert restored["points"][0] == [0, first_energy, first_count]
                assert restored["points"][-1] == [point_count - 1, round(last_energy, 8), last_count]

    run(exercise())


def test_coordinator_background_and_validation_edges() -> None:
    async def exercise() -> None:
        hass = make_hass()
        entry = make_entry()
        fake = FakeClient(info=make_info((2, 0, 5)), status=make_status())
        with patch.object(coordinator_module.DataUpdateCoordinator, "__init__", return_value=None):
            with patch.object(coordinator_module, "KC761Client", return_value=fake):
                coordinator = coordinator_module.KC761Coordinator(hass, entry)
        coordinator._device_info = fake.info
        coordinator._snapshot = coordinator_module.KC761Snapshot(
            fake.info,
            fake.status,
            datetime.now(UTC),
        )
        coordinator.async_request_refresh = AsyncMock()
        coordinator.async_set_updated_data = MagicMock()

        await coordinator.async_capture_spectrum()
        assert fake.spectrum_calls == [0]
        coordinator._latest_spectrum = None
        with pytest.raises(RuntimeError, match="no spectrum"):
            await coordinator.async_export_spectrum_csv()
        coordinator._snapshot = None
        with pytest.raises(RuntimeError, match="current status"):
            await coordinator.async_capture_spectrum()
        with pytest.raises(RuntimeError, match="current status"):
            coordinator._require_status()
        coordinator._archive = None
        with pytest.raises(RuntimeError, match="not initialized"):
            coordinator._require_archive()
        coordinator._notify_entities()

        coordinator._device_info = fake.info
        with pytest.raises(ValueError, match="not installed"):
            coordinator._validate_source(1)
        with pytest.raises(ValueError, match="volume flag"):
            await coordinator.async_set_volume_flag(1, True)
        with pytest.raises(ValueError, match="LED"):
            await coordinator.async_set_led_flag(3, True)

        coordinator.async_capture_spectrum = AsyncMock()
        await coordinator._async_initial_spectrum()
        coordinator.async_capture_spectrum = AsyncMock(side_effect=RuntimeError("expected"))
        await coordinator._async_initial_spectrum()
        coordinator.async_capture_spectrum = AsyncMock(side_effect=asyncio.CancelledError)
        with pytest.raises(asyncio.CancelledError):
            await coordinator._async_initial_spectrum()

        coordinator._shutdown = False
        coordinator.async_sync_device_time = AsyncMock()
        with patch.object(
            coordinator_module.asyncio,
            "sleep",
            AsyncMock(side_effect=asyncio.CancelledError),
        ):
            with pytest.raises(asyncio.CancelledError):
                await coordinator._async_time_sync_loop()
        coordinator.async_sync_device_time = AsyncMock(side_effect=RuntimeError("expected"))
        with patch.object(
            coordinator_module.asyncio,
            "sleep",
            AsyncMock(side_effect=asyncio.CancelledError),
        ):
            with pytest.raises(asyncio.CancelledError):
                await coordinator._async_time_sync_loop()
        coordinator.async_sync_device_time = AsyncMock(side_effect=asyncio.CancelledError)
        with pytest.raises(asyncio.CancelledError):
            await coordinator._async_time_sync_loop()

        coordinator._device_info = fake.info
        coordinator.async_save_spectrum = AsyncMock()
        with patch.object(
            coordinator_module.asyncio,
            "sleep",
            AsyncMock(side_effect=asyncio.CancelledError),
        ):
            with pytest.raises(asyncio.CancelledError):
                await coordinator._async_spectrum_save_loop()
        assert coordinator.async_save_spectrum.await_args_list == [call(0), call(2)]
        coordinator.async_save_spectrum = AsyncMock(side_effect=RuntimeError("expected"))
        with patch.object(
            coordinator_module.asyncio,
            "sleep",
            AsyncMock(side_effect=asyncio.CancelledError),
        ):
            with pytest.raises(asyncio.CancelledError):
                await coordinator._async_spectrum_save_loop()
        coordinator.async_save_spectrum = AsyncMock(side_effect=asyncio.CancelledError)
        with pytest.raises(asyncio.CancelledError):
            await coordinator._async_spectrum_save_loop()

        coordinator._profile_logged = False
        coordinator._log_protocol_profile(make_info(model=0xFE))
        coordinator._log_protocol_profile(fake.info)
        coordinator._reload_scheduled = False
        coordinator._shutdown = False
        coordinator._schedule_hardware_reload()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        hass.config_entries.async_schedule_reload.assert_called_once_with(entry.entry_id)
        coordinator._schedule_hardware_reload()
        coordinator._shutdown = True
        coordinator._reload_scheduled = False
        coordinator._schedule_hardware_reload()

        coordinator._backoff_base = 1
        coordinator._increase_backoff()
        assert coordinator._backoff_base == 2

        coordinator._shutdown = False
        sleeper = asyncio.Event()
        task = asyncio.create_task(sleeper.wait())
        coordinator._background_tasks.add(task)
        base_shutdown = AsyncMock()
        with patch.object(coordinator_module.DataUpdateCoordinator, "async_shutdown", base_shutdown):
            await coordinator.async_shutdown()
        assert task.cancelled()

    run(exercise())


def test_coordinator_selects_ble_transport_and_closes_one_client() -> None:
    async def exercise() -> None:
        hass = make_hass()
        entry = make_entry()
        entry.data = {
            CONF_TRANSPORT: TRANSPORT_BLE,
            CONF_BLE_ADDRESS: "AA:BB:CC:DD:EE:FF",
            CONF_BLE_SERVICE_UUID: "12345678-1234-5678-1234-567812345678",
            "device_id": "7601-0000-000001",
        }
        fake_client = FakeClient()
        fake_transport = object()
        with patch.object(coordinator_module.DataUpdateCoordinator, "__init__", return_value=None):
            with patch.object(coordinator_module.DataUpdateCoordinator, "async_shutdown", new_callable=AsyncMock):
                with patch.object(coordinator_module, "BleTransport", return_value=fake_transport) as ble:
                    with patch.object(coordinator_module, "KC761Client", return_value=fake_client) as client:
                        coordinator = coordinator_module.KC761Coordinator(hass, entry)

                        ble.assert_called_once_with(
                            hass,
                            "AA:BB:CC:DD:EE:FF",
                            service_uuid="12345678-1234-5678-1234-567812345678",
                        )
                        assert client.call_args.args == ("AA:BB:CC:DD:EE:FF", 0)
                        assert client.call_args.kwargs["transport"] is fake_transport

                await coordinator.async_shutdown()
                await coordinator.async_shutdown()
        assert fake_client.close_calls == 1

    run(exercise())


def test_entities_and_diagnostics_cover_dynamic_and_unknown_values() -> None:
    async def exercise() -> None:
        info = make_info((2, 8, 5))
        status = make_status(
            sentinel=True,
            sensor_status=0x1C,
            volume_status=0x18,
            led_screen_status=0x03,
            auto_upload_status=1,
        )
        received_at = datetime(2025, 1, 1, 0, 0, 10, tzinfo=UTC)
        snapshot = coordinator_module.KC761Snapshot(info, status, received_at)
        coordinator = MagicMock()
        coordinator.device_info = info
        coordinator.snapshot = snapshot
        coordinator.hardware_verified = False
        coordinator.create_raw_entities = False
        coordinator.device_controls_enabled = False
        coordinator.destructive_actions_enabled = False
        coordinator.calibration = make_calibration()
        coordinator.latest_spectrum = None
        coordinator.async_add_listener = MagicMock()
        entry = make_entry()
        entry.runtime_data = coordinator
        with patch.object(
            sensor_module.CoordinatorEntity,
            "__init__",
            lambda self, value: setattr(self, "coordinator", value),
        ):
            sensor_entities = []
            await sensor_module.async_setup_entry(MagicMock(), entry, lambda entities: sensor_entities.extend(entities))
            assert len(sensor_entities) == 76
            by_key = {
                entity.entity_description.key: entity
                for entity in sensor_entities
                if hasattr(entity, "entity_description")
            }
            assert all(
                entity.extra_state_attributes["hardware_verified"] is False
                for entity in sensor_entities
            )
            assert by_key["battery_percentage"].native_value == 87
            assert by_key["device_time"].native_value == status.device_time
            assert by_key["clock_offset"].native_value == -10
            assert by_key["coprocessor_firmware"].native_value == "V1.80"
            assert by_key["device_id"].native_value == info.device_id
            assert by_key["gamma_sensor_model"].native_value == "KC7601.24 CsI"
            assert by_key["neutron_sensor_model"].native_value == "KC7601.31 6Li"
            assert by_key["pin_sensor_model"].native_value == "PIN"
            assert by_key["gamma_avg_cps"].native_value is None
            assert by_key["gamma_avg_dose_rate"].entity_description.entity_registry_enabled_default
            assert by_key["gamma_spectrum_runtime"].native_value == 3600
            assert by_key["gamma_dose_runtime"].native_value == 7200
            assert by_key["gamma_cumulative_dose"].native_value == 1.0
            assert by_key["gamma_cumulative_dose_equivalent"].native_value == 2.0
            assert by_key["gamma_cumulative_dose"].entity_description.state_class is None
            assert by_key["gamma_cumulative_dose_equivalent"].entity_description.state_class is None
            assert by_key["gamma_spectrum_runtime"].entity_description.native_unit_of_measurement == "s"
            assert by_key["gamma_dose_runtime"].entity_description.native_unit_of_measurement == "s"
            assert by_key["gamma_cumulative_dose"].entity_description.native_unit_of_measurement == "mGy"
            assert by_key["gamma_cumulative_dose_equivalent"].entity_description.native_unit_of_measurement == "mSv"
            assert by_key["rad0_trigger_offset"].native_value == 0
            assert by_key["rad2_trigger_offset"].native_value == 0
            assert by_key["rad0_dose_zoom"].native_value == 1.0
            assert by_key["rad1_dose_zoom"].native_value == 1.0
            assert by_key["rad2_dose_zoom"].native_value == 1.0
            assert by_key["neu_window_center"].native_value == 0
            assert by_key["altitude_m_offset"].native_value == 0
            assert by_key["active_sensor_state"].native_value == "gamma"
            coordinator.snapshot = replace(snapshot, status=make_status(sensor_status=0x03))
            assert by_key["active_sensor_state"].native_value is None
            coordinator.snapshot = snapshot
            assert by_key["global_volume_state"].native_value == "mute"
            assert by_key["backlight_brightness_state"].native_value == "dim"
            assert by_key["energy_calibration_version"].native_value == "V2"
            assert by_key["energy_calibration_selection"].native_value == "factory"
            assert by_key["gamma_energy_zoom"].native_value == 1.0
            assert by_key["gamma_custom_energy_coefficient_a"].native_value == 0.0
            assert by_key["gamma_factory_energy_node2"].native_value == 1536
            assert all(
                by_key[key].entity_description.state_class is None
                for key in (
                    "rad0_trigger_offset",
                    "rad2_trigger_offset",
                    "rad0_dose_zoom",
                    "rad1_dose_zoom",
                    "rad2_dose_zoom",
                    "neu_window_center",
                    "altitude_m_offset",
                )
            )
            assert not any("_raw_" in key for key in by_key)
            device_info = by_key["device_id"].device_info
            assert device_info["serial_number"] == info.device_id
            assert device_info["model_id"] == str(info.device_model)
            spectrum_entity = next(
                entity for entity in sensor_entities if isinstance(entity, sensor_module.KC761SpectrumSensor)
            )
            assert spectrum_entity.native_value is None
            assert spectrum_entity.extra_state_attributes["points"] == []
            assert spectrum_entity.extra_state_attributes["device_controls_enabled"] is False
            assert spectrum_entity.extra_state_attributes["destructive_actions_enabled"] is False

            with patch.object(
                binary_sensor_module.CoordinatorEntity,
                "__init__",
                lambda self, value: setattr(self, "coordinator", value),
            ):
                binary_entities = []
                await binary_sensor_module.async_setup_entry(
                    MagicMock(), entry, lambda entities: binary_entities.extend(entities)
                )
            assert len(binary_entities) == 9
            binary_by_key = {
                entity.entity_description.key: entity for entity in binary_entities
            }
            assert set(binary_by_key) == {
                "auto_upload_status",
                "key_tone_status",
                "count_tone_status",
                "dense_count_effect_status",
                "count_flash_status",
                "backlight_status",
                "gamma_spectrum_accumulating_status",
                "neutron_spectrum_accumulating_status",
                "pin_spectrum_accumulating_status",
            }
            assert binary_by_key["auto_upload_status"].is_on is True
            assert binary_by_key["key_tone_status"].is_on is True
            assert binary_by_key["count_tone_status"].is_on is False
            assert binary_by_key["dense_count_effect_status"].is_on is True
            assert binary_by_key["count_flash_status"].is_on is True
            assert binary_by_key["backlight_status"].is_on is True
            assert all(
                binary_by_key[f"{channel}_spectrum_accumulating_status"].is_on
                for channel in ("gamma", "neutron", "pin")
            )
            snapshot = replace(
                snapshot,
                status=replace(status, auto_upload_status=0),
            )

            coordinator.create_raw_entities = True
            raw_entities = []
            await sensor_module.async_setup_entry(MagicMock(), entry, lambda entities: raw_entities.extend(entities))
            assert len(raw_entities) == 85
            raw_descriptions = [
                entity.entity_description
                for entity in raw_entities
                if hasattr(entity, "entity_description")
                and entity.entity_description.raw_reading
            ]
            assert len(raw_descriptions) == 9
            assert all(description.entity_registry_enabled_default for description in raw_descriptions)

            coordinator.create_raw_entities = False
            partial_info = make_info((2, 0, 5))
            coordinator.device_info = partial_info
            coordinator.snapshot = replace(snapshot, device_info=partial_info)
            partial_entities = []
            await sensor_module.async_setup_entry(
                MagicMock(), entry, lambda entities: partial_entities.extend(entities)
            )
            partial_keys = {
                entity.entity_description.key
                for entity in partial_entities
                if hasattr(entity, "entity_description")
            }
            assert len(partial_entities) == 69
            assert "gamma_cumulative_dose" in partial_keys
            assert "pin_cumulative_dose" in partial_keys
            assert "neutron_cumulative_dose" not in partial_keys

            reset_info = replace(
                info,
                cumulative=(
                    CumulativeChannel(0, 0, 0.0, 0.0),
                    CumulativeChannel(0, 0, 0.0, 0.0),
                    CumulativeChannel(0, 0, 0.0, 0.0),
                ),
            )
            coordinator.device_info = info
            coordinator.snapshot = replace(snapshot, device_info=reset_info)
            assert by_key["gamma_spectrum_runtime"].native_value == 0
            assert by_key["gamma_dose_runtime"].native_value == 0
            assert by_key["gamma_cumulative_dose"].native_value == 0.0
            assert by_key["gamma_cumulative_dose_equivalent"].native_value == 0.0

            coordinator.snapshot = None
            assert by_key["battery_percentage"].native_value is None
            coordinator.snapshot = snapshot
            coordinator.create_raw_entities = False
        empty = MagicMock()
        empty.device_info = None
        entry.runtime_data = empty
        entities = []
        await sensor_module.async_setup_entry(MagicMock(), entry, lambda values: entities.extend(values))
        assert entities == []

        coordinator.device_info = info
        coordinator.snapshot = snapshot
        entry.runtime_data = coordinator
        coordinator.async_set_auto_upload = AsyncMock()
        coordinator.async_set_spectrum_accumulating = AsyncMock()
        coordinator.async_set_volume_flag = AsyncMock()
        coordinator.async_set_led_flag = AsyncMock()
        coordinator.async_set_active_sensor = AsyncMock()
        coordinator.async_set_volume = AsyncMock()
        coordinator.async_sync_device_time = AsyncMock()
        coordinator.async_capture_spectrum = AsyncMock()
        coordinator.async_save_spectrum = AsyncMock()
        coordinator.async_clear_statistics = AsyncMock()
        coordinator.device_controls_enabled = True
        coordinator.destructive_actions_enabled = True

        with patch.object(
            switch_module.CoordinatorEntity,
            "__init__",
            lambda self, value: setattr(self, "coordinator", value),
        ):
            switches = []
            await switch_module.async_setup_entry(
                MagicMock(), entry, lambda values: switches.extend(values)
            )
            assert len(switches) == 9
            switch_by_key = {entity.entity_description.key: entity for entity in switches}
            assert switch_by_key["auto_upload"].is_on is False
            assert switch_by_key["gamma_spectrum_accumulating"].is_on is True
            assert switch_by_key["neutron_spectrum_accumulating"].is_on is True
            assert switch_by_key["pin_spectrum_accumulating"].is_on is True
            await switch_by_key["auto_upload"].async_turn_on()
            coordinator.async_set_auto_upload.assert_awaited_once_with(True)
            await switch_by_key["count_tone"].async_turn_off()
            coordinator.async_set_volume_flag.assert_awaited_once_with(2, False)
            await switch_by_key["backlight"].async_turn_on()
            coordinator.async_set_led_flag.assert_awaited_once_with(1, True)

        with patch.object(
            select_module.CoordinatorEntity,
            "__init__",
            lambda self, value: setattr(self, "coordinator", value),
        ):
            selects = []
            await select_module.async_setup_entry(
                MagicMock(), entry, lambda values: selects.extend(values)
            )
            select_by_key = {entity.entity_description.key: entity for entity in selects}
            assert select_by_key["active_sensor"].options == ["gamma", "neutron", "pin"]
            assert select_by_key["active_sensor"].current_option == "gamma"
            assert select_by_key["global_volume"].current_option == "mute"
            assert select_by_key["backlight_brightness"].current_option == "dim"
            await select_by_key["active_sensor"].async_select_option("pin")
            coordinator.async_set_active_sensor.assert_awaited_once_with(2)
            await select_by_key["global_volume"].async_select_option("high")
            coordinator.async_set_volume.assert_awaited_once_with(2)
            with pytest.raises(ValueError):
                await select_by_key["global_volume"].async_select_option("invalid")

        with patch.object(
            button_module.CoordinatorEntity,
            "__init__",
            lambda self, value: setattr(self, "coordinator", value),
        ):
            buttons = []
            await button_module.async_setup_entry(
                MagicMock(), entry, lambda values: buttons.extend(values)
            )
            assert len(buttons) == 12
            button_by_key = {entity.entity_description.key: entity for entity in buttons}
            sync_button = button_by_key["sync_device_time"]
            assert sync_button.extra_state_attributes["device_id"] == info.device_id
            await sync_button.async_press()
            coordinator.async_sync_device_time.assert_awaited_once()
            await button_by_key["save_spectrum"].async_press()
            coordinator.async_save_spectrum.assert_awaited_once()
            await button_by_key["clear_gamma_spectrum"].async_press()
            coordinator.async_clear_statistics.assert_awaited_once_with(0, "spectrum")

        coordinator.device_controls_enabled = False
        coordinator.destructive_actions_enabled = False
        switches = []
        await switch_module.async_setup_entry(
            MagicMock(), entry, lambda values: switches.extend(values)
        )
        assert switches == []
        selects = []
        await select_module.async_setup_entry(
            MagicMock(), entry, lambda values: selects.extend(values)
        )
        assert selects == []
        with patch.object(
            button_module.CoordinatorEntity,
            "__init__",
            lambda self, value: setattr(self, "coordinator", value),
        ):
            safe_buttons = []
            await button_module.async_setup_entry(
                MagicMock(), entry, lambda values: safe_buttons.extend(values)
            )
        assert {button.entity_description.key for button in safe_buttons} == {
            "refresh_spectrum",
            "save_spectrum",
        }

        coordinator.client = SimpleNamespace(
            connected=False,
            last_frame=RawFrame(1, 0xA2, 81, b"", b""),
            received_frame_flags={0xA1: 9, 0xA2: 2, 0xA3: 1, 0xA4: 3},
            client_instance_token="ephemeral-test-token",
            data_errors=2,
            consecutive_protocol_errors=1,
            responses=11,
            push_frames=4,
            late_responses=2,
            unhandled_frames=3,
            parser=SimpleNamespace(
                errors=3,
                dropped_bytes=7,
                buffered_bytes=0,
                recent_errors=("length:0",),
            ),
        )
        coordinator.status_interval = 5
        coordinator.info_interval = 60
        coordinator.create_raw_entities = False
        coordinator.time_sync_enabled = False
        coordinator.time_sync_interval = 24
        coordinator.spectrum_save_enabled = False
        coordinator.spectrum_save_interval = 60
        coordinator.control_commands = 0
        coordinator.last_time_sync = None
        coordinator.latest_spectrum = None
        coordinator.last_spectrum_save = None
        coordinator.last_success = None
        coordinator.consecutive_failures = 2
        coordinator.reconnects = 1
        coordinator.protocol_errors = 3
        coordinator.identity_mismatches = 1
        coordinator.retry_delay = 2.5
        coordinator.last_error = "TimeoutError"
        coordinator.transport_type = "ble"
        entry.runtime_data = coordinator
        result = await diagnostics_module.async_get_config_entry_diagnostics(MagicMock(), entry)
        assert "host" not in str(result)
        assert info.device_id not in str(result)
        assert result["protocol_profile"] == "manual_v1_8_candidate"
        assert result["hardware_verified"] is False
        assert result["device"]["device_id_suffix"] == "0001"
        assert result["connection"]["last_frame"]["flag"] == 0xA2
        assert result["connection"]["data_errors"] == 2
        assert result["connection"]["responses"] == 11
        assert result["connection"]["received_frame_flags"] == {
            0xA1: 9,
            0xA2: 2,
            0xA3: 1,
            0xA4: 3,
        }
        assert result["connection"]["received_frame_flags_scope"] == "client_lifetime"
        assert result["connection"]["received_frame_flags_basis"] == (
            "complete_routed_frames_before_semantic_decode"
        )
        assert result["connection"]["client_instance_token"] == "ephemeral-test-token"
        result["connection"]["received_frame_flags"][0xA1] = 99
        result = await diagnostics_module.async_get_config_entry_diagnostics(MagicMock(), entry)
        assert result["connection"]["received_frame_flags"][0xA1] == 9
        assert result["connection"]["transport"] == "ble"
        assert result["connection"]["identity_mismatches"] == 1
        assert result["framing"]["dropped_bytes"] == 7

        coordinator.last_success = datetime.now(UTC)
        result = await diagnostics_module.async_get_config_entry_diagnostics(MagicMock(), entry)
        assert result["connection"]["last_success"] is not None

        disabled_entry = SimpleNamespace(
            version=7,
            data={
                "transport": "tcp",
                "host": "secret.example",
                "port": 7601,
                "device_id": "7601-0000-000001",
            },
            options={
                CONF_GAMMA_SPECTRUM_CHANNELS: "1024",
                CONF_NEUTRON_SPECTRUM_CHANNELS: 4096,
                CONF_PIN_SPECTRUM_CHANNELS: "invalid",
            },
        )
        result = await diagnostics_module.async_get_config_entry_diagnostics(
            MagicMock(), disabled_entry
        )
        assert result["device"] is None
        assert result["connection"]["connected"] is False
        assert result["connection"]["transport"] == "tcp"
        assert result["spectrum"]["channel_counts"] is None
        assert result["spectrum"]["legacy_channel_counts"] == [1024, 4096, 2048]
        assert "secret.example" not in str(result)
        assert "7601-0000-000001" not in str(result)

        unloaded_entry = make_entry()
        unloaded_entry.data = {
            "transport": "ble",
            "ble_address": "AA:BB:CC:DD:EE:FF",
            "ble_service_uuid": "secret-uuid",
        }
        unloaded_entry.runtime_data = None
        result = await diagnostics_module.async_get_config_entry_diagnostics(
            MagicMock(), unloaded_entry
        )
        assert result["connection"]["transport"] == "ble"
        assert result["framing"] is None

        empty = SimpleNamespace(
            device_info=None,
            status_interval=5,
            info_interval=60,
            create_raw_entities=False,
            time_sync_enabled=False,
            time_sync_interval=24,
            spectrum_save_enabled=False,
            spectrum_save_interval=60,
            client=SimpleNamespace(connected=False, last_frame=None),
            last_success=None,
            consecutive_failures=0,
            reconnects=0,
            protocol_errors=0,
            identity_mismatches=0,
            retry_delay=None,
            last_error=None,
            control_commands=0,
            last_time_sync=None,
            latest_spectrum=None,
            last_spectrum_save=None,
        )
        empty_entry = make_entry()
        empty_entry.version = 1
        empty_entry.runtime_data = empty
        result = await diagnostics_module.async_get_config_entry_diagnostics(MagicMock(), empty_entry)
        assert result["device"] is None
        assert result["connection"]["last_frame"] is None
        assert result["connection"]["last_success"] is None
        assert result["framing"] is None

        unknown = SimpleNamespace(**empty.__dict__)
        unknown.device_info = make_info(model=0xFE)
        unknown_entry = make_entry()
        unknown_entry.runtime_data = unknown
        result = await diagnostics_module.async_get_config_entry_diagnostics(MagicMock(), unknown_entry)
        assert result["device"]["compatibility_warnings"] == ["unknown_model:254"]

        short_id = SimpleNamespace(**empty.__dict__)
        short_id.device_info = make_info(device_id="AB")
        short_entry = make_entry()
        short_entry.runtime_data = short_id
        result = await diagnostics_module.async_get_config_entry_diagnostics(MagicMock(), short_entry)
        assert result["device"]["device_id_suffix"] == "<2 characters>"
        assert "AB" not in str(result)

    run(exercise())


class _FakeEntityRegistry:
    """Minimal registry snapshot used to verify legacy IDs are untouched."""

    def __init__(self, unique_ids: set[str]) -> None:
        self.unique_ids = unique_ids
        self.async_update_entity = MagicMock()
        self.async_remove = MagicMock()


def _collect_sensor_entities(
    *,
    create_raw_entities: bool,
    registry_unique_ids: set[str],
    options: dict | None = None,
) -> tuple[dict[str, object], _FakeEntityRegistry, MagicMock]:
    info = make_info((2, 8, 5))
    coordinator = MagicMock()
    coordinator.device_info = info
    coordinator.snapshot = coordinator_module.KC761Snapshot(
        info,
        make_status(),
        datetime(2025, 1, 1, 0, 0, 10, tzinfo=UTC),
    )
    coordinator.hardware_verified = False
    coordinator.create_raw_entities = create_raw_entities
    coordinator.calibration = make_calibration()
    coordinator.latest_spectrum = None
    coordinator.device_controls_enabled = False
    coordinator.destructive_actions_enabled = False
    coordinator.async_add_listener = MagicMock()
    entry = make_entry(options=options)
    entry.runtime_data = coordinator
    registry = _FakeEntityRegistry(registry_unique_ids)
    entities: list[object] = []

    async def exercise() -> None:
        with patch.object(
            sensor_module.CoordinatorEntity,
            "__init__",
            lambda self, value: setattr(self, "coordinator", value),
        ):
            await sensor_module.async_setup_entry(
                MagicMock(), entry, lambda values: entities.extend(values)
            )

    run(exercise())
    return (
        {
            entity.entity_description.key: entity
            for entity in entities
            if hasattr(entity, "entity_description")
        },
        registry,
        coordinator,
    )


def test_dose_option_removed_new_install_keeps_canonical_entities() -> None:
    info = make_info((2, 8, 5))
    by_key, registry, _coordinator = _collect_sensor_entities(
        create_raw_entities=True,
        registry_unique_ids=set(),
        options={"dose_display_unit": "micro"},
    )
    assert not any(key.endswith("_micro") for key in by_key)
    assert registry.async_update_entity.call_count == 0
    for key, entity in by_key.items():
        assert entity._attr_unique_id == f"{info.device_id}_{key}"
    assert by_key["gamma_avg_dose_rate"].native_value == pytest.approx(0.01, abs=1e-4)
    assert by_key["gamma_avg_dose_rate"].entity_description.native_unit_of_measurement == "mGy/h"
    assert by_key["gamma_avg_dose_equivalent_rate"].entity_description.native_unit_of_measurement == "mSv/h"
    assert by_key["gamma_cumulative_dose"].native_value == 1.0
    assert by_key["gamma_cumulative_dose"].entity_description.native_unit_of_measurement == "mGy"
    assert by_key["gamma_cumulative_dose_equivalent"].native_value == 2.0
    assert by_key["gamma_cumulative_dose_equivalent"].entity_description.native_unit_of_measurement == "mSv"


def test_existing_micro_registry_entities_are_not_loaded_or_mutated() -> None:
    info = make_info((2, 8, 5))
    micro_keys = {
        f"{channel}_{measurement}_micro"
        for channel in ("gamma", "neutron", "pin")
        for measurement in (
            "avg_dose_rate",
            "avg_dose_equivalent_rate",
            "raw_dose_rate",
            "raw_dose_equivalent_rate",
            "cumulative_dose",
            "cumulative_dose_equivalent",
        )
    }
    by_key, registry, _coordinator = _collect_sensor_entities(
        create_raw_entities=True,
        registry_unique_ids={f"{info.device_id}_{key}" for key in micro_keys},
        options={"dose_display_unit": "micro"},
    )
    assert not micro_keys & by_key.keys()
    assert registry.unique_ids == {
        f"{info.device_id}_{key}" for key in micro_keys
    }
    assert registry.async_update_entity.call_count == 0
    assert registry.async_remove.call_count == 0
    assert by_key["gamma_avg_dose_rate"].native_value == pytest.approx(0.01, abs=1e-4)
    assert by_key["gamma_avg_dose_rate"].entity_description.native_unit_of_measurement == "mGy/h"
    assert by_key["gamma_avg_dose_equivalent_rate"].native_value == pytest.approx(0.02, abs=1e-4)
    assert by_key["gamma_avg_dose_equivalent_rate"].entity_description.native_unit_of_measurement == "mSv/h"
    assert by_key["gamma_cumulative_dose"].native_value == 1.0
    assert by_key["gamma_cumulative_dose"].entity_description.native_unit_of_measurement == "mGy"
    assert by_key["gamma_cumulative_dose_equivalent"].native_value == 2.0
    assert by_key["gamma_cumulative_dose_equivalent"].entity_description.native_unit_of_measurement == "mSv"
    assert "gamma_avg_cps_micro" not in by_key


def test_raw_entities_follow_raw_option_without_micro_compatibility() -> None:
    info = make_info((2, 8, 5))
    raw_micro_unique_id = f"{info.device_id}_gamma_raw_dose_rate_micro"
    disabled_by_option, registry, _coordinator = _collect_sensor_entities(
        create_raw_entities=False,
        registry_unique_ids={raw_micro_unique_id},
    )
    assert "gamma_raw_dose_rate" not in disabled_by_option
    assert "gamma_raw_dose_rate_micro" not in disabled_by_option
    assert registry.unique_ids == {raw_micro_unique_id}
    assert registry.async_update_entity.call_count == 0
    assert registry.async_remove.call_count == 0
    enabled_by_option, registry, _coordinator = _collect_sensor_entities(
        create_raw_entities=True,
        registry_unique_ids={raw_micro_unique_id},
    )
    assert "gamma_raw_dose_rate" in enabled_by_option
    assert "gamma_raw_dose_rate_micro" not in enabled_by_option
    assert registry.unique_ids == {raw_micro_unique_id}
    assert registry.async_update_entity.call_count == 0
    assert registry.async_remove.call_count == 0


def test_spectrum_entity_skips_unchanged_coordinator_updates() -> None:
    info = make_info((2, 0, 0))
    coordinator = SimpleNamespace(
        device_info=info,
        latest_spectrum=SpectrumSnapshot.create(
            source=0,
            acquired_at=datetime(2025, 1, 1, tzinfo=UTC),
            accumulation_seconds=1,
            counts=(1,) * 2048,
            energy_kev=(0.0,) * 2048,
            segment_count=1,
            calibration_profile="factory:1",
        ),
        hardware_verified=False,
        last_update_success=True,
        device_controls_enabled=False,
        destructive_actions_enabled=False,
        entry=SimpleNamespace(entry_id="entry-1"),
    )
    with patch.object(
        sensor_module.CoordinatorEntity,
        "__init__",
        lambda self, value: setattr(self, "coordinator", value),
    ):
        entity = sensor_module.KC761SpectrumSensor(coordinator)
    entity.async_write_ha_state = MagicMock()

    entity._handle_coordinator_update()
    entity._handle_coordinator_update()
    assert entity.async_write_ha_state.call_count == 1

    coordinator.latest_spectrum = SpectrumSnapshot.create(
        source=0,
        acquired_at=datetime(2025, 1, 1, 0, 0, 1, tzinfo=UTC),
        accumulation_seconds=1,
        counts=(1,) * 2048,
        energy_kev=(0.0,) * 2048,
        segment_count=1,
        calibration_profile="factory:1",
    )
    entity._handle_coordinator_update()
    assert entity.async_write_ha_state.call_count == 2

    coordinator.last_update_success = False
    entity._handle_coordinator_update()
    assert entity.async_write_ha_state.call_count == 3

    coordinator.device_controls_enabled = True
    entity._handle_coordinator_update()
    assert entity.async_write_ha_state.call_count == 4

    coordinator.destructive_actions_enabled = True
    entity._handle_coordinator_update()
    assert entity.async_write_ha_state.call_count == 5
