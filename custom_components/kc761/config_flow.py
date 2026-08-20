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
    CONF_CREATE_RAW_ENTITIES,
    CONF_INFO_INTERVAL,
    CONF_SPECTRUM_SAVE_ENABLED,
    CONF_SPECTRUM_SAVE_INTERVAL,
    CONF_STATUS_INTERVAL,
    CONF_TIME_SYNC_ENABLED,
    CONF_TIME_SYNC_INTERVAL,
    DEFAULT_CREATE_RAW_ENTITIES,
    DEFAULT_INFO_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_STATUS_INTERVAL,
    DEFAULT_SPECTRUM_SAVE_ENABLED,
    DEFAULT_SPECTRUM_SAVE_INTERVAL,
    DEFAULT_TIME_SYNC_ENABLED,
    DEFAULT_TIME_SYNC_INTERVAL,
    DOMAIN,
    MAX_INFO_INTERVAL,
    MAX_SPECTRUM_SAVE_INTERVAL,
    MAX_STATUS_INTERVAL,
    MAX_TIME_SYNC_INTERVAL,
    MIN_INFO_INTERVAL,
    MIN_SPECTRUM_SAVE_INTERVAL,
    MIN_STATUS_INTERVAL,
    MIN_TIME_SYNC_INTERVAL,
)
from .protocol.client import KC761Client

_LOGGER = logging.getLogger(__name__)


class KC761ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle initial setup and endpoint reconfiguration."""

    VERSION = 3

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
            return self.async_show_form(step_id="user", data_schema=_connection_schema(user_input), errors=errors)
        await self.async_set_unique_id(info.device_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"{info.model_name} {info.device_id[-4:]}",
            data={CONF_HOST: host, CONF_PORT: port, "device_id": info.device_id},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            return self.async_abort(reason="unknown")
        if user_input is None:
            return self.async_show_form(step_id="reconfigure", data_schema=_connection_schema(entry.data))
        host = str(user_input[CONF_HOST]).strip()
        port = int(user_input[CONF_PORT])
        client = KC761Client(host, port)
        info = None
        error: str | None = None
        try:
            await client.async_connect()
            info = await client.async_get_device_info()
        except Exception:
            error = "cannot_connect"
        finally:
            await client.async_close()
        if error is not None or info is None:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=_connection_schema(user_input),
                errors={"base": error or "cannot_connect"},
            )
        expected_device_id = str(entry.data.get("device_id", entry.unique_id or ""))
        if info.device_id != expected_device_id:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=_connection_schema(user_input),
                errors={"base": "device_mismatch"},
            )
        return self.async_update_reload_and_abort(
            entry,
            data_updates={CONF_HOST: host, CONF_PORT: port},
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        del config_entry
        return KC761OptionsFlow()


class KC761OptionsFlow(config_entries.OptionsFlow):
    """Configure polling, scheduled time sync, and spectrum archiving."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_STATUS_INTERVAL,
                    default=options.get(CONF_STATUS_INTERVAL, DEFAULT_STATUS_INTERVAL),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_STATUS_INTERVAL, max=MAX_STATUS_INTERVAL),
                ),
                vol.Required(
                    CONF_INFO_INTERVAL,
                    default=options.get(CONF_INFO_INTERVAL, DEFAULT_INFO_INTERVAL),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_INFO_INTERVAL, max=MAX_INFO_INTERVAL),
                ),
                vol.Required(
                    CONF_CREATE_RAW_ENTITIES,
                    default=options.get(CONF_CREATE_RAW_ENTITIES, DEFAULT_CREATE_RAW_ENTITIES),
                ): bool,
                vol.Required(
                    CONF_TIME_SYNC_ENABLED,
                    default=options.get(CONF_TIME_SYNC_ENABLED, DEFAULT_TIME_SYNC_ENABLED),
                ): bool,
                vol.Required(
                    CONF_TIME_SYNC_INTERVAL,
                    default=options.get(CONF_TIME_SYNC_INTERVAL, DEFAULT_TIME_SYNC_INTERVAL),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_TIME_SYNC_INTERVAL, max=MAX_TIME_SYNC_INTERVAL),
                ),
                vol.Required(
                    CONF_SPECTRUM_SAVE_ENABLED,
                    default=options.get(CONF_SPECTRUM_SAVE_ENABLED, DEFAULT_SPECTRUM_SAVE_ENABLED),
                ): bool,
                vol.Required(
                    CONF_SPECTRUM_SAVE_INTERVAL,
                    default=options.get(CONF_SPECTRUM_SAVE_INTERVAL, DEFAULT_SPECTRUM_SAVE_INTERVAL),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_SPECTRUM_SAVE_INTERVAL, max=MAX_SPECTRUM_SAVE_INTERVAL),
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
