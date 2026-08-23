"""Pure transport tests for the explicit KC761 Bluetooth LE path."""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from custom_components.kc761.const import (
    BLE_CONNECT_OVERHEAD,
    BLE_CONNECT_TIMEOUT,
    BLE_MAX_CONNECT_ATTEMPTS,
    BLE_RX_CHARACTERISTIC_UUID,
    BLE_TX_CHARACTERISTIC_UUID,
    BLE_VERIFIED_SERVICE_UUID,
    CMD_GET_DEVICE_INFO,
    CMD_GET_STA,
)
from custom_components.kc761.protocol import transport as transport_module
from custom_components.kc761.protocol.transport import (
    BleTransport,
    TransportError,
    _ble_write_chunk_size,
    _find_characteristics,
    _validate_characteristic_properties,
    _validate_reported_mtu,
)
from custom_components.kc761.protocol.codec import encode_read_request


def _characteristic(uuid: str, *properties: str) -> SimpleNamespace:
    return SimpleNamespace(uuid=uuid, properties=properties)


def _services(*, duplicate: bool = False) -> tuple[SimpleNamespace, ...]:
    rx = _characteristic(BLE_RX_CHARACTERISTIC_UUID, "write")
    tx = _characteristic(BLE_TX_CHARACTERISTIC_UUID, "notify")
    service = SimpleNamespace(uuid=BLE_VERIFIED_SERVICE_UUID, characteristics=(rx, tx))
    if not duplicate:
        return (service,)
    duplicate_service = SimpleNamespace(
        uuid="87654321-4321-8765-4321-876587654321",
        characteristics=(
            _characteristic(BLE_RX_CHARACTERISTIC_UUID, "write"),
            _characteristic(BLE_TX_CHARACTERISTIC_UUID, "notify"),
        ),
    )
    return service, duplicate_service


def test_characteristics_require_one_shared_service() -> None:
    services = _services()
    rx, tx = _find_characteristics(services, service_uuid=BLE_VERIFIED_SERVICE_UUID)
    assert rx.uuid == BLE_RX_CHARACTERISTIC_UUID
    assert tx.uuid == BLE_TX_CHARACTERISTIC_UUID


def test_characteristics_require_documented_write_and_notify_properties() -> None:
    with pytest.raises(TransportError, match="not writable"):
        _validate_characteristic_properties(
            _characteristic(BLE_RX_CHARACTERISTIC_UUID),
            _characteristic(BLE_TX_CHARACTERISTIC_UUID, "notify"),
        )
    with pytest.raises(TransportError, match="notifications"):
        _validate_characteristic_properties(
            _characteristic(BLE_RX_CHARACTERISTIC_UUID, "write"),
            _characteristic(BLE_TX_CHARACTERISTIC_UUID),
        )

    with pytest.raises(TransportError, match="missing, ambiguous"):
        _find_characteristics(_services(duplicate=True), service_uuid=None)

    rx, tx = _find_characteristics(
        _services(duplicate=True),
        service_uuid="87654321-4321-8765-4321-876587654321",
    )
    assert rx.uuid == BLE_RX_CHARACTERISTIC_UUID
    assert tx.uuid == BLE_TX_CHARACTERISTIC_UUID


def test_mtu_gate_does_not_reject_bluez_placeholder() -> None:
    _validate_reported_mtu(SimpleNamespace(mtu_size=23))
    _validate_reported_mtu(SimpleNamespace(mtu_size=185))
    _validate_reported_mtu(SimpleNamespace(mtu_size=None))
    with pytest.raises(TransportError, match="MTU 184"):
        _validate_reported_mtu(SimpleNamespace(mtu_size=184))


def test_ble_write_size_prefers_backend_characteristic_limit() -> None:
    characteristic = SimpleNamespace(max_write_without_response_size=41)
    assert _ble_write_chunk_size(SimpleNamespace(mtu_size=507), characteristic, response=False) == 41
    assert _ble_write_chunk_size(SimpleNamespace(mtu_size=185), characteristic, response=True) == 182
    assert _ble_write_chunk_size(SimpleNamespace(mtu_size=None), SimpleNamespace(), response=True) == 20


def test_notification_queue_is_bounded_and_read_preserves_excess() -> None:
    async def exercise() -> None:
        transport = BleTransport(SimpleNamespace(), "AA:BB:CC:DD:EE:FF", queue_size=1)
        transport._client = SimpleNamespace(is_connected=True)
        transport._on_notification(None, b"abcdef")
        assert await transport.async_read(2) == b"ab"
        assert await transport.async_read(8) == b"cdef"
        transport._on_notification(None, b"one")
        transport._on_notification(None, b"two")
        with pytest.raises(TransportError, match="overflowed"):
            await transport.async_read()

    asyncio.run(exercise())


def test_ble_address_and_service_validation_is_explicit() -> None:
    with pytest.raises(ValueError, match="address"):
        BleTransport(SimpleNamespace(), "   ")
    transport = BleTransport(
        SimpleNamespace(),
        "AA:BB:CC:DD:EE:FF",
        service_uuid="12345678-1234-5678-1234-567812345678",
    )
    assert transport.service_uuid == "12345678-1234-5678-1234-567812345678"


def test_ble_service_defaults_to_verified_device_constraint() -> None:
    transport = BleTransport(SimpleNamespace(), "AA:BB:CC:DD:EE:FF")
    assert transport.service_uuid == BLE_VERIFIED_SERVICE_UUID


def test_authorized_probe_command_audit_is_exact_read_sequence() -> None:
    commands = (CMD_GET_DEVICE_INFO, CMD_GET_STA)
    assert commands == (0x54, 0x53)
    assert [encode_read_request(sync, command) for sync, command in ((1, commands[0]), (2, commands[1]))] == [
        bytes((0x00, 0x54, 0x01, 0x00)),
        bytes((0x00, 0x53, 0x02, 0x00)),
    ]


