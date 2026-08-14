"""KC761 DataUpdateCoordinator and in-memory snapshot."""

from __future__ import annotations

import asyncio
import logging
import random
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
    FLAG_STATUS_UPLOAD,
    MAX_INFO_INTERVAL,
    MAX_RECONNECT_BACKOFF,
    MAX_STATUS_INTERVAL,
    MIN_INFO_INTERVAL,
    MIN_RECONNECT_BACKOFF,
    MIN_STATUS_INTERVAL,
    RECONNECT_JITTER_MAX,
    RECONNECT_JITTER_MIN,
)
from .protocol.client import KC761Client
from .protocol.codec import DecodedFrame, HARDWARE_VERIFIED
from .protocol.models import DeviceInfo, StatusData

_LOGGER = logging.getLogger(__name__)


class DeviceIdentityError(ValueError):
    """A reachable endpoint returned a different physical DEVICE_ID."""


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
        self.expected_device_id = str(entry.data.get("device_id", entry.unique_id or ""))
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
        self.identity_mismatches = 0
        self.retry_delay: float | None = None
        self._backoff_base = 0.0
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
            new_connection = not self.client.connected
            if new_connection:
                await self.client.async_connect()
                if self._device_info is not None:
                    self.reconnects += 1
            now = datetime.now(UTC)
            if (
                new_connection
                or self._device_info is None
                or self._last_info_at is None
                or (now - self._last_info_at).total_seconds() >= self.info_interval
            ):
                device_info = await self.client.async_get_device_info()
                self._validate_device_info(device_info)
                self._device_info = device_info
                self._last_info_at = now
            status = await self.client.async_get_status()
            if self._device_info is None:
                raise UpdateFailed("KC761 returned no device information")
            self.protocol_errors = self.client.protocol_errors
            self.last_success = datetime.now(UTC)
            self.consecutive_failures = 0
            self.last_error = None
            self._reset_backoff()
            self._snapshot = KC761Snapshot(
                device_info=self._device_info,
                status=status,
                received_at=self.last_success,
            )
            return self._snapshot
        except Exception as err:
            if isinstance(err, DeviceIdentityError):
                await self.client.async_close()
            self.consecutive_failures += 1
            self.last_error = type(err).__name__
            self.protocol_errors = self.client.protocol_errors
            self._increase_backoff()
            raise UpdateFailed(f"KC761 update failed: {type(err).__name__}") from err

    async def _async_push(self, decoded: DecodedFrame) -> None:
        """Accept status pushes without changing device settings."""
        if (
            decoded.frame.flag != FLAG_STATUS_UPLOAD
            or not isinstance(decoded.data, StatusData)
            or self._device_info is None
        ):
            return
        now = datetime.now(UTC)
        self._snapshot = KC761Snapshot(
            device_info=self._device_info,
            status=decoded.data,
            received_at=now,
        )
        self.last_success = now
        self.consecutive_failures = 0
        self.last_error = None
        self._reset_backoff()
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
                if info.device_id != self.expected_device_id:
                    raise ValueError("DEVICE_ID does not match this config entry")
            finally:
                await candidate.async_close()
        await self.client.async_close()
        self.host, self.port = host, port
        self.client = KC761Client(host, port, on_push=self._async_push)
        self._last_info_at = None

    def _validate_device_info(self, info: DeviceInfo) -> None:
        if not self.expected_device_id or info.device_id != self.expected_device_id:
            self.identity_mismatches += 1
            raise DeviceIdentityError("KC761 DEVICE_ID changed")

    def _increase_backoff(self) -> None:
        self._backoff_base = (
            MIN_RECONNECT_BACKOFF
            if self._backoff_base == 0
            else min(MAX_RECONNECT_BACKOFF, self._backoff_base * 2)
        )
        delay = self._backoff_base * random.uniform(
            RECONNECT_JITTER_MIN,
            RECONNECT_JITTER_MAX,
        )
        self.retry_delay = max(
            MIN_RECONNECT_BACKOFF,
            min(MAX_RECONNECT_BACKOFF, delay),
        )
        self.update_interval = timedelta(seconds=self.retry_delay)

    def _reset_backoff(self) -> None:
        self._backoff_base = 0.0
        self.retry_delay = None
        self.update_interval = timedelta(seconds=self.status_interval)


def _bounded(value: Any, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = minimum
    return max(minimum, min(maximum, number))
