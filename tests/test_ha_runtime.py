"""Home Assistant runtime coverage without pytest-specific HA plugins.

The module is skipped when Home Assistant is unavailable on the developer
machine.  The target HA image supplies the real package and runs these tests
with ordinary pytest; every coroutine is driven with ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("homeassistant")

from homeassistant.exceptions import ConfigEntryNotReady  # noqa: E402
from custom_components.kc761 import (  # noqa: E402
    CONFIG_SCHEMA,
    async_migrate_entry,
    async_reload_entry,
    async_setup,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.kc761 import binary_sensor as binary_sensor_module  # noqa: E402
from custom_components.kc761 import config_flow as config_flow_module  # noqa: E402
from custom_components.kc761 import coordinator as coordinator_module  # noqa: E402
from custom_components.kc761 import diagnostics as diagnostics_module  # noqa: E402
from custom_components.kc761 import sensor as sensor_module  # noqa: E402
from custom_components.kc761.const import DOMAIN  # noqa: E402
from custom_components.kc761.protocol.codec import (  # noqa: E402
    build_device_info_frame,
    build_status_frame,
    decode_device_info,
    decode_frame,
    decode_status,
)
from custom_components.kc761.protocol.framing import FrameParser  # noqa: E402
from custom_components.kc761.protocol.models import RawFrame  # noqa: E402


def run(coro):
    """Run a coroutine without pytest-asyncio."""
    return asyncio.run(coro)


def make_info(sensor_types: tuple[int, int, int] = (2, 0, 0), **kwargs):
    frame = FrameParser().feed(build_device_info_frame(1, sensor_types=sensor_types, **kwargs))[0]
    return decode_device_info(frame)


def make_status(*, sentinel: bool = False, sensor_status: int = 0, **kwargs):
    channels = ((-1, -1, -1, -1, -1, -1),) * 3 if sentinel else None
    frame = FrameParser().feed(build_status_frame(1, channels=channels, sensor_status=sensor_status, **kwargs))[0]
    return decode_status(frame)


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
    hass.async_create_task = lambda coroutine, *, name=None: asyncio.create_task(coroutine, name=name)
    return hass


def make_decoded_status(*, upload: bool = True):
    frame = FrameParser().feed(build_status_frame(1, upload=upload))[0]
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


def test_entry_setup_unload_reload_and_failure() -> None:
    hass = make_hass()
    assert callable(CONFIG_SCHEMA)
    assert run(async_setup(hass, {})) is True
    assert hass.data[DOMAIN] == {}
    entry = make_entry()
    fake = MagicMock()
    fake.async_config_entry_first_refresh = AsyncMock()
    fake.async_shutdown = AsyncMock()
    with patch("custom_components.kc761.coordinator.KC761Coordinator", return_value=fake):
        assert run(async_setup_entry(hass, entry)) is True
    assert entry.runtime_data is fake
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
    broken.async_shutdown = AsyncMock()
    with patch("custom_components.kc761.coordinator.KC761Coordinator", return_value=broken):
        with pytest.raises(ConfigEntryNotReady):
            run(async_setup_entry(hass, make_entry()))
    broken.async_shutdown.assert_awaited_once()

    cancelled = MagicMock()
    cancelled.async_config_entry_first_refresh = AsyncMock(side_effect=asyncio.CancelledError)
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
    assert run(async_migrate_entry(hass, migration_entry)) is True
    hass.config_entries.async_update_entry.assert_called_once_with(
        migration_entry,
        unique_id="7601-0000-000001",
        version=2,
    )
    migration_entry.version = 3
    assert run(async_migrate_entry(hass, migration_entry)) is False


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
        coordinator.async_stop_update = MagicMock()
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
        await coordinator._async_push(make_decoded_status())
        await coordinator._async_push(SimpleNamespace(frame=RawFrame(1, 0xA3, 81, b"", b""), data=None))
        coordinator._device_info = None
        await coordinator._async_push(make_decoded_status())
        await coordinator.async_shutdown()
        await coordinator.async_shutdown()
        assert fake.closed is True
        assert fake.close_calls == 1
        coordinator.async_stop_update.assert_called_once()
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
        with patch.object(coordinator_module, "KC761Client", return_value=candidate):
            await coordinator.async_reconfigure("new-host", 7602)
        assert old.closed is True
        assert coordinator.host == "new-host"
        assert candidate.closed is True

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
        with patch.object(coordinator_module, "KC761Client", return_value=bad_candidate):
            with pytest.raises(ValueError):
                await coordinator.async_reconfigure("bad-host", 7603)
        assert bad_candidate.closed is True

        reconnect_candidate = FakeClient(error=OSError("offline"))
        with patch.object(coordinator_module, "KC761Client", return_value=reconnect_candidate):
            with pytest.raises(OSError):
                await coordinator.async_reconfigure("error-host", 7604)
        assert reconnect_candidate.closed is True

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
            assert len(sensor_entities) == 15
            by_key = {entity.entity_description.key: entity for entity in sensor_entities}
            assert all(entity.extra_state_attributes["hardware_verified"] is False for entity in sensor_entities)
            assert by_key["battery_percentage"].native_value == 87
            assert by_key["device_time"].native_value == status.device_time
            assert by_key["clock_offset"].native_value == -10
            assert by_key["coprocessor_firmware"].native_value == "V1.80"
            assert by_key["gamma_avg_cps"].native_value is None
            assert by_key["gamma_avg_dose_rate"].entity_description.entity_registry_enabled_default
            assert not any("_raw_" in key for key in by_key)

            coordinator.create_raw_entities = True
            raw_entities = []
            await sensor_module.async_setup_entry(MagicMock(), entry, lambda entities: raw_entities.extend(entities))
            assert len(raw_entities) == 24
            raw_descriptions = [
                entity.entity_description for entity in raw_entities if entity.entity_description.raw_reading
            ]
            assert len(raw_descriptions) == 9
            assert all(description.entity_registry_enabled_default for description in raw_descriptions)

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

        binary_entities = []
        entry.runtime_data = coordinator
        with patch.object(
            binary_sensor_module.CoordinatorEntity,
            "__init__",
            lambda self, value: setattr(self, "coordinator", value),
        ):
            await binary_sensor_module.async_setup_entry(
                MagicMock(), entry, lambda values: binary_entities.extend(values)
            )
            assert len(binary_entities) == 4
            assert binary_entities[0].is_on is False
            assert binary_entities[1].is_on is True
            assert binary_entities[2].is_on is True
            assert binary_entities[3].is_on is True
            assert all(entity.extra_state_attributes["hardware_verified"] is False for entity in binary_entities)

            coordinator.device_info = make_info((2, 0, 0))
            installed_binary_entities = []
            await binary_sensor_module.async_setup_entry(
                MagicMock(),
                entry,
                lambda values: installed_binary_entities.extend(values),
            )
            assert [entity.entity_description.key for entity in installed_binary_entities] == [
                "auto_upload_enabled",
                "gamma_spectrum_accumulating",
            ]
            coordinator.device_info = info

            invalid_status = make_status(auto_upload_status=7)
            coordinator.snapshot = coordinator_module.KC761Snapshot(info, invalid_status, received_at)
            assert binary_entities[0].is_on is None
        broken_binary = MagicMock(device_info=None)
        with patch.object(
            binary_sensor_module.CoordinatorEntity,
            "__init__",
            lambda self, value: setattr(self, "coordinator", value),
        ):
            with pytest.raises(AssertionError):
                binary_sensor_module.KC761BinarySensor(broken_binary, binary_sensor_module.DESCRIPTIONS[0])
        coordinator.snapshot = None
        assert binary_entities[0].is_on is None

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
            client=SimpleNamespace(connected=False, last_frame=None),
            last_success=None,
            consecutive_failures=0,
            reconnects=0,
            protocol_errors=0,
            identity_mismatches=0,
            retry_delay=None,
            last_error=None,
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
