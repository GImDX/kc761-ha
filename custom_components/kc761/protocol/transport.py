"""Async transports for KC761 TCP and Bluetooth LE connections."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Protocol

from ..const import (
    BLE_DEFAULT_WRITE_CHUNK_SIZE,
    BLE_CONNECT_TIMEOUT,
    BLE_CONNECT_OVERHEAD,
    BLE_MAX_CONNECT_ATTEMPTS,
    BLE_MIN_SUPPORTED_MTU,
    BLE_NOTIFICATION_QUEUE_SIZE,
    BLE_RX_CHARACTERISTIC_UUID,
    BLE_TX_CHARACTERISTIC_UUID,
    BLE_VERIFIED_SERVICE_UUID,
    CONNECT_TIMEOUT,
    IO_TIMEOUT,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class TransportError(ConnectionError):
    """Transport failed or was closed."""


class Transport(Protocol):
    """Minimal byte-stream transport consumed by :class:`KC761Client`."""

    @property
    def connected(self) -> bool:
        """Return whether the transport has an active connection."""

    async def async_connect(self) -> None:
        """Connect and prepare the byte stream."""

    async def async_read(self, size: int = 4096) -> bytes:
        """Read at most ``size`` bytes from the byte stream."""

    async def async_write(self, data: bytes) -> None:
        """Write bytes to the byte stream."""

    async def async_close(self) -> None:
        """Close the connection and release all resources."""


class TcpTransport:
    """Small asyncio transport with bounded connect/read/write operations."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        connect_timeout: float = CONNECT_TIMEOUT,
        write_timeout: float = IO_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.write_timeout = write_timeout
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None

    @property
    def connected(self) -> bool:
        return self.writer is not None and not self.writer.is_closing()

    async def async_connect(self) -> None:
        if self.connected:
            return
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.connect_timeout
            )
        except (OSError, TimeoutError) as err:
            self.reader = None
            self.writer = None
            raise TransportError(f"cannot connect to KC761 at {self.host}:{self.port}") from err

    async def async_read(self, size: int = 4096) -> bytes:
        if self.reader is None:
            raise TransportError("transport is not connected")
        try:
            return await self.reader.read(size)
        except OSError as err:
            raise TransportError("KC761 read failed") from err

    async def async_write(self, data: bytes) -> None:
        if self.writer is None or self.writer.is_closing():
            raise TransportError("transport is not connected")
        try:
            self.writer.write(data)
            await asyncio.wait_for(self.writer.drain(), self.write_timeout)
        except (OSError, TimeoutError) as err:
            await self.async_close()
            raise TransportError("KC761 write failed") from err

    async def async_close(self) -> None:
        writer, self.writer = self.writer, None
        self.reader = None
        if writer is None:
            return
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, asyncio.CancelledError):
            pass


