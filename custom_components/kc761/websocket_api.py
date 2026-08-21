"""Authenticated WebSocket API for current and archived KC761 spectra."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import KC761Coordinator


def async_register(hass: HomeAssistant) -> None:
    """Register KC761 commands once for the integration domain."""
    websocket_api.async_register_command(hass, websocket_info)
    websocket_api.async_register_command(hass, websocket_spectrum_list)
    websocket_api.async_register_command(hass, websocket_spectrum_get)
    websocket_api.async_register_command(hass, websocket_spectrum_capture)
    websocket_api.async_register_command(hass, websocket_spectrum_save)
    websocket_api.async_register_command(hass, websocket_spectrum_csv)
    websocket_api.async_register_command(hass, websocket_clear)


def _coordinator(hass: HomeAssistant, entry_id: str) -> KC761Coordinator:
    value = hass.data.get(DOMAIN, {}).get(entry_id)
    if not isinstance(value, KC761Coordinator):
        raise ValueError("KC761 configuration entry is not loaded")
    return value


def _send_exception(connection: websocket_api.ActiveConnection, message_id: int, err: Exception) -> None:
    connection.send_error(message_id, "kc761_error", f"KC761 operation failed: {type(err).__name__}")


@websocket_api.websocket_command({vol.Required("type"): "kc761/info", vol.Required("entry_id"): str})
@websocket_api.async_response
async def websocket_info(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return local device identity for frontend confirmation dialogs."""
    try:
        coordinator = _coordinator(hass, msg["entry_id"])
        info = coordinator.device_info
        if info is None:
            raise RuntimeError("device information is unavailable")
        connection.send_result(
            msg["id"],
            {
                "model": info.model_name,
                "model_id": info.device_model,
                "hardware_version": info.hw_version_text,
                "firmware_version": info.fw_version_text,
                "coprocessor_firmware_version": info.co_fw_version_text,
                "serial_number": info.device_id,
                "sensor_types": list(info.sensor_types),
            },
        )
    except Exception as err:
        _send_exception(connection, msg["id"], err)


@websocket_api.websocket_command(
    {vol.Required("type"): "kc761/spectrum/list", vol.Required("entry_id"): str}
)
@websocket_api.async_response
async def websocket_spectrum_list(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    try:
        result = await _coordinator(hass, msg["entry_id"]).async_list_saved_spectra()
        connection.send_result(msg["id"], result)
    except Exception as err:
        _send_exception(connection, msg["id"], err)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "kc761/spectrum/get",
        vol.Required("entry_id"): str,
        vol.Optional("snapshot_id"): str,
    }
)
@websocket_api.async_response
async def websocket_spectrum_get(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    try:
        coordinator = _coordinator(hass, msg["entry_id"])
        snapshot_id = msg.get("snapshot_id")
        snapshot = (
            coordinator.latest_spectrum
            if snapshot_id is None
            else await coordinator.async_get_saved_spectrum(snapshot_id)
        )
        if snapshot is None:
            raise RuntimeError("no spectrum is available")
        connection.send_result(msg["id"], snapshot.entity_attributes())
    except Exception as err:
        _send_exception(connection, msg["id"], err)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "kc761/spectrum/capture",
        vol.Required("entry_id"): str,
        vol.Optional("source"): vol.All(vol.Coerce(int), vol.Range(min=0, max=2)),
    }
)
@websocket_api.async_response
async def websocket_spectrum_capture(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    try:
        snapshot = await _coordinator(hass, msg["entry_id"]).async_capture_spectrum(msg.get("source"))
        connection.send_result(msg["id"], snapshot.entity_attributes())
    except Exception as err:
        _send_exception(connection, msg["id"], err)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "kc761/spectrum/save",
        vol.Required("entry_id"): str,
        vol.Optional("source"): vol.All(vol.Coerce(int), vol.Range(min=0, max=2)),
    }
)
@websocket_api.async_response
async def websocket_spectrum_save(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    try:
        snapshot = await _coordinator(hass, msg["entry_id"]).async_save_spectrum(msg.get("source"))
        connection.send_result(msg["id"], snapshot.metadata())
    except Exception as err:
        _send_exception(connection, msg["id"], err)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "kc761/spectrum/csv",
        vol.Required("entry_id"): str,
        vol.Optional("snapshot_id"): str,
    }
)
@websocket_api.async_response
async def websocket_spectrum_csv(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    try:
        filename, content = await _coordinator(hass, msg["entry_id"]).async_export_spectrum_csv(
            msg.get("snapshot_id")
        )
        connection.send_result(msg["id"], {"filename": filename, "content": content})
    except Exception as err:
        _send_exception(connection, msg["id"], err)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "kc761/clear",
        vol.Required("entry_id"): str,
        vol.Required("source"): vol.All(vol.Coerce(int), vol.Range(min=0, max=2)),
        vol.Required("kind"): vol.In(("spectrum", "dose_curve", "cumulative_dose")),
    }
)
@websocket_api.async_response
async def websocket_clear(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    try:
        await _coordinator(hass, msg["entry_id"]).async_clear_statistics(msg["source"], msg["kind"])
        connection.send_result(msg["id"], {"success": True})
    except Exception as err:
        _send_exception(connection, msg["id"], err)
