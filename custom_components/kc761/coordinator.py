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
    CONF_CREATE_RAW_ENTITIES,
    CONF_INFO_INTERVAL,
    CONF_STATUS_INTERVAL,
    DEFAULT_CREATE_RAW_ENTITIES,
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
    MODEL_NAMES,
    RECONNECT_JITTER_MAX,
    RECONNECT_JITTER_MIN,
)
from .protocol.client import KC761Client
from .protocol.codec import CODEC_PROFILE, DecodedFrame, HARDWARE_VERIFIED
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
        self._hass = hass
        self.entry = entry
        self.host = str(entry.data["host"])
        self.port = int(entry.data["port"])
        self.expected_device_id = str(entry.data.get("device_id", entry.unique_id or ""))
        self.status_interval = _bounded(
            entry.options.get(CONF_STATUS_INTERVAL, DEFAULT_STATUS_INTERVAL),
            MIN_STATUS_INTERVAL,
            MAX_STATUS_INTERVAL,
        )
        self.info_interval = _bounded(
            entry.options.get(CONF_INFO_INTERVAL, DEFAULT_INFO_INTERVAL),
            MIN_INFO_INTERVAL,
            MAX_INFO_INTERVAL,
        )
        self.create_raw_entities = bool(entry.options.get(CONF_CREATE_RAW_ENTITIES, DEFAULT_CREATE_RAW_ENTITIES))
        self.client = self._new_client(self.host, self.port)
        self._device_info: DeviceInfo | None = None
        self._last_info_at: datetime | None = None
        self._snapshot: KC761Snapshot | None = None
        self._write_lock = asyncio.Lock()
        self._shutdown_lock = asyncio.Lock()
        self._shutdown = False
        self._profile_logged = False
        self._reload_scheduled = False
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
                if not isinstance(device_info, DeviceInfo):
                    raise UpdateFailed("KC761 returned no device information")
                self._validate_device_info(device_info)
                previous_info = self._device_info
                self._device_info = device_info
                self._last_info_at = now
                if previous_info is None:
                    self._log_protocol_profile(device_info)
                elif _device_signature(previous_info) != _device_signature(device_info):
                    self._schedule_hardware_reload()
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
        async with self._shutdown_lock:
            if self._shutdown:
                return
            self._shutdown = True
            self.async_stop_update()
            await self.client.async_close()

    async def async_reconfigure(self, host: str, port: int) -> None:
        """Validate and replace the endpoint while preserving DEVICE_ID."""
        async with self._write_lock:
            candidate = self._new_client(host, port, on_push=False)
            try:
                await candidate.async_connect()
                info = await candidate.async_get_device_info()
                if info.device_id != self.expected_device_id:
                    raise ValueError("DEVICE_ID does not match this config entry")
            finally:
                await candidate.async_close()
        await self.client.async_close()
        self.host, self.port = host, port
        self.client = self._new_client(host, port)
        self._last_info_at = None

    def _new_client(self, host: str, port: int, *, on_push: bool = True) -> KC761Client:
        return KC761Client(
            host,
            port,
            on_push=self._async_push if on_push else None,
            task_creator=self._create_hass_task,
        )

    def _create_hass_task(self, coroutine: Any, name: str) -> asyncio.Task[None]:
        return self._hass.async_create_task(coroutine, name=name)

    def _validate_device_info(self, info: DeviceInfo) -> None:
        if not self.expected_device_id or info.device_id != self.expected_device_id:
            self.identity_mismatches += 1
            raise DeviceIdentityError("KC761 DEVICE_ID changed")

    def _log_protocol_profile(self, info: DeviceInfo) -> None:
        if self._profile_logged:
            return
        self._profile_logged = True
        _LOGGER.warning(
            "KC761 protocol profile %s loaded with hardware_verified=%s",
            CODEC_PROFILE,
            HARDWARE_VERIFIED,
        )
        if info.device_model not in MODEL_NAMES:
            _LOGGER.warning(
                "KC761 returned structurally compatible unknown model code %s",
                info.device_model,
            )

    def _schedule_hardware_reload(self) -> None:
        if self._reload_scheduled or self._shutdown:
            return
        self._reload_scheduled = True
        self._hass.async_create_task(
            self._async_reload_after_hardware_change(),
            name="kc761-hardware-reload",
        )

    async def _async_reload_after_hardware_change(self) -> None:
        await asyncio.sleep(0)
        await self._hass.config_entries.async_reload(self.entry.entry_id)

    def _increase_backoff(self) -> None:
        self._backoff_base = (
            MIN_RECONNECT_BACKOFF if self._backoff_base == 0 else min(MAX_RECONNECT_BACKOFF, self._backoff_base * 2)
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


def _device_signature(info: DeviceInfo) -> tuple[Any, ...]:
    return (
        info.device_model,
        info.hardware_version,
        info.firmware_version,
        info.coprocessor_firmware_version,
        info.sensor_types,
    )
