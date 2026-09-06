"""KC761 DataUpdateCoordinator and in-memory snapshot."""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_CREATE_RAW_ENTITIES,
    CONF_DOSE_DISPLAY_UNIT,
    CONF_GAMMA_SPECTRUM_CHANNELS,
    CONF_NEUTRON_SPECTRUM_CHANNELS,
    CONF_PIN_SPECTRUM_CHANNELS,
    CONF_ENABLE_DESTRUCTIVE_ACTIONS,
    CONF_ENABLE_DEVICE_CONTROLS,
    CONF_BLE_ADDRESS,
    CONF_BLE_SERVICE_UUID,
    CONF_INFO_INTERVAL,
    CONF_SPECTRUM_SAVE_ENABLED,
    CONF_SPECTRUM_SAVE_INTERVAL,
    CONF_STATUS_INTERVAL,
    CONF_TIME_SYNC_ENABLED,
    CONF_TIME_SYNC_INTERVAL,
    DEFAULT_CREATE_RAW_ENTITIES,
    DEFAULT_DOSE_DISPLAY_UNIT,
    DEFAULT_ENABLE_DESTRUCTIVE_ACTIONS,
    DEFAULT_ENABLE_DEVICE_CONTROLS,
    DEFAULT_INFO_INTERVAL,
    DEFAULT_SPECTRUM_SAVE_ENABLED,
    DEFAULT_SPECTRUM_SAVE_INTERVAL,
    DEFAULT_SPECTRUM_CHANNEL_COUNTS,
    DEFAULT_STATUS_INTERVAL,
    DEFAULT_TIME_SYNC_ENABLED,
    DEFAULT_TIME_SYNC_INTERVAL,
    DOMAIN,
    DOSE_DISPLAY_UNIT_MICRO,
    DOSE_DISPLAY_UNIT_RAW,
    FLAG_MC_DATA_UPLOAD,
    FLAG_STATUS_UPLOAD,
    MAX_INFO_INTERVAL,
    MAX_RECONNECT_BACKOFF,
    MAX_STATUS_INTERVAL,
    MAX_SPECTRUM_SAVE_INTERVAL,
    MAX_TIME_SYNC_INTERVAL,
    MIN_INFO_INTERVAL,
    MIN_RECONNECT_BACKOFF,
    MIN_STATUS_INTERVAL,
    MIN_SPECTRUM_SAVE_INTERVAL,
    MIN_TIME_SYNC_INTERVAL,
    MODEL_NAMES,
    RECONNECT_JITTER_MAX,
    RECONNECT_JITTER_MIN,
    SPECTRUM_CHANNEL_COUNT_OPTIONS,
    CONF_TRANSPORT,
    TRANSPORT_BLE,
    TRANSPORT_TCP,
)
from .protocol.client import KC761Client, SpectrumUploadAssembler
from .protocol.codec import CODEC_PROFILE, DecodedFrame, HARDWARE_VERIFIED
from .protocol.models import CalibrationData, DeviceInfo, SpectrumSegment, StatusData
from .protocol.transport import BleTransport
from .spectrum import SpectrumArchive, SpectrumSnapshot

_LOGGER = logging.getLogger(__name__)


class DeviceIdentityError(ValueError):
    """A reachable endpoint returned a different physical DEVICE_ID."""