class BleTransport:
    """Bounded GATT byte-stream transport using Home Assistant's BLE manager.

    KC761 documents the RX and TX characteristic UUIDs. The authorized device
    test also confirmed one service UUID, which is used as the default
    post-connection constraint. A compatible firmware may explicitly supply
    another verified service UUID. In either case the two known characteristics
    must resolve to exactly one shared service.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        *,
        service_uuid: str | None = None,
        connect_timeout: float = BLE_CONNECT_TIMEOUT,
        write_timeout: float = IO_TIMEOUT,
        queue_size: int = BLE_NOTIFICATION_QUEUE_SIZE,
    ) -> None:
        address = address.strip()
        if not address or any(ord(char) < 0x20 for char in address):
            raise ValueError("BLE address must be a non-empty printable string")
        if queue_size < 1:
            raise ValueError("BLE notification queue size must be positive")
        self.hass = hass
        self.address = address
        self.service_uuid = _normalize_uuid(service_uuid or BLE_VERIFIED_SERVICE_UUID)
        self.connect_timeout = max(float(connect_timeout), BLE_CONNECT_TIMEOUT)
        self.overall_connect_timeout = (
            self.connect_timeout * BLE_MAX_CONNECT_ATTEMPTS + BLE_CONNECT_OVERHEAD
        )
        self.write_timeout = write_timeout
        self.queue_size = queue_size
        self._client: Any | None = None
        self._rx_characteristic: Any | None = None
        self._tx_characteristic: Any | None = None
        self._notifications: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=queue_size)
        self._read_buffer = bytearray()
        self._notification_overflow = False
        self._connect_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        """Return the underlying Bleak connection state."""
        client = self._client
        return client is not None and bool(getattr(client, "is_connected", False))

    async def async_connect(self) -> None:
        """Resolve the shared HA BLE device, connect, and subscribe to TX."""
        async with self._connect_lock:
            if self.connected:
                return
            await self.async_close()
            self._notifications = asyncio.Queue(maxsize=self.queue_size)
            self._read_buffer.clear()
            self._notification_overflow = False
            try:
                from bleak_retry_connector import (
                    BleakClientWithServiceCache,
                    establish_connection,
                )
                from homeassistant.components import bluetooth
            except ImportError as err:  # pragma: no cover - only offline protocol runs
                raise TransportError("Home Assistant Bluetooth support is unavailable") from err

            try:
                ble_device = bluetooth.async_ble_device_from_address(
                    self.hass,
                    self.address,
                    connectable=True,
                )
            except Exception as err:
                raise TransportError(f"KC761 BLE device {self.address} cannot be resolved") from err
            if ble_device is None:
                raise TransportError(f"KC761 BLE device {self.address} is not reachable")
            client: Any | None = None
            try:
                client = await asyncio.wait_for(
                    establish_connection(
                        BleakClientWithServiceCache,
                        ble_device,
                        self.address,
                        disconnected_callback=self._on_disconnect,
                        max_attempts=BLE_MAX_CONNECT_ATTEMPTS,
                        timeout=self.connect_timeout,
                    ),
                    self.overall_connect_timeout,
                )
                _validate_reported_mtu(client)
                rx_characteristic, tx_characteristic = _find_characteristics(
                    client.services,
                    service_uuid=self.service_uuid,
                )
                _validate_characteristic_properties(rx_characteristic, tx_characteristic)
                self._client = client
                self._rx_characteristic = rx_characteristic
                self._tx_characteristic = tx_characteristic
                await asyncio.wait_for(
                    client.start_notify(tx_characteristic, self._on_notification),
                    self.connect_timeout,
                )
            except TransportError:
                await _disconnect_ble_client(client, timeout=self.write_timeout)
                raise
            except Exception as err:
                await _disconnect_ble_client(client, timeout=self.write_timeout)
                raise TransportError(f"KC761 BLE connection to {self.address} failed") from err

    async def async_read(self, size: int = 4096) -> bytes:
        """Read notification bytes while retaining any excess in a local buffer."""
        if not self.connected:
            raise TransportError("transport is not connected")
        if size < 1:
            raise ValueError("read size must be positive")
        if self._notification_overflow:
            raise TransportError("KC761 BLE notification queue overflowed")
        if not self._read_buffer:
            chunk = await self._notifications.get()
            if chunk is None:
                raise TransportError("KC761 BLE connection was lost")
            self._read_buffer.extend(chunk)
        data = bytes(self._read_buffer[:size])
        del self._read_buffer[:size]
        return data

    async def async_write(self, data: bytes) -> None:
        """Write bytes to RX using bounded GATT chunks."""
        client = self._client
        characteristic = self._rx_characteristic
        if client is None or characteristic is None or not self.connected:
            raise TransportError("transport is not connected")
        if not data:
            return
        properties = {str(value).lower().replace("_", "-") for value in getattr(characteristic, "properties", ())}
        if not properties.intersection({"write", "write-without-response", "writewithoutresponse"}):
            raise TransportError("KC761 BLE RX characteristic is not writable")
        response = "write" in properties
        chunk_size = _ble_write_chunk_size(client, characteristic, response=response)
        try:
            for offset in range(0, len(data), chunk_size):
                await asyncio.wait_for(
                    client.write_gatt_char(
                        characteristic,
                        data[offset : offset + chunk_size],
                        response=response,
                    ),
                    self.write_timeout,
                )
        except Exception as err:
            await self.async_close()
            raise TransportError("KC761 BLE write failed") from err

    async def async_close(self) -> None:
        """Stop notifications and disconnect idempotently."""
        client, self._client = self._client, None
        tx_characteristic, self._tx_characteristic = self._tx_characteristic, None
        self._rx_characteristic = None
        self._read_buffer.clear()
        self._notification_overflow = False
        if client is None:
            return
        if tx_characteristic is not None:
            try:
                await asyncio.wait_for(client.stop_notify(tx_characteristic), self.write_timeout)
            except Exception:
                pass
        await _disconnect_ble_client(client, timeout=self.write_timeout)

    def _on_notification(self, _sender: Any, data: bytearray | bytes) -> None:
        """Queue one notification without allowing callback-side unbounded growth."""
        if self._notification_overflow:
            return
        try:
            self._notifications.put_nowait(bytes(data))
        except asyncio.QueueFull:
            self._notification_overflow = True

    def _on_disconnect(self, _client: Any) -> None:
        """Wake the reader task when the remote device disconnects."""
        try:
            self._notifications.put_nowait(None)
        except asyncio.QueueFull:
            self._notification_overflow = True


def _normalize_uuid(value: str) -> str:
    """Normalize a UUID without assigning semantic meaning to it."""
    import uuid

    return str(uuid.UUID(value.strip())).lower()


def _find_characteristics(
    services: Any,
    *,
    service_uuid: str | None,
) -> tuple[Any, Any]:
    """Find one known RX/TX characteristic pair in a Bleak service collection."""
    rx_matches: list[tuple[str, Any]] = []
    tx_matches: list[tuple[str, Any]] = []
    for service in _iter_services(services):
        current_service_uuid = _normalize_uuid(str(service.uuid))
        if service_uuid is not None and current_service_uuid != service_uuid:
            continue
        for characteristic in getattr(service, "characteristics", ()):
            characteristic_uuid = _normalize_uuid(str(characteristic.uuid))
            if characteristic_uuid == BLE_RX_CHARACTERISTIC_UUID.lower():
                rx_matches.append((current_service_uuid, characteristic))
            elif characteristic_uuid == BLE_TX_CHARACTERISTIC_UUID.lower():
                tx_matches.append((current_service_uuid, characteristic))
    if len(rx_matches) != 1 or len(tx_matches) != 1 or rx_matches[0][0] != tx_matches[0][0]:
        raise TransportError(
            "KC761 BLE RX/TX characteristics are missing, ambiguous, or not in "
            "the selected service UUID"
        )
    return rx_matches[0][1], tx_matches[0][1]


def _validate_characteristic_properties(rx_characteristic: Any, tx_characteristic: Any) -> None:
    """Require the documented write/notify operations before subscribing."""
    rx_properties = _characteristic_properties(rx_characteristic)
    tx_properties = _characteristic_properties(tx_characteristic)
    if not rx_properties.intersection({"write", "write-without-response", "writewithoutresponse"}):
        raise TransportError("KC761 BLE RX characteristic is not writable")
    if "notify" not in tx_properties:
        raise TransportError("KC761 BLE TX characteristic does not support notifications")


def _characteristic_properties(characteristic: Any) -> set[str]:
    """Normalize Bleak characteristic properties for backend variations."""
    return {
        str(value).lower().replace("_", "-")
        for value in getattr(characteristic, "properties", ())
    }


def _validate_reported_mtu(client: Any) -> None:
    """Apply the documented MTU gate without rejecting BlueZ's 23 placeholder.

    Bleak backends may expose ``23`` before/without exposing the negotiated
    value.  Treat that value as unknown and let the first request determine
    whether the device responds.  Other positive values below 185 are treated
    as a reliable lower-than-supported negotiation and rejected with an
    actionable error.
    """
    mtu = getattr(client, "mtu_size", None)
    if not isinstance(mtu, int) or mtu <= 23 or mtu >= BLE_MIN_SUPPORTED_MTU:
        return
    raise TransportError(
        f"KC761 BLE negotiated MTU {mtu}, below the required {BLE_MIN_SUPPORTED_MTU}; "
        "use a Bluetooth adapter/backend that supports MTU >= 185"
    )


def _iter_services(services: Any) -> Iterable[Any]:
    """Iterate Bleak service collections across supported Bleak backends."""
    if services is None:
        return ()
    try:
        return tuple(services)
    except TypeError:
        service_map = getattr(services, "services", {})
        return tuple(service_map.values())


def _ble_write_chunk_size(client: Any, characteristic: Any, *, response: bool) -> int:
    """Return a conservative bounded GATT write size."""
    if not response:
        candidate = getattr(characteristic, "max_write_without_response_size", None)
        if isinstance(candidate, int) and candidate > 0:
            return candidate
    mtu = getattr(client, "mtu_size", None)
    if isinstance(mtu, int) and mtu > 3:
        return mtu - 3
    return BLE_DEFAULT_WRITE_CHUNK_SIZE


async def _disconnect_ble_client(client: Any, *, timeout: float = IO_TIMEOUT) -> None:
    """Disconnect a Bleak client without masking the original connection error."""
    try:
        if bool(getattr(client, "is_connected", False)):
            await asyncio.wait_for(client.disconnect(), timeout)
    except Exception:
        pass