def test_ble_connect_uses_ha_resolver_and_gatt_notifications() -> None:
    async def exercise() -> None:
        resolver_calls: list[tuple[object, str, bool]] = []
        writes: list[tuple[object, bytes, bool]] = []
        connector_calls: list[tuple[str, int, float]] = []
        wait_for_timeouts: list[float] = []

        class FakeBleakClient:
            def __init__(self, device, *, disconnected_callback, timeout, **kwargs):
                self.device = device
                self.disconnected_callback = disconnected_callback
                self.timeout = timeout
                self.connector_kwargs = kwargs
                self.is_connected = False
                self.mtu_size = 23
                self.services = _services()
                self.notify_callback = None
                self.stopped = False

            async def connect(self):
                self.is_connected = True

            async def start_notify(self, characteristic, callback):
                assert characteristic.uuid == BLE_TX_CHARACTERISTIC_UUID
                self.notify_callback = callback

            async def write_gatt_char(self, characteristic, data, *, response):
                writes.append((characteristic, bytes(data), response))

            async def stop_notify(self, characteristic):
                self.stopped = True

            async def disconnect(self):
                self.is_connected = False

        async def establish_connection(
            client_class,
            device,
            name,
            *,
            disconnected_callback,
            max_attempts,
            timeout,
            **kwargs,
        ):
            connector_calls.append((name, max_attempts, timeout))
            client = client_class(
                device,
                disconnected_callback=disconnected_callback,
                timeout=timeout,
                **kwargs,
            )
            await client.connect()
            return client

        def resolve(hass, address, *, connectable):
            resolver_calls.append((hass, address, connectable))
            return SimpleNamespace(address=address)

        retry_connector_module = ModuleType("bleak_retry_connector")
        retry_connector_module.BleakClientWithServiceCache = FakeBleakClient
        retry_connector_module.establish_connection = establish_connection
        homeassistant_module = ModuleType("homeassistant")
        components_module = ModuleType("homeassistant.components")
        bluetooth_module = SimpleNamespace(async_ble_device_from_address=resolve)
        components_module.bluetooth = bluetooth_module
        homeassistant_module.components = components_module
        transport = BleTransport(SimpleNamespace(name="hass"), "AA:BB:CC:DD:EE:FF")
        real_wait_for = transport_module.asyncio.wait_for

        async def recording_wait_for(awaitable, timeout):
            wait_for_timeouts.append(timeout)
            return await real_wait_for(awaitable, timeout)

        with patch.dict(
            sys.modules,
            {
                "bleak_retry_connector": retry_connector_module,
                "homeassistant": homeassistant_module,
                "homeassistant.components": components_module,
            },
        ):
            with patch.object(transport_module.asyncio, "wait_for", recording_wait_for):
                await transport.async_connect()
            assert transport.connected
            assert transport.connect_timeout == BLE_CONNECT_TIMEOUT
            assert transport.overall_connect_timeout == (
                transport.connect_timeout * BLE_MAX_CONNECT_ATTEMPTS + BLE_CONNECT_OVERHEAD
            )
            assert wait_for_timeouts[0] == transport.overall_connect_timeout
            assert connector_calls == [(transport.address, BLE_MAX_CONNECT_ATTEMPTS, transport.connect_timeout)]
            assert resolver_calls == [(transport.hass, transport.address, True)]
            transport._on_notification(None, b"frame")
            assert await transport.async_read() == b"frame"
            await transport.async_write(b"1234567890123456789012345")
            await transport.async_close()

        assert [item[1] for item in writes] == [b"12345678901234567890", b"12345"]
        assert all(item[2] for item in writes)
        assert not transport.connected

    asyncio.run(exercise())


def test_ble_connector_transport_error_is_preserved_without_unbound_client() -> None:
    async def exercise() -> None:
        async def establish_connection(*args, **kwargs):
            del args, kwargs
            raise TransportError("connector failed")

        retry_connector_module = ModuleType("bleak_retry_connector")
        retry_connector_module.BleakClientWithServiceCache = object
        retry_connector_module.establish_connection = establish_connection
        homeassistant_module = ModuleType("homeassistant")
        components_module = ModuleType("homeassistant.components")
        components_module.bluetooth = SimpleNamespace(
            async_ble_device_from_address=lambda *args, **kwargs: SimpleNamespace(address="AA:BB")
        )
        homeassistant_module.components = components_module
        transport = BleTransport(SimpleNamespace(name="hass"), "AA:BB:CC:DD:EE:FF")

        with patch.dict(
            sys.modules,
            {
                "bleak_retry_connector": retry_connector_module,
                "homeassistant": homeassistant_module,
                "homeassistant.components": components_module,
            },
        ):
            with pytest.raises(TransportError, match="connector failed") as error:
                await transport.async_connect()

        assert not isinstance(error.value, UnboundLocalError)
        assert transport._client is None

    asyncio.run(exercise())


def test_ble_disconnect_cleanup_is_bounded() -> None:
    async def exercise() -> None:
        class HangingClient:
            is_connected = True

            async def stop_notify(self, characteristic):
                del characteristic

            async def disconnect(self):
                await asyncio.sleep(60)

        transport = BleTransport(
            SimpleNamespace(name="hass"),
            "AA:BB:CC:DD:EE:FF",
            write_timeout=0.01,
        )
        transport._client = HangingClient()
        transport._tx_characteristic = SimpleNamespace(uuid=BLE_TX_CHARACTERISTIC_UUID)

        await asyncio.wait_for(transport.async_close(), timeout=0.5)
        assert transport._client is None

    asyncio.run(exercise())
