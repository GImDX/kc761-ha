"""Boundary and cleanup contracts supporting independent branch coverage."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import custom_components.kc761 as integration
from custom_components.kc761 import binary_sensor, button, config_flow, diagnostics, select, sensor, switch
from custom_components.kc761.protocol import transport as transport_module
from custom_components.kc761.protocol.transport import BleTransport, TransportError
from tests.check_branch_coverage import check_report
from tests.test_ble_transport import _characteristic, _services
from tests.test_ha_runtime import FakeClient, make_calibration, make_entry, make_hass
from tests.test_review_regressions import make_coordinator


@pytest.mark.parametrize("covered,total,passes", [(95, 100, True), (94, 100, False), (950, 1001, False)])
def test_branch_gate_uses_unrounded_independent_fraction(covered, total, passes):
    report = {"meta": {"branch_coverage": True}, "totals": {
        "covered_branches": covered, "num_branches": total, "percent_covered": 99.99,
    }}
    if passes:
        assert check_report(report) == (covered, total)
    else:
        with pytest.raises(ValueError, match="branch coverage"):
            check_report(report)


@pytest.mark.parametrize("report", [{}, {"meta": {"branch_coverage": False}},
    {"meta": {"branch_coverage": True}},
    {"meta": {"branch_coverage": True}, "totals": {"covered_branches": 0, "num_branches": 0}},
    {"meta": {"branch_coverage": True}, "totals": {"covered_branches": True, "num_branches": 1}},
    {"meta": {"branch_coverage": True}, "totals": {"covered_branches": 2, "num_branches": 1}},
])
def test_branch_gate_rejects_absent_or_invalid_data(report):
    with pytest.raises(ValueError):
        check_report(report)


@pytest.mark.parametrize("platform", [binary_sensor, button, select, switch, sensor])
def test_platform_setup_does_not_create_entities_without_information(platform):
    coordinator = make_coordinator(FakeClient())
    coordinator._device_info = None
    coordinator.entry.runtime_data = coordinator
    add = MagicMock()
    asyncio.run(platform.async_setup_entry(coordinator._hass, coordinator.entry, add))
    add.assert_not_called()


def test_entity_getters_are_null_without_snapshot_and_brightness_is_explicit():
    async def exercise():
        coordinator = make_coordinator(FakeClient())
        coordinator.async_set_led_flag = AsyncMock()
        scalar = sensor.KC761Sensor(coordinator, sensor.DEVICE_DESCRIPTIONS[0])
        binary = binary_sensor.KC761BinarySensor(coordinator, binary_sensor.DESCRIPTIONS[0])
        switching = switch.KC761Switch(coordinator, switch.DESCRIPTIONS[0])
        selecting = select.KC761Select(coordinator, select.DESCRIPTIONS[2])
        original = coordinator._snapshot
        coordinator._snapshot = None
        assert scalar.native_value is binary.is_on is switching.is_on is selecting.current_option is None
        coordinator._snapshot = original
        assert selecting.options == ["dim", "bright"]
        await selecting.async_select_option("bright")
        coordinator.async_set_led_flag.assert_awaited_once_with(2, True)
        led = switch.KC761Switch(coordinator, switch.DESCRIPTIONS[4])
        assert led.is_on is bool(original.status.led_screen_status & 1)
        await led.async_turn_off()
        coordinator.async_set_led_flag.assert_awaited_with(0, False)
        clock_description = next(item for item in sensor.DEVICE_DESCRIPTIONS if item.key == "clock_offset")
        clock = sensor.KC761Sensor(coordinator, clock_description)
        coordinator._snapshot = replace(original, status=replace(original.status, device_time=None))
        assert clock.native_value is None

    asyncio.run(exercise())


def test_buttons_skip_absent_channels_and_route_save_to_coordinator():
    async def exercise():
        coordinator = make_coordinator(FakeClient())
        coordinator.destructive_actions_enabled = True
        coordinator.entry.runtime_data = coordinator
        coordinator.async_save_spectrum = AsyncMock()
        entities = []
        await button.async_setup_entry(coordinator._hass, coordinator.entry, entities.extend)
        keys = {item.entity_description.key for item in entities}
        assert not any("neutron" in key or "pin" in key for key in keys)
        save = next(item for item in entities if item.entity_description.action == "save_spectrum")
        await save.async_press()
        coordinator.async_save_spectrum.assert_awaited_once()

    asyncio.run(exercise())


def test_migration_preserves_missing_registry_rows_and_user_disabled_raw_sensors():
    async def exercise():
        hass, entry = make_hass(), make_entry(options={"create_raw_entities": True})
        entry.data = {}
        assert await integration.async_migrate_entry(hass, entry) is False
        hass.config_entries.async_update_entry.assert_not_called()
        entry.data = {"device_id": "offline-id"}
        with patch.object(integration, "er", None):
            assert await integration.async_migrate_entry(hass, entry) is True
        registry = MagicMock()
        registry.async_get_entity_id.return_value = "sensor.missing"
        registry.async_get.return_value = None
        integration._migrate_sensor_registry(registry, entry, "offline-id")
        registry.async_update_entity.assert_not_called()
        registry.async_get.return_value = SimpleNamespace(disabled_by=integration.er.RegistryEntryDisabler.USER)
        integration._migrate_sensor_registry(registry, entry, "offline-id")
        assert all(item.kwargs == {"entity_category": None} for item in registry.async_update_entity.call_args_list)
        entry.runtime_data = None
        assert await integration.async_unload_entry(hass, entry) is True

    asyncio.run(exercise())


def test_unknown_transport_diagnostics_fall_back_without_exposing_endpoint():
    async def exercise():
        coordinator = make_coordinator(FakeClient())
        coordinator.transport_type = "unsupported"
        coordinator.entry.runtime_data = coordinator
        report = await diagnostics.async_get_config_entry_diagnostics(coordinator._hass, coordinator.entry)
        assert "unsupported" not in str(report)
        coordinator.entry.runtime_data = None
        coordinator.entry.data["transport"] = "unsupported"
        report = await diagnostics.async_get_config_entry_diagnostics(coordinator._hass, coordinator.entry)
        assert "unsupported" not in str(report)

    asyncio.run(exercise())


def test_calibration_rejects_negative_channel_and_handles_older_factory_version():
    calibration = make_calibration()
    with pytest.raises(ValueError, match="non-negative"):
        calibration.energy_kev(0, -1)
    legacy = replace(calibration, factory_version=1, channel0_factory_middle=(0, 0, 2, 3))
    assert legacy.energy_kev(0, 7) == 17 * legacy.energy_zoom[0] + legacy.energy_offset[0]


def test_ble_error_forms_keep_transport_specific_schemas():
    values = {"transport": "ble", "ble_address": ""}
    assert config_flow._endpoint_schema(values).schema
    assert config_flow._reconfigure_endpoint_step(values) == "reconfigure_ble"
    assert config_flow._reconfigure_endpoint_schema(values).schema


def test_ble_queue_bounds_disconnection_and_write_preconditions():
    async def exercise():
        with pytest.raises(ValueError, match="positive"):
            BleTransport(SimpleNamespace(), "AA:BB", queue_size=0)
        transport = BleTransport(SimpleNamespace(), "AA:BB", queue_size=1)
        with pytest.raises(TransportError, match="not connected"):
            await transport.async_read()
        with pytest.raises(TransportError, match="not connected"):
            await transport.async_write(b"read-only-test")
        transport._client = SimpleNamespace(is_connected=True)
        with pytest.raises(ValueError, match="positive"):
            await transport.async_read(0)
        transport._on_disconnect(None)
        with pytest.raises(TransportError, match="lost"):
            await transport.async_read()
        transport._on_notification(None, b"first")
        transport._on_disconnect(None)
        transport._on_notification(None, b"ignored")
        assert transport._notifications.qsize() == 1
        assert transport._notification_overflow
        transport._rx_characteristic = SimpleNamespace(properties=[])
        await transport.async_write(b"")
        with pytest.raises(TransportError, match="not writable"):
            await transport.async_write(b"read-only-test")
        transport._rx_characteristic = SimpleNamespace(properties=["write"])
        transport._client.write_gatt_char = AsyncMock(side_effect=OSError("mock failure"))
        transport._client.disconnect = AsyncMock()
        with pytest.raises(TransportError, match="write failed"):
            await transport.async_write(b"read-only-test")
        assert transport._client is None
        await transport.async_close()

    asyncio.run(exercise())


def test_ble_backend_variants_and_failed_notification_cleanup():
    async def exercise():
        assert transport_module._iter_services(None) == ()
        assert transport_module._iter_services(SimpleNamespace(services={"one": _services()[0]})) == _services()
        mixed = SimpleNamespace(uuid=transport_module.BLE_VERIFIED_SERVICE_UUID, characteristics=(
            _characteristic("12345678-1234-5678-1234-567812345678", "read"), *_services()[0].characteristics,
        ))
        assert transport_module._find_characteristics((mixed,), service_uuid=None)[0].uuid == (
            transport_module.BLE_RX_CHARACTERISTIC_UUID
        )
        transport = BleTransport(SimpleNamespace(), "AA:BB")
        client = SimpleNamespace(is_connected=True, stop_notify=AsyncMock(side_effect=OSError("already gone")),
                                 disconnect=AsyncMock())
        transport._client = client
        transport._tx_characteristic = object()
        await transport.async_close()
        client.disconnect.assert_awaited_once()
        assert transport._client is None
        await transport_module._disconnect_ble_client(None)

    asyncio.run(exercise())


def test_ble_connect_shortcuts_and_resolver_failure():
    async def exercise():
        import homeassistant.components as components

        bluetooth = SimpleNamespace(async_ble_device_from_address=MagicMock())

        transport = BleTransport(SimpleNamespace(), "AA:BB")
        transport._client = SimpleNamespace(is_connected=True)
        await transport.async_connect()
        transport._client = None
        with patch.object(components, "bluetooth", bluetooth, create=True), patch.object(
            bluetooth, "async_ble_device_from_address", return_value=None
        ):
            with pytest.raises(TransportError, match="not reachable"):
                await transport.async_connect()
        with patch.object(components, "bluetooth", bluetooth, create=True), patch.object(
            bluetooth, "async_ble_device_from_address", side_effect=RuntimeError("no manager")
        ):
            with pytest.raises(TransportError, match="cannot be resolved"):
                await transport.async_connect()

    asyncio.run(exercise())
