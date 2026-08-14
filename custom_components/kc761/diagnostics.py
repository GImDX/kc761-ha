"""Redacted diagnostics for KC761."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CREATE_RAW_ENTITIES,
    DEFAULT_CREATE_RAW_ENTITIES,
    MODEL_NAMES,
    VERSION,
)
from .coordinator import KC761Coordinator
from .protocol.codec import CODEC_PROFILE, HARDWARE_VERIFIED
from .protocol.models import redacted_device_id_suffix


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Return bounded diagnostics without host, full ID, payload, or secrets."""
    del hass
    coordinator: KC761Coordinator = entry.runtime_data
    info = coordinator.device_info
    client = coordinator.client
    parser = getattr(client, "parser", None)
    last_frame = getattr(client, "last_frame", None)
    compatibility_warnings: list[str] = []
    if info is not None and info.device_model not in MODEL_NAMES:
        compatibility_warnings.append(f"unknown_model:{info.device_model}")
    return {
        "integration_version": VERSION,
        "config_version": entry.version,
        "protocol_profile": CODEC_PROFILE,
        "hardware_verified": HARDWARE_VERIFIED,
        "options": {
            "status_interval": coordinator.status_interval,
            "info_interval": coordinator.info_interval,
            CONF_CREATE_RAW_ENTITIES: bool(
                getattr(
                    coordinator,
                    "create_raw_entities",
                    entry.options.get(CONF_CREATE_RAW_ENTITIES, DEFAULT_CREATE_RAW_ENTITIES),
                )
            ),
        },
        "device": None
        if info is None
        else {
            "model": info.model_name,
            "hardware_version": info.hw_version_text,
            "firmware_version": info.fw_version_text,
            "coprocessor_firmware_version": info.co_fw_version_text,
            "sensor_types": list(info.sensor_types),
            "device_id_suffix": redacted_device_id_suffix(info.device_id),
            "hardware_verified": info.hardware_verified,
            "compatibility_warnings": compatibility_warnings,
        },
        "connection": {
            "connected": client.connected,
            "last_success": coordinator.last_success.isoformat() if coordinator.last_success else None,
            "consecutive_failures": coordinator.consecutive_failures,
            "reconnects": coordinator.reconnects,
            "protocol_errors": coordinator.protocol_errors,
            "data_errors": getattr(client, "data_errors", 0),
            "consecutive_protocol_errors": getattr(client, "consecutive_protocol_errors", 0),
            "responses": getattr(client, "responses", 0),
            "push_frames": getattr(client, "push_frames", 0),
            "late_responses": getattr(client, "late_responses", 0),
            "unhandled_frames": getattr(client, "unhandled_frames", 0),
            "identity_mismatches": getattr(coordinator, "identity_mismatches", 0),
            "retry_delay": getattr(coordinator, "retry_delay", None),
            "last_error": coordinator.last_error,
            "last_frame": None
            if last_frame is None
            else {
                "flag": last_frame.flag,
                "bag_len": last_frame.bag_len,
            },
        },
        "framing": None
        if parser is None
        else {
            "errors": parser.errors,
            "dropped_bytes": parser.dropped_bytes,
            "buffered_bytes": parser.buffered_bytes,
            "recent_errors": list(parser.recent_errors),
        },
    }
