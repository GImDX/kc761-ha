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
    decode_status,
)
from custom_components.kc761.protocol.framing import FrameParser  # noqa: E402
from custom_components.kc761.protocol.models import RawFrame  # noqa: E402


def run(coro):
    """Run a coroutine without pytest-asyncio."""
    return asyncio.run(coro)


def make_info(sensor_types: tuple[int, int, int] = (2, 0, 0)):
    frame = FrameParser().feed(
        build_device_info_frame(1, sensor_types=sensor_types)
    )[0]
    return decode_device_info(frame)


def make_status(*, sentinel: bool = False, sensor_status: int = 0):
    channels = ((-1, -1, -1, -1, -1, -1),) * 3 if sentinel else None
    frame = FrameParser().feed(
        build_status_frame(1, channels=channels, sensor_status=sensor_status)
    )[0]
    return decode_status(frame)


def make_entry(*, options: dict | None = None, unique_id: str = "7601_0000_000001"):
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
    return hass


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
        self.connects = 0
        self.info_calls = 0
        self.status_calls = 0

    async def async_connect(self):
        self.connects += 1
        if self.error:
            raise self.error
        self.connected = True

    async def async_close(self):
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
        assert (await flow.async_step_user())['type'] == "form"
        with patch.object(config_flow_module, "KC761Client", Client):
            result = await flow.async_step_user({"host": "localhost", "port": 7601})
        assert result["type"] == "create_entry"
        flow.async_set_unique_id.assert_awaited_once_with("7601_0000_000001")

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
        entry.unique_id = "7601_0000_000001"
        hass = make_hass()
        hass.config_entries.async_get_entry.return_value = entry
        hass.config_entries.async_get_known_entry.return_value = entry
        reconfigure = config_flow_module.KC761ConfigFlow()
        reconfigure.hass = hass
        reconfigure.context = {"entry_id": entry.entry_id}
        reconfigure.async_show_form = MagicMock(return_value={"type": "form"})
        reconfigure.async_update_reload_and_abort = MagicMock(return_value={"type": "abort"})
        assert (await reconfigure.async_step_reconfigure())['type'] == "form"
        with patch.object(config_flow_module, "KC761Client", Client):
            result = await reconfigure.async_step_reconfigure({"host": "new", "port": 7602})
        assert result["type"] == "abort"

        unknown = config_flow_module.KC761ConfigFlow()
        unknown.hass = hass
        unknown.context = {"entry_id": "missing"}
        unknown.async_abort = MagicMock(return_value={"type": "abort", "reason": "unknown"})
        hass.config_entries.async_get_entry.return_value = None
        assert (await unknown.async_step_reconfigure())['reason'] == "unknown"
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
        assert (await options.async_step_init())['type'] == "form"
        options.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        assert (await options.async_step_init({"status_interval": 5, "info_interval": 60}))["type"] == "create_entry"

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
        assert fake.info_calls == 1
        assert fake.status_calls == 2
        await coordinator._async_push(SimpleNamespace(data=make_status()))
        await coordinator._async_push(SimpleNamespace(data=None))
        coordinator._device_info = None
        await coordinator._async_push(SimpleNamespace(data=make_status()))
        await coordinator.async_shutdown()
        assert fake.closed is True
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
        await coordinator._async_push(SimpleNamespace(data=make_status()))
        coordinator.async_set_updated_data.assert_not_called()

    run(exercise())


def test_entities_and_diagnostics_cover_dynamic_and_unknown_values() -> None:
    async def exercise() -> None:
        info = make_info((2, 8, 5))
        status = make_status(sentinel=True, sensor_status=0x1C)
        snapshot = coordinator_module.KC761Snapshot(info, status, datetime.now(UTC))
        coordinator = MagicMock()
        coordinator.device_info = info
        coordinator.snapshot = snapshot
        coordinator.hardware_verified = False
        coordinator.async_add_listener = MagicMock()
        entry = make_entry()
        entry.runtime_data = coordinator
        sensor_entities = []
        with patch.object(
            sensor_module.CoordinatorEntity,
            "__init__",
            lambda self, value: setattr(self, "coordinator", value),
        ):
            await sensor_module.async_setup_entry(
                MagicMock(), entry, lambda entities: sensor_entities.extend(entities)
            )
            assert len(sensor_entities) == 21
            assert all(entity.extra_state_attributes["hardware_verified"] is False for entity in sensor_entities)
            assert sensor_entities[0].native_value == 87
            assert sensor_entities[3].native_value is None
            coordinator.snapshot = None
            assert sensor_entities[0].native_value is None
            coordinator.snapshot = snapshot
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
        )
        coordinator.status_interval = 5
        coordinator.info_interval = 60
        coordinator.last_success = None
        coordinator.consecutive_failures = 2
        coordinator.reconnects = 1
        coordinator.protocol_errors = 3
        coordinator.last_error = "TimeoutError"
        entry.runtime_data = coordinator
        result = await diagnostics_module.async_get_config_entry_diagnostics(MagicMock(), entry)
        assert "host" not in str(result)
        assert result["device"]["device_id_suffix"] == "0001"
        assert result["connection"]["last_frame"]["flag"] == 0xA2

        coordinator.last_success = datetime.now(UTC)
        result = await diagnostics_module.async_get_config_entry_diagnostics(MagicMock(), entry)
        assert result["connection"]["last_success"] is not None

        empty = MagicMock()
        empty.device_info = None
        empty.status_interval = 5
        empty.info_interval = 60
        empty.client = SimpleNamespace(connected=False, last_frame=None)
        empty.last_success = None
        empty.consecutive_failures = 0
        empty.reconnects = 0
        empty.protocol_errors = 0
        empty.last_error = None
        empty_entry = make_entry()
        empty_entry.version = 1
        empty_entry.runtime_data = empty
        result = await diagnostics_module.async_get_config_entry_diagnostics(MagicMock(), empty_entry)
        assert result["device"] is None
        assert result["connection"]["last_frame"] is None
        assert result["connection"]["last_success"] is None

    run(exercise())
