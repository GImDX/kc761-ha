"""Home Assistant runtime coverage without pytest-specific HA plugins.

The module is skipped when Home Assistant is unavailable on the developer
machine.  The target HA image supplies the real package and runs these tests
with ordinary pytest; every coroutine is driven with ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
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
    async_setup,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.kc761 import button as button_module  # noqa: E402
from custom_components.kc761 import config_flow as config_flow_module  # noqa: E402
from custom_components.kc761 import coordinator as coordinator_module  # noqa: E402
from custom_components.kc761 import diagnostics as diagnostics_module  # noqa: E402
from custom_components.kc761 import select as select_module  # noqa: E402
from custom_components.kc761 import sensor as sensor_module  # noqa: E402
from custom_components.kc761 import switch as switch_module  # noqa: E402
from custom_components.kc761 import websocket_api as websocket_module  # noqa: E402
from custom_components.kc761.const import DOMAIN  # noqa: E402
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
from custom_components.kc761.spectrum import SpectrumSnapshot  # noqa: E402


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


def test_entry_setup_unload_reload_and_failure() -> None:
    hass = make_hass()
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
        "/kc761/kc761-spectrum-card.js?v=0.2.0-alpha.37",
    )
    hass.http.async_register_static_paths.assert_awaited_once()
    static_paths = hass.http.async_register_static_paths.await_args.args[0]
    assert {path.url_path for path in static_paths} == {
        "/kc761/kc761-spectrum-card.js",
        "/kc761/echarts.esm.min.js",
    }
    assert hass.data[DOMAIN] == {}
    entry = make_entry()
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
    assert run(async_unload_entry(hass, entry)) is True
    fake.async_shutdown.assert_awaited_once()

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
        version=5,
    )
    migration_entry.version = 6
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
        reconfigure.async_update_reload_and_abort = MagicMock(return_value={"type": "abort"})
        assert (await reconfigure.async_step_reconfigure())["type"] == "form"
        with patch.object(config_flow_module, "KC761Client", Client):
            result = await reconfigure.async_step_reconfigure({"host": "new", "port": 7602})
        assert result["type"] == "abort"

        loaded_coordinator = object.__new__(coordinator_module.KC761Coordinator)
        loaded_coordinator.async_reconfigure = AsyncMock()
        hass.data[DOMAIN] = {entry.entry_id: loaded_coordinator}
        loaded = config_flow_module.KC761ConfigFlow()
        loaded.hass = hass
        loaded.context = {"entry_id": entry.entry_id}
        loaded.async_update_reload_and_abort = MagicMock(return_value={"type": "abort"})
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
        options.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        option_values = {
            "status_interval": 5,
            "info_interval": 60,
            "create_raw_entities": True,
            "time_sync_enabled": True,
            "time_sync_interval": 24,
            "spectrum_save_enabled": True,
            "spectrum_save_interval": 60,
        }
        assert (await options.async_step_init(option_values))["type"] == "create_entry"
        options.async_create_entry.assert_called_once_with(title="", data=option_values)

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
        coordinator.client = old
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
        assert coordinator.async_request_refresh.await_count == 8

        with pytest.raises(ValueError):
            await coordinator.async_set_volume(3)
        with pytest.raises(ValueError):
            await coordinator.async_set_active_sensor(3)
        with pytest.raises(ValueError):
            await coordinator.async_clear_statistics(0, "unknown")

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
        assert connection.send_result.call_args.args[1]["serial_number"] == fake.info.device_id
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
        hass.config_entries.async_reload.assert_awaited_with(entry.entry_id)
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


def test_entities_and_diagnostics_cover_dynamic_and_unknown_values() -> None:
    async def exercise() -> None:
        info = make_info((2, 8, 5))
        status = make_status(sentinel=True, sensor_status=0x1C)
        received_at = datetime(2025, 1, 1, 0, 0, 10, tzinfo=UTC)
        snapshot = coordinator_module.KC761Snapshot(info, status, received_at)
        coordinator = MagicMock()
        coordinator.device_info = info
        coordinator.snapshot = snapshot
        coordinator.hardware_verified = False
        coordinator.create_raw_entities = False
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
            assert len(sensor_entities) == 39
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

            coordinator.create_raw_entities = True
            raw_entities = []
            await sensor_module.async_setup_entry(MagicMock(), entry, lambda entities: raw_entities.extend(entities))
            assert len(raw_entities) == 48
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
            assert len(partial_entities) == 32
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

        coordinator.client = SimpleNamespace(
            connected=False,
            last_frame=RawFrame(1, 0xA2, 81, b"", b""),
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
        assert result["connection"]["identity_mismatches"] == 1
        assert result["framing"]["dropped_bytes"] == 7

        coordinator.last_success = datetime.now(UTC)
        result = await diagnostics_module.async_get_config_entry_diagnostics(MagicMock(), entry)
        assert result["connection"]["last_success"] is not None

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
