"""Pure KC761 endpoint and reconfigure persistence tests."""

from __future__ import annotations

import pytest

from custom_components.kc761.const import BLE_VERIFIED_SERVICE_UUID
from custom_components.kc761.endpoint import (
    Endpoint,
    parse_endpoint,
    replace_endpoint_data,
)


def test_parse_explicit_ble_endpoint_uses_verified_service_by_default() -> None:
    endpoint = parse_endpoint(
        {
            "transport": "ble",
            "ble_address": " AA:BB:CC:DD:EE:FF ",
        }
    )

    assert endpoint == Endpoint(
        transport="ble",
        ble_address="AA:BB:CC:DD:EE:FF",
        ble_service_uuid=BLE_VERIFIED_SERVICE_UUID,
    )
    assert endpoint.as_data() == {
        "transport": "ble",
        "ble_address": "AA:BB:CC:DD:EE:FF",
        "ble_service_uuid": BLE_VERIFIED_SERVICE_UUID,
    }


def test_parse_ble_service_uuid_is_canonical_and_validated() -> None:
    endpoint = parse_endpoint(
        {
            "transport": "ble",
            "ble_address": "AA:BB:CC:DD:EE:FF",
            "ble_service_uuid": "12345678-1234-5678-1234-567812345678",
        }
    )
    assert endpoint.ble_service_uuid == "12345678-1234-5678-1234-567812345678"

    with pytest.raises(ValueError, match="invalid_ble_service_uuid"):
        parse_endpoint({"transport": "ble", "ble_address": "AA:BB", "ble_service_uuid": "nus"})


def test_parse_tcp_endpoint_preserves_legacy_defaults() -> None:
    endpoint = parse_endpoint({"host": "meter.local", "port": "7602"})
    assert endpoint.as_data() == {"transport": "tcp", "host": "meter.local", "port": 7602}


def test_reconfigure_replaces_cross_transport_keys_and_preserves_identity() -> None:
    existing = {
        "device_id": "7601-0000-000001",
        "transport": "tcp",
        "host": "old-meter",
        "port": 7601,
        "ble_address": "AA:BB:CC:DD:EE:FF",
        "ble_service_uuid": "12345678-1234-5678-1234-567812345678",
        "unrelated": "preserved",
    }

    tcp_data = replace_endpoint_data(
        existing,
        Endpoint(transport="tcp", host="new-meter", port=7602),
    )
    assert tcp_data == {
        "device_id": "7601-0000-000001",
        "unrelated": "preserved",
        "transport": "tcp",
        "host": "new-meter",
        "port": 7602,
    }

    ble_data = replace_endpoint_data(
        existing,
        Endpoint(
            transport="ble",
            ble_address="11:22:33:44:55:66",
            ble_service_uuid=BLE_VERIFIED_SERVICE_UUID,
        ),
    )
    assert ble_data == {
        "device_id": "7601-0000-000001",
        "unrelated": "preserved",
        "transport": "ble",
        "ble_address": "11:22:33:44:55:66",
        "ble_service_uuid": BLE_VERIFIED_SERVICE_UUID,
    }
