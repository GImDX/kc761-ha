"""Redacted diagnostics for KC761."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import VERSION
from .coordinator import KC761Coordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return bounded diagnostics without host, full ID, payload, or secrets."""
    del hass
    coordinator: KC761Coordinator = entry.runtime_data
    info = coordinator.device_info
    return {
        "integration_version": VERSION,
        "config_version": entry.version,
        "options": {
            "status_interval": coordinator.status_interval,
            "info_interval": coordinator.info_interval,
        },
        "device": None
        if info is None
        else {
            "model": info.model_name,
            "hardware_version": info.hw_version_text,
            "firmware_version": info.fw_version_text,
            "coprocessor_firmware_version": info.co_fw_version_text,
            "device_id_suffix": info.device_id[-4:],
            "hardware_verified": info.hardware_verified,
        },
        "connection": {
            "connected": coordinator.client.connected,
            "last_success": coordinator.last_success.isoformat()
            if coordinator.last_success
            else None,
            "consecutive_failures": coordinator.consecutive_failures,
            "reconnects": coordinator.reconnects,
            "protocol_errors": coordinator.protocol_errors,
            "last_error": coordinator.last_error,
            "last_frame": None
            if coordinator.client.last_frame is None
            else {
                "flag": coordinator.client.last_frame.flag,
                "bag_len": coordinator.client.last_frame.bag_len,
            },
        },
    }