class ReconfigureRestoreError(ConnectionError):
    """The requested endpoint failed and the previous endpoint could not be restored."""

    def __init__(self, candidate_error: Exception, restore_error: Exception) -> None:
        self.candidate_error = candidate_error
        self.restore_error = restore_error
        super().__init__("KC761 endpoint reconfiguration rollback failed")


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
        self.transport_type = str(entry.data.get(CONF_TRANSPORT, TRANSPORT_TCP))
        if self.transport_type not in (TRANSPORT_TCP, TRANSPORT_BLE):
            self.transport_type = TRANSPORT_TCP
        self.host = str(entry.data.get("host", ""))
        self.port = int(entry.data.get("port", 0) or 0)
        self.ble_address = str(entry.data.get(CONF_BLE_ADDRESS, ""))
        self.ble_service_uuid = str(entry.data.get(CONF_BLE_SERVICE_UUID, "")) or None
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
        dose_display_unit = entry.options.get(CONF_DOSE_DISPLAY_UNIT, DEFAULT_DOSE_DISPLAY_UNIT)
        self.dose_display_unit = (
            dose_display_unit
            if dose_display_unit in (DOSE_DISPLAY_UNIT_RAW, DOSE_DISPLAY_UNIT_MICRO)
            else DEFAULT_DOSE_DISPLAY_UNIT
        )
        self.device_controls_enabled = bool(
            entry.options.get(
                CONF_ENABLE_DEVICE_CONTROLS,
                DEFAULT_ENABLE_DEVICE_CONTROLS,
            )
        )
        self.destructive_actions_enabled = self.device_controls_enabled and bool(
            entry.options.get(
                CONF_ENABLE_DESTRUCTIVE_ACTIONS,
                DEFAULT_ENABLE_DESTRUCTIVE_ACTIONS,
            )
        )
        self.time_sync_enabled = self.device_controls_enabled and bool(
            entry.options.get(CONF_TIME_SYNC_ENABLED, DEFAULT_TIME_SYNC_ENABLED)
        )
        self.time_sync_interval = _bounded(
            entry.options.get(CONF_TIME_SYNC_INTERVAL, DEFAULT_TIME_SYNC_INTERVAL),
            MIN_TIME_SYNC_INTERVAL,
            MAX_TIME_SYNC_INTERVAL,
        )
        self.spectrum_save_enabled = bool(
            entry.options.get(CONF_SPECTRUM_SAVE_ENABLED, DEFAULT_SPECTRUM_SAVE_ENABLED)
        )
        self.spectrum_save_interval = _bounded(
            entry.options.get(CONF_SPECTRUM_SAVE_INTERVAL, DEFAULT_SPECTRUM_SAVE_INTERVAL),
            MIN_SPECTRUM_SAVE_INTERVAL,
            MAX_SPECTRUM_SAVE_INTERVAL,
        )
        self.spectrum_channel_counts = _spectrum_channel_counts(entry.options)
        self.client = self._new_client(self.host, self.port)
        self._device_info: DeviceInfo | None = None
        self._last_info_at: datetime | None = None
        self._snapshot: KC761Snapshot | None = None
        self._calibration: CalibrationData | None = None
        self._auto_upload_spectrum = SpectrumUploadAssembler(
            channel_counts=self.spectrum_channel_counts
        )
        self._latest_spectrum: SpectrumSnapshot | None = None
        self._archive: SpectrumArchive | None = None
        self._background_tasks: set[asyncio.Task[None]] = set()
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
        self.last_time_sync: datetime | None = None
        self.last_spectrum_save: datetime | None = None
        self.control_commands = 0
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

    @property
    def latest_spectrum(self) -> SpectrumSnapshot | None:
        return self._latest_spectrum

    @property
    def calibration(self) -> CalibrationData | None:
        """Return the latest A6 calibration response for read-only entities."""
        return self._calibration

    async def async_start(self) -> None:
        """Initialize bounded spectrum storage and optional background schedules."""
        archive_path = Path(self._hass.config.path(".storage", DOMAIN, self.entry.entry_id))
        self._archive = SpectrumArchive(archive_path)
        self._start_background(self._async_initial_spectrum(), "kc761-initial-spectrum")
        if self.time_sync_enabled:
            self._start_background(self._async_time_sync_loop(), "kc761-time-sync")
        if self.spectrum_save_enabled:
            self._start_background(self._async_spectrum_save_loop(), "kc761-spectrum-save")

    async def _async_update_data(self) -> KC761Snapshot:
        async with self._write_lock:
            return await self._async_update_data_locked()

    async def _async_update_data_locked(self) -> KC761Snapshot:
        """Poll while the connection ownership lock is held."""
        try:
            new_connection = not self.client.connected
            if new_connection:
                self._auto_upload_spectrum.reset()
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
        """Accept A3 status and bounded A1 spectrum pushes without writes."""
        if decoded.frame.flag == FLAG_STATUS_UPLOAD and isinstance(decoded.data, StatusData):
            self._auto_upload_spectrum.begin_round(decoded.data.active_sensor)
            if self._device_info is None:
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
            return
        if (
            decoded.frame.flag != FLAG_MC_DATA_UPLOAD
            or not isinstance(decoded.data, SpectrumSegment)
            or self._device_info is None
        ):
            return
        uploaded = self._auto_upload_spectrum.add(decoded.data)
        if uploaded is None or self._calibration is None:
            return
        acquired_at = datetime.now(UTC)
        energy = tuple(
            self._calibration.energy_kev(uploaded.source, channel)
            for channel in range(len(uploaded.counts))
        )
        runtime = self._device_info.cumulative[uploaded.source].spectrum_runtime
        self._latest_spectrum = SpectrumSnapshot.create(
            source=uploaded.source,
            acquired_at=acquired_at,
            accumulation_seconds=runtime,
            counts=uploaded.counts,
            energy_kev=energy,
            segment_count=uploaded.segment_count,
            calibration_profile=f"factory:{self._calibration.factory_version}",
        )
        self._notify_entities()

    async def async_shutdown(self) -> None:
        """Cancel coordinator work and close the owned TCP client."""
        async with self._shutdown_lock:
            if self._shutdown:
                return
            self._shutdown = True
            tasks = list(self._background_tasks)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self._background_tasks.clear()
            self._auto_upload_spectrum.reset()
            await super().async_shutdown()
            await self.client.async_close()

    async def async_capture_spectrum(self, source: int | None = None) -> SpectrumSnapshot:
        """Capture one complete configured spectrum and publish the single entity payload."""
        async with self._write_lock:
            if self._snapshot is None or self._device_info is None:
                raise RuntimeError("KC761 has no current status snapshot")
            selected = self._snapshot.status.active_sensor if source is None else int(source)
            self._validate_source(selected)
            if self._calibration is None:
                self._calibration = await self.client.async_get_calibration()
            spectrum = await self.client.async_get_spectrum(selected)
            acquired_at = datetime.now(UTC)
            energy = tuple(
                self._calibration.energy_kev(selected, channel)
                for channel in range(len(spectrum.counts))
            )
            runtime = self._device_info.cumulative[selected].spectrum_runtime
            snapshot = SpectrumSnapshot.create(
                source=selected,
                acquired_at=acquired_at,
                accumulation_seconds=runtime,
                counts=spectrum.counts,
                energy_kev=energy,
                segment_count=spectrum.segment_count,
                calibration_profile=f"factory:{self._calibration.factory_version}",
            )
            self._latest_spectrum = snapshot
            self._notify_entities()
            return snapshot

    async def async_save_spectrum(self, source: int | None = None) -> SpectrumSnapshot:
        """Capture and persist one spectrum using the bounded private archive."""
        snapshot = await self.async_capture_spectrum(source)
        archive = self._require_archive()
        await self._hass.async_add_executor_job(archive.save, snapshot)
        self.last_spectrum_save = datetime.now(UTC)
        return snapshot

    async def async_list_saved_spectra(self) -> list[dict[str, Any]]:
        archive = self._require_archive()
        return await self._hass.async_add_executor_job(archive.list_metadata)

    async def async_get_saved_spectrum(self, snapshot_id: str) -> SpectrumSnapshot:
        archive = self._require_archive()
        return await self._hass.async_add_executor_job(archive.load, snapshot_id)

    async def async_export_spectrum_csv(self, snapshot_id: str | None = None) -> tuple[str, str]:
        snapshot = (
            self._latest_spectrum
            if snapshot_id is None
            else await self.async_get_saved_spectrum(snapshot_id)
        )
        if snapshot is None:
            raise RuntimeError("no spectrum is available for CSV export")
        content = await self._hass.async_add_executor_job(snapshot.to_csv)
        return f"kc761-{snapshot.snapshot_id}.csv", content

    async def async_set_active_sensor(self, source: int) -> None:
        self._validate_source(source)
        status = self._require_status()
        await self._execute_state_write(sensor_status=(status.sensor_status & ~0x03) | source)

    async def async_set_spectrum_accumulating(self, source: int, enabled: bool) -> None:
        self._validate_source(source)
        status = self._require_status()
        bit = 1 << (source + 2)
        value = status.sensor_status | bit if enabled else status.sensor_status & ~bit
        await self._execute_state_write(sensor_status=value)

    async def async_set_volume(self, volume: int) -> None:
        if volume not in (0, 1, 2):
            raise ValueError("volume must be 0, 1, or 2")
        status = self._require_status()
        await self._execute_state_write(volume_status=(status.volume_status & ~0x03) | volume)

    async def async_set_volume_flag(self, bit: int, enabled: bool) -> None:
        if bit not in (2, 3, 4):
            raise ValueError("unsupported volume flag")
        status = self._require_status()
        value = status.volume_status | (1 << bit) if enabled else status.volume_status & ~(1 << bit)
        await self._execute_state_write(volume_status=value)

    async def async_set_led_flag(self, bit: int, enabled: bool) -> None:
        if bit not in (0, 1, 2):
            raise ValueError("unsupported LED/screen flag")
        status = self._require_status()
        value = status.led_screen_status | (1 << bit) if enabled else status.led_screen_status & ~(1 << bit)
        await self._execute_state_write(led_screen_status=value)

    async def async_set_auto_upload(self, enabled: bool) -> None:
        await self._execute_state_write(auto_upload_status=int(enabled))

    async def async_sync_device_time(self) -> None:
        self._require_device_controls()
        async with self._write_lock:
            await self.client.async_set_device_time(int(datetime.now(UTC).timestamp()))
            self.control_commands += 1
            self.last_time_sync = datetime.now(UTC)
        await self.async_request_refresh()

    async def async_clear_statistics(self, source: int, kind: str) -> None:
        self._require_destructive_actions()
        self._validate_source(source)
        bit = 1 << source
        masks = {
            "spectrum": {"spectrum_mask": bit},
            "dose_curve": {"dose_curve_mask": bit},
            "cumulative_dose": {"cumulative_dose_mask": bit},
        }
        if kind not in masks:
            raise ValueError("unknown clear operation")
        async with self._write_lock:
            await self.client.async_clear_statistics(**masks[kind])
            self.control_commands += 1
            self._last_info_at = None
        await self.async_request_refresh()

    async def _execute_state_write(self, **values: int) -> None:
        self._require_device_controls()
        async with self._write_lock:
            await self.client.async_set_state(**values)
            self.control_commands += 1
        await self.async_request_refresh()

    def _validate_source(self, source: int) -> None:
        if not 0 <= source <= 2:
            raise ValueError("spectrum source outside 0..2")
        if self._device_info is None or not self._device_info.sensor_types[source]:
            raise ValueError("requested KC761 channel is not installed")

    def _require_status(self) -> StatusData:
        if self._snapshot is None:
            raise RuntimeError("KC761 has no current status snapshot")
        return self._snapshot.status

    def _require_archive(self) -> SpectrumArchive:
        if self._archive is None:
            raise RuntimeError("KC761 spectrum archive is not initialized")
        return self._archive

    def _require_device_controls(self) -> None:
        if not self.device_controls_enabled:
            raise PermissionError("KC761 device controls are disabled in integration options")

    def _require_destructive_actions(self) -> None:
        if not self.destructive_actions_enabled:
            raise PermissionError("KC761 destructive actions are disabled in integration options")

    def _notify_entities(self) -> None:
        if self._snapshot is not None:
            self.async_set_updated_data(self._snapshot)

    def _start_background(self, coroutine: Any, name: str) -> None:
        task = self._create_hass_task(coroutine, name)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _async_initial_spectrum(self) -> None:
        try:
            await self.async_capture_spectrum()
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.warning("Initial KC761 spectrum capture failed: %s", type(err).__name__)

    async def _async_time_sync_loop(self) -> None:
        while not self._shutdown:
            try:
                await self.async_sync_device_time()
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.warning("Scheduled KC761 time sync failed: %s", type(err).__name__)
            await asyncio.sleep(self.time_sync_interval * 3600)

    async def _async_spectrum_save_loop(self) -> None:
        while not self._shutdown:
            try:
                info = self._device_info
                if info is not None:
                    for source, sensor_type in enumerate(info.sensor_types):
                        if sensor_type:
                            await self.async_save_spectrum(source)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.warning("Scheduled KC761 spectrum save failed: %s", type(err).__name__)
            await asyncio.sleep(self.spectrum_save_interval * 60)

    async def async_reconfigure(
        self,
        host: str | None = None,
        port: int | None = None,
        *,
        transport: str | None = None,
        ble_address: str | None = None,
        ble_service_uuid: str | None = None,
    ) -> None:
        """Atomically validate and replace the endpoint while preserving DEVICE_ID.

        The old client is closed before a candidate is constructed.  Failed
        candidates are closed before a new client for the old endpoint is
        restored, so a single-entry device never has two active sessions.
        """
        async with self._write_lock:
            old_client = self.client
            old_transport = self.transport_type
            old_host, old_port = self.host, self.port
            old_ble_address, old_ble_service_uuid = self.ble_address, self.ble_service_uuid
            old_info = self._device_info
            old_info_at = self._last_info_at

            candidate_transport = transport or self.transport_type
            if candidate_transport not in (TRANSPORT_TCP, TRANSPORT_BLE):
                raise ValueError("unsupported KC761 transport")
            candidate_host = str(host or "")
            candidate_port = int(port or 0)
            candidate_ble_address = str(ble_address or "").strip()
            candidate_ble_service_uuid = str(ble_service_uuid or "").strip() or None
            if candidate_transport == TRANSPORT_TCP and (not candidate_host or not candidate_port):
                raise ValueError("TCP reconfigure requires host and port")
            if candidate_transport == TRANSPORT_BLE and not candidate_ble_address:
                raise ValueError("BLE reconfigure requires an address")

            self._auto_upload_spectrum.reset()
            await old_client.async_close()
            candidate = self._new_client(
                candidate_host,
                candidate_port,
                on_push=False,
                transport_type=candidate_transport,
                ble_address=candidate_ble_address,
                ble_service_uuid=candidate_ble_service_uuid,
                use_current_ble_service_uuid=False,
            )
            try:
                await candidate.async_connect()
                info = await candidate.async_get_device_info()
                if not isinstance(info, DeviceInfo):
                    raise UpdateFailed("KC761 returned no device information")
                self._validate_device_info(info)
                await candidate.async_get_status()
            except Exception as candidate_error:
                await self._close_client_quietly(candidate)
                try:
                    restored, restored_info, restored_info_at = await self._restore_client(
                        old_host,
                        old_port,
                        old_info,
                        transport_type=old_transport,
                        ble_address=old_ble_address,
                        ble_service_uuid=old_ble_service_uuid,
                        use_current_ble_service_uuid=False,
                    )
                except Exception as restore_error:
                    self.transport_type = old_transport
                    self.host, self.port = old_host, old_port
                    self.ble_address, self.ble_service_uuid = old_ble_address, old_ble_service_uuid
                    self.client = self._new_client(
                        old_host,
                        old_port,
                        transport_type=old_transport,
                        ble_address=old_ble_address,
                        ble_service_uuid=old_ble_service_uuid,
                        use_current_ble_service_uuid=False,
                    )
                    self._device_info = old_info
                    self._last_info_at = old_info_at
                    _LOGGER.error(
                        "KC761 endpoint rollback failed: candidate=%s restore=%s",
                        type(candidate_error).__name__,
                        type(restore_error).__name__,
                    )
                    raise ReconfigureRestoreError(candidate_error, restore_error) from candidate_error
                self.transport_type = old_transport
                self.host, self.port = old_host, old_port
                self.ble_address, self.ble_service_uuid = old_ble_address, old_ble_service_uuid
                self.client = restored
                self._device_info = restored_info
                self._last_info_at = restored_info_at
                raise

            candidate.on_push = self._async_push
            self.transport_type = candidate_transport
            self.host, self.port = candidate_host, candidate_port
            self.ble_address, self.ble_service_uuid = (
                candidate_ble_address,
                candidate_ble_service_uuid,
            )
            self.client = candidate
            self._device_info = info
            self._last_info_at = datetime.now(UTC)
            self.last_error = None
            self.retry_delay = None

    async def _restore_client(
        self,
        host: str,
        port: int,
        previous_info: DeviceInfo | None,
        *,
        transport_type: str | None = None,
        ble_address: str | None = None,
        ble_service_uuid: str | None = None,
        use_current_ble_service_uuid: bool = True,
    ) -> tuple[KC761Client, DeviceInfo | None, datetime | None]:
        """Create and validate the one replacement client after a rollback."""
        restored = self._new_client(
            host,
            port,
            transport_type=transport_type,
            ble_address=ble_address,
            ble_service_uuid=ble_service_uuid,
            use_current_ble_service_uuid=use_current_ble_service_uuid,
        )
        try:
            await restored.async_connect()
            restored_info = previous_info
            restored_info_at: datetime | None = None
            if previous_info is not None:
                info = await restored.async_get_device_info()
                if not isinstance(info, DeviceInfo):
                    raise UpdateFailed("KC761 returned no device information")
                self._validate_device_info(info)
                restored_info = info
                restored_info_at = datetime.now(UTC)
            return restored, restored_info, restored_info_at
        except Exception:
            await self._close_client_quietly(restored)
            raise

    @staticmethod
    async def _close_client_quietly(client: KC761Client) -> None:
        try:
            await client.async_close()
        except Exception as err:
            _LOGGER.debug("KC761 client close failed: %s", type(err).__name__)

    def _new_client(
        self,
        host: str,
        port: int,
        *,
        on_push: bool = True,
        transport_type: str | None = None,
        ble_address: str | None = None,
        ble_service_uuid: str | None = None,
        use_current_ble_service_uuid: bool = True,
    ) -> KC761Client:
        selected_transport = transport_type or self.transport_type
        if selected_transport == TRANSPORT_BLE:
            address = str(ble_address or self.ble_address).strip()
            transport = BleTransport(
                self._hass,
                address,
                service_uuid=(
                    self.ble_service_uuid
                    if use_current_ble_service_uuid and ble_service_uuid is None
                    else ble_service_uuid
                ),
            )
            return KC761Client(
                address,
                0,
                transport=transport,
                spectrum_channel_counts=self.spectrum_channel_counts,
                on_push=self._async_push if on_push else None,
                task_creator=self._create_hass_task,
            )
        return KC761Client(
            host,
            port,
            spectrum_channel_counts=self.spectrum_channel_counts,
            on_push=self._async_push if on_push else None,
            task_creator=self._create_hass_task,
        )

    def _create_hass_task(self, coroutine: Any, name: str) -> asyncio.Task[None]:
        return self.entry.async_create_background_task(self._hass, coroutine, name)

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
        self._start_background(
            self._async_reload_after_hardware_change(),
            "kc761-hardware-reload",
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


def _spectrum_channel_counts(options: dict[str, Any]) -> tuple[int, int, int]:
    """Normalize the three explicit per-channel spectrum lengths."""
    keys = (
        CONF_GAMMA_SPECTRUM_CHANNELS,
        CONF_NEUTRON_SPECTRUM_CHANNELS,
        CONF_PIN_SPECTRUM_CHANNELS,
    )
    values: list[int] = []
    for index, key in enumerate(keys):
        try:
            value = int(options.get(key, DEFAULT_SPECTRUM_CHANNEL_COUNTS[index]))
        except (TypeError, ValueError):
            value = DEFAULT_SPECTRUM_CHANNEL_COUNTS[index]
        if value not in SPECTRUM_CHANNEL_COUNT_OPTIONS:
            value = DEFAULT_SPECTRUM_CHANNEL_COUNTS[index]
        values.append(value)
    return tuple(values)  # type: ignore[return-value]


def _device_signature(info: DeviceInfo) -> tuple[Any, ...]:
    return (
        info.device_model,
        info.hardware_version,
        info.firmware_version,
        info.coprocessor_firmware_version,
        info.sensor_types,
    )
