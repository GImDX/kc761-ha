"""KC761 DataUpdateCoordinator and in-memory snapshot."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DEFAULT_INFO_INTERVAL,
    DEFAULT_STATUS_INTERVAL,
    DOMAIN,
    MAX_INFO_INTERVAL,
    MAX_STATUS_INTERVAL,
    MIN_INFO_INTERVAL,
    MIN_STATUS_INTERVAL,
)
from .protocol.client import KC761Client
from .protocol.codec import DecodedFrame, HARDWARE_VERIFIED
from .protocol.models import DeviceInfo, StatusData

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class KC761Snapshot:
    """Immutable values consumed by HA entities."""

    device_info: DeviceInfo
    status: StatusData
    received_at: datetime
    hardware_verified: bool = HARDWARE_VERIFIED


class KC761Coordinator(DataUpdateCoordinator[KC761Snapshot]):
    """Own one connection and all polling/availability state."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.host = str(entry.data["host"])
        self.port = int(entry.data["port"])
        self.status_interval = _bounded(
            entry.options.get("status_interval", DEFAULT_STATUS_INTERVAL),
            MIN_STATUS_INTERVAL,
            MAX_STATUS_INTERVAL,
        )
        self.info_interval = _bounded(
            entry.options.get("info_interval", DEFAULT_INFO_INTERVAL),
            MIN_INFO_INTERVAL,
            MAX_INFO_INTERVAL,
        )
        self.client = KC761Client(self.host, self.port, on_push=self._async_push)
        self._device_info: DeviceInfo | None = None
        self._last_info_at: datetime | None = None
        self._snapshot: KC761Snapshot | None = None
        self._write_lock = asyncio.Lock()
        self.reconnects = 0
        self.protocol_errors = 0
        self.consecutive_failures = 0
        self.last_success: datetime | None = None
        self.last_error: str | None = None
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_method=self._async_update_data,
            update_interval=timedelta(seconds=self.status_interval),
        )

    @property
    def snapshot(self) -> KC761Snapshot | None:
        return self._snapshot

    @property
    def device_info(self) -> DeviceInfo | None:
        return self._device_info

    @property
    def hardware_verified(self) -> bool:
        return bool(self._device_info and self._device_info.hardware_verified)

    async def _async_update_data(self) -> KC761Snapshot:
        try:
            was_connected = self.client.connected
            if not was_connected:
                await self.client.async_connect()
                if self._device_info is not None:
                    self.reconnects += 1
            now = datetime.now(UTC)
            if (
                self._device_info is None
                or self._last_info_at is None
                or (now - self._last_info_at).total_seconds() >= self.info_interval
            ):
                self._device_info = await self.client.async_get_device_info()
                self._last_info_at = now
            status = await self.client.async_get_status()
            if self._device_info is None:
                raise UpdateFailed("KC761 returned no device information")
            self.protocol_errors = self.client.protocol_errors
            self.last_success = datetime.now(UTC)
            self.consecutive_failures = 0
            self.last_error = None
            self._snapshot = KC761Snapshot(
                device_info=self._device_info,
                status=status,
                received_at=self.last_success,
            )
            return self._snapshot
        except Exception as err:
            self.consecutive_failures += 1
            self.last_error = type(err).__name__
            self.protocol_errors = self.client.protocol_errors
            raise UpdateFailed(f"KC761 update failed: {type(err).__name__}") from err

    async def _async_push(self, decoded: DecodedFrame) -> None:
        """Accept status pushes without changing device settings."""
        if not isinstance(decoded.data, StatusData) or self._device_info is None:
            return
        now = datetime.now(UTC)
        self._snapshot = KC761Snapshot(
            device_info=self._device_info,
            status=decoded.data,
            received_at=now,
        )
        self.last_success = now
        self.consecutive_failures = 0
        if self.last_update_success:
            self.async_set_updated_data(self._snapshot)

    async def async_shutdown(self) -> None:
        """Cancel coordinator work and close the owned TCP client."""
        await self.client.async_close()
        self.async_stop_update()

    async def async_reconfigure(self, host: str, port: int) -> None:
        """Validate and replace the endpoint while preserving DEVICE_ID."""
        async with self._write_lock:
            candidate = KC761Client(host, port)
            try:
                await candidate.async_connect()
                info = await candidate.async_get_device_info()
                if self._device_info and info.device_id != self._device_info.device_id:
                    raise ValueError("DEVICE_ID does not match this config entry")
            finally:
                await candidate.async_close()
        await self.client.async_close()
        self.host, self.port = host, port
        self.client = KC761Client(host, port, on_push=self._async_push)
        self._last_info_at = None


def _bounded(value: Any, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = minimum
    return max(minimum, min(maximum, number))
