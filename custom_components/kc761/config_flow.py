"""Config and options flows for KC761."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback

from .const import (
    DEFAULT_INFO_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_STATUS_INTERVAL,
    DOMAIN,
    MAX_INFO_INTERVAL,
    MAX_STATUS_INTERVAL,
    MIN_INFO_INTERVAL,
    MIN_STATUS_INTERVAL,
)
from .protocol.client import KC761Client

_LOGGER = logging.getLogger(__name__)


class KC761ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle initial setup and endpoint reconfiguration."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=_connection_schema())
        return await self._async_validate_and_create(user_input)

    async def _async_validate_and_create(self, user_input: dict[str, Any]) -> ConfigFlowResult:
        host = str(user_input[CONF_HOST]).strip()
        port = int(user_input[CONF_PORT])
        errors: dict[str, str] = {}
        client = KC761Client(host, port)
        try:
            await client.async_connect()
            info = await client.async_get_device_info()
            await client.async_get_status()
        except Exception:
            errors["base"] = "cannot_connect"
            info = None
        finally:
            await client.async_close()
        if info is None:
            return self.async_show_form(
                step_id="user", data_schema=_connection_schema(user_input), errors=errors
            )
        unique_id = normalize_device_id(info.device_id)
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"{info.model_name} {info.device_id[-4:]}",
            data={CONF_HOST: host, CONF_PORT: port, "device_id": info.device_id},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            return self.async_abort(reason="unknown")
        if user_input is None:
            return self.async_show_form(
                step_id="reconfigure", data_schema=_connection_schema(entry.data)
            )
        host = str(user_input[CONF_HOST]).strip()
        port = int(user_input[CONF_PORT])
        client = KC761Client(host, port)
        try:
            await client.async_connect()
            info = await client.async_get_device_info()
            if normalize_device_id(info.device_id) != entry.unique_id:
                raise ValueError("DEVICE_ID mismatch")
        except ValueError:
            await client.async_close()
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=_connection_schema(user_input),
                errors={"base": "device_mismatch"},
            )
        except Exception:
            await client.async_close()
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=_connection_schema(user_input),
                errors={"base": "cannot_connect"},
            )
        await client.async_close()
        return self.async_update_reload_and_abort(
            entry,
            data_updates={CONF_HOST: host, CONF_PORT: port},
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return KC761OptionsFlow(config_entry)


class KC761OptionsFlow(config_entries.OptionsFlow):
    """Configure polling only; no transport or control settings."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    "status_interval",
                    default=options.get("status_interval", DEFAULT_STATUS_INTERVAL),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_STATUS_INTERVAL, max=MAX_STATUS_INTERVAL),
                ),
                vol.Required(
                    "info_interval",
                    default=options.get("info_interval", DEFAULT_INFO_INTERVAL),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_INFO_INTERVAL, max=MAX_INFO_INTERVAL),
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)


def _connection_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
        }
    )


def normalize_device_id(device_id: str) -> str:
    """Normalize only separators needed for a stable unique ID."""
    return "".join(
        character if character.isalnum() else "_" for character in device_id
    ).strip("_").lower()
