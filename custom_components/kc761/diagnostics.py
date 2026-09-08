"""Redacted diagnostics for KC761."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CREATE_RAW_ENTITIES,
    CONF_ENABLE_DESTRUCTIVE_ACTIONS,
    CONF_ENABLE_DEVICE_CONTROLS,
    CONF_GAMMA_SPECTRUM_CHANNELS,
    CONF_INFO_INTERVAL,
    CONF_NEUTRON_SPECTRUM_CHANNELS,
    CONF_PIN_SPECTRUM_CHANNELS,
    CONF_SPECTRUM_SAVE_ENABLED,
    CONF_SPECTRUM_SAVE_INTERVAL,
    CONF_STATUS_INTERVAL,
    CONF_TIME_SYNC_ENABLED,
    CONF_TIME_SYNC_INTERVAL,
    DEFAULT_CREATE_RAW_ENTITIES,
    DEFAULT_ENABLE_DESTRUCTIVE_ACTIONS,
    DEFAULT_ENABLE_DEVICE_CONTROLS,
    DEFAULT_INFO_INTERVAL,
    DEFAULT_SPECTRUM_CHANNEL_COUNTS,
    DEFAULT_SPECTRUM_SAVE_INTERVAL,
    DEFAULT_SPECTRUM_SAVE_ENABLED,
    DEFAULT_STATUS_INTERVAL,
    DEFAULT_TIME_SYNC_ENABLED,
    DEFAULT_TIME_SYNC_INTERVAL,
    MODEL_NAMES,
    VERSION,
)
from .coordinator import KC761Coordinator
from .protocol.codec import CODEC_PROFILE, HARDWARE_VERIFIED
from .protocol.models import redacted_device_id_suffix


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Return bounded diagnostics without host, full ID, payload, or secrets."""
    del hass
    coordinator: KC761Coordinator | None = getattr(entry, "runtime_data", None)
    if coordinator is None:
        return _diagnostics_without_runtime(entry)
    info = coordinator.device_info
    client = coordinator.client
    parser = getattr(client, "parser", None)
    last_frame = getattr(client, "last_frame", None)
    received_frame_flags: dict[int, int] = dict(getattr(client, "received_frame_flags", {}))
    transport_type = getattr(coordinator, "transport_type", "tcp")
    if transport_type not in {"tcp", "ble"}:
        transport_type = "tcp"
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
            "legacy_spectrum_channel_options": list(
                getattr(coordinator, "legacy_spectrum_channel_counts", (None, None, None))
            ),
            CONF_ENABLE_DEVICE_CONTROLS: bool(
                getattr(coordinator, "device_controls_enabled", False)
            ),
            CONF_ENABLE_DESTRUCTIVE_ACTIONS: bool(
                getattr(coordinator, "destructive_actions_enabled", False)
            ),
            CONF_TIME_SYNC_ENABLED: bool(getattr(coordinator, "time_sync_enabled", False)),
            CONF_TIME_SYNC_INTERVAL: getattr(coordinator, "time_sync_interval", None),
            CONF_SPECTRUM_SAVE_ENABLED: bool(getattr(coordinator, "spectrum_save_enabled", False)),
            CONF_SPECTRUM_SAVE_INTERVAL: getattr(coordinator, "spectrum_save_interval", None),
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
            "transport": transport_type,
            "connected": client.connected,
            "last_success": coordinator.last_success.isoformat() if coordinator.last_success else None,
            "consecutive_failures": coordinator.consecutive_failures,
            "reconnects": coordinator.reconnects,
            "protocol_errors": coordinator.protocol_errors,
            "data_errors": getattr(client, "data_errors", 0),
            "consecutive_protocol_errors": getattr(client, "consecutive_protocol_errors", 0),
            "responses": getattr(client, "responses", 0),
            "push_frames": getattr(client, "push_frames", 0),
            "received_frame_flags": received_frame_flags,
            "received_frame_flags_scope": "client_lifetime",
            "received_frame_flags_reset": "client_recreation",
            "received_frame_flags_basis": "complete_routed_frames_before_semantic_decode",
            "client_instance_token": getattr(client, "client_instance_token", None),
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
        "controls": {
            "commands": getattr(coordinator, "control_commands", 0),
            "last_time_sync": (
                coordinator.last_time_sync.isoformat()
                if getattr(coordinator, "last_time_sync", None)
                else None
            ),
        },
        "spectrum": {
            "channel_counts": None,
            "legacy_channel_counts": list(
                getattr(coordinator, "legacy_spectrum_channel_counts", (None, None, None))
            ),
            "latest": (
                coordinator.latest_spectrum.metadata()
                if getattr(coordinator, "latest_spectrum", None)
                else None
            ),
            "last_protocol_result": _spectrum_result_metadata(
                getattr(coordinator, "last_spectrum_data", None)
            ),
            "assembler": getattr(
                getattr(coordinator, "_auto_upload_spectrum", None),
                "state",
                lambda: None,
            )(),
            "last_saved": (
                coordinator.last_spectrum_save.isoformat()
                if getattr(coordinator, "last_spectrum_save", None)
                else None
            ),
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


def _diagnostics_without_runtime(entry: ConfigEntry) -> dict[str, Any]:
    """Return a safe, bounded snapshot for disabled, failed, or unloaded entries."""
    raw_options = getattr(entry, "options", {})
    options = raw_options if isinstance(raw_options, Mapping) else {}
    raw_data = getattr(entry, "data", {})
    data = raw_data if isinstance(raw_data, Mapping) else {}
    transport = str(data.get("transport", "tcp")).strip().lower()
    if transport not in {"tcp", "ble"}:
        transport = "tcp"
    channel_counts = _entry_channel_counts(options)
    return {
        "integration_version": VERSION,
        "config_version": getattr(entry, "version", None),
        "protocol_profile": CODEC_PROFILE,
        "hardware_verified": HARDWARE_VERIFIED,
        "options": {
            CONF_STATUS_INTERVAL: _option_int(options, CONF_STATUS_INTERVAL, DEFAULT_STATUS_INTERVAL),
            CONF_INFO_INTERVAL: _option_int(options, CONF_INFO_INTERVAL, DEFAULT_INFO_INTERVAL),
            CONF_CREATE_RAW_ENTITIES: bool(options.get(CONF_CREATE_RAW_ENTITIES, DEFAULT_CREATE_RAW_ENTITIES)),
            "legacy_spectrum_channel_options": list(channel_counts),
            CONF_ENABLE_DEVICE_CONTROLS: bool(
                options.get(CONF_ENABLE_DEVICE_CONTROLS, DEFAULT_ENABLE_DEVICE_CONTROLS)
            ),
            CONF_ENABLE_DESTRUCTIVE_ACTIONS: bool(
                options.get(CONF_ENABLE_DESTRUCTIVE_ACTIONS, DEFAULT_ENABLE_DESTRUCTIVE_ACTIONS)
            ),
            CONF_TIME_SYNC_ENABLED: bool(options.get(CONF_TIME_SYNC_ENABLED, DEFAULT_TIME_SYNC_ENABLED)),
            CONF_TIME_SYNC_INTERVAL: options.get(CONF_TIME_SYNC_INTERVAL, DEFAULT_TIME_SYNC_INTERVAL),
            CONF_SPECTRUM_SAVE_ENABLED: bool(
                options.get(CONF_SPECTRUM_SAVE_ENABLED, DEFAULT_SPECTRUM_SAVE_ENABLED)
            ),
            CONF_SPECTRUM_SAVE_INTERVAL: options.get(
                CONF_SPECTRUM_SAVE_INTERVAL, DEFAULT_SPECTRUM_SAVE_INTERVAL
            ),
        },
        "device": None,
        "connection": {
            "transport": transport,
            "connected": False,
            "last_success": None,
            "consecutive_failures": 0,
            "reconnects": 0,
            "protocol_errors": 0,
            "data_errors": 0,
            "consecutive_protocol_errors": 0,
            "responses": 0,
            "push_frames": 0,
            "received_frame_flags": {},
            "received_frame_flags_scope": "no_runtime_client",
            "received_frame_flags_reset": "client_recreation",
            "received_frame_flags_basis": "complete_routed_frames_before_semantic_decode",
            "client_instance_token": None,
            "late_responses": 0,
            "unhandled_frames": 0,
            "identity_mismatches": 0,
            "retry_delay": None,
            "last_error": None,
            "last_frame": None,
        },
        "controls": {"commands": 0, "last_time_sync": None},
        "spectrum": {
            "channel_counts": None,
            "legacy_channel_counts": list(channel_counts),
            "latest": None,
            "last_protocol_result": None,
            "assembler": None,
            "last_saved": None,
        },
        "framing": None,
    }


def _option_int(options: Mapping[str, Any], key: str, default: int) -> int:
    """Read a bounded diagnostic option without exposing arbitrary values."""
    try:
        return int(options.get(key, default))
    except (TypeError, ValueError):
        return default


def _spectrum_result_metadata(value: Any) -> dict[str, Any] | None:
    """Return bounded protocol-length evidence without exposing point data."""
    if value is None:
        return None
    return {
        "source": getattr(value, "source", None),
        "point_count": len(getattr(value, "counts", ())),
        "segment_count": getattr(value, "segment_count", None),
        "spectrum_status": getattr(value, "spectrum_status", None),
        "length_confidence": getattr(value, "length_confidence", None),
        "completion_basis": getattr(value, "completion_basis", None),
        "received_count": getattr(value, "received_count", None),
        "received_span": getattr(value, "received_span", None),
        "padding_count": getattr(value, "padding_count", None),
    }


def _entry_channel_counts(options: Mapping[str, Any]) -> tuple[int, int, int]:
    """Normalize persisted channel lengths when no coordinator is available."""
    values: list[int] = []
    keys = (
        CONF_GAMMA_SPECTRUM_CHANNELS,
        CONF_NEUTRON_SPECTRUM_CHANNELS,
        CONF_PIN_SPECTRUM_CHANNELS,
    )
    for key, default in zip(keys, DEFAULT_SPECTRUM_CHANNEL_COUNTS, strict=True):
        value = _option_int(options, key, default)
        values.append(value if value in (1024, 2048, 4096) else default)
    return tuple(values)  # type: ignore[return-value]
