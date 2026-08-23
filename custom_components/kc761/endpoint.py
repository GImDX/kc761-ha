"""Transport-neutral KC761 endpoint validation and persistence helpers."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .const import (
    BLE_VERIFIED_SERVICE_UUID,
    CONF_BLE_ADDRESS,
    CONF_BLE_SERVICE_UUID,
    CONF_TRANSPORT,
    DEFAULT_PORT,
    SUPPORTED_TRANSPORTS,
    TRANSPORT_BLE,
    TRANSPORT_TCP,
)

_HOST = "host"
_PORT = "port"
_ENDPOINT_KEYS = frozenset(
    {
        _HOST,
        _PORT,
        CONF_TRANSPORT,
        CONF_BLE_ADDRESS,
        CONF_BLE_SERVICE_UUID,
    }
)


@dataclass(frozen=True, slots=True)
class Endpoint:
    """Validated TCP or explicit BLE endpoint."""

    transport: str
    host: str = ""
    port: int = 0
    ble_address: str = ""
    ble_service_uuid: str | None = None

    def as_data(self) -> dict[str, Any]:
        """Return only the endpoint keys applicable to this transport."""
        if self.transport == TRANSPORT_BLE:
            data: dict[str, Any] = {
                CONF_TRANSPORT: TRANSPORT_BLE,
                CONF_BLE_ADDRESS: self.ble_address,
            }
            data[CONF_BLE_SERVICE_UUID] = self.ble_service_uuid or BLE_VERIFIED_SERVICE_UUID
            return data
        return {
            CONF_TRANSPORT: TRANSPORT_TCP,
            _HOST: self.host,
            _PORT: self.port,
        }


def parse_endpoint(
    user_input: Mapping[str, Any],
    *,
    defaults: Mapping[str, Any] | None = None,
) -> Endpoint:
    """Validate a TCP or explicit BLE endpoint without probing hardware."""
    defaults = defaults or {}
    transport = str(user_input.get(CONF_TRANSPORT, defaults.get(CONF_TRANSPORT, TRANSPORT_TCP))).strip().lower()
    if transport not in SUPPORTED_TRANSPORTS:
        raise ValueError("invalid_transport")
    if transport == TRANSPORT_BLE:
        address = str(user_input.get(CONF_BLE_ADDRESS, defaults.get(CONF_BLE_ADDRESS, ""))).strip()
        if not address or any(ord(char) < 0x20 for char in address):
            raise ValueError("invalid_ble_address")
        raw_service_uuid = str(
            user_input.get(CONF_BLE_SERVICE_UUID, defaults.get(CONF_BLE_SERVICE_UUID, "")) or ""
        ).strip()
        service_uuid = (
            normalize_service_uuid(raw_service_uuid)
            if raw_service_uuid
            else BLE_VERIFIED_SERVICE_UUID
        )
        return Endpoint(
            transport=TRANSPORT_BLE,
            ble_address=address,
            ble_service_uuid=service_uuid,
        )
    host = str(user_input.get(_HOST, defaults.get(_HOST, ""))).strip()
    try:
        port = int(user_input.get(_PORT, defaults.get(_PORT, DEFAULT_PORT)))
    except (TypeError, ValueError) as err:
        raise ValueError("invalid_tcp_endpoint") from err
    if not host or not 1 <= port <= 65535:
        raise ValueError("invalid_tcp_endpoint")
    return Endpoint(transport=TRANSPORT_TCP, host=host, port=port)


def normalize_service_uuid(value: str) -> str:
    """Return a canonical user-supplied UUID without assigning a default."""
    try:
        return str(uuid.UUID(value.strip())).lower()
    except ValueError as err:
        raise ValueError("invalid_ble_service_uuid") from err


def replace_endpoint_data(existing: Mapping[str, Any], endpoint: Endpoint) -> dict[str, Any]:
    """Replace all endpoint keys while preserving identity and unrelated data.

    A reconfigure operation must not leave TCP fields behind after a BLE switch
    (or BLE fields behind after a TCP switch).  This pure helper makes the
    replacement deterministic before ``ConfigEntries.async_update_entry`` is
    called by the flow.
    """
    data = {key: value for key, value in existing.items() if key not in _ENDPOINT_KEYS}
    data.update(endpoint.as_data())
    return data
