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
    BLE_VERIFIED_SERVICE_UUID,
    CONF_BLE_ADDRESS,
    CONF_BLE_SERVICE_UUID,
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
    CONF_TRANSPORT,
    SUPPORTED_TRANSPORTS,
    TRANSPORT_BLE,
    TRANSPORT_TCP,
)
from .coordinator import DeviceIdentityError, KC761Coordinator
from .endpoint import Endpoint as _Endpoint
from .endpoint import parse_endpoint as _parse_endpoint
from .endpoint import replace_endpoint_data
from .protocol.client import KC761Client
from .protocol.transport import BleTransport

_LOGGER = logging.getLogger(__name__)


class KC761ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle initial setup and endpoint reconfiguration."""

    VERSION = 6

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=_connection_schema())
        return await self._async_validate_and_create(user_input)

    async def _async_validate_and_create(self, user_input: dict[str, Any]) -> ConfigFlowResult:
        try:
            endpoint = _parse_endpoint(user_input)
        except ValueError as err:
            return self.async_show_form(
                step_id="user",
                data_schema=_connection_schema(user_input),
                errors={"base": str(err)},
            )
        errors: dict[str, str] = {}
        client = self._new_flow_client(endpoint)
        info = None
        try:
            await client.async_connect()
            info = await client.async_get_device_info()
            await client.async_get_status()
        except Exception:
            errors["base"] = "cannot_connect"
        finally:
            await client.async_close()
        if info is None or not info.device_id:
            errors.setdefault("base", "cannot_connect")
            return self.async_show_form(step_id="user", data_schema=_connection_schema(user_input), errors=errors)
        await self.async_set_unique_id(info.device_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"{info.model_name} {info.device_id[-4:]}",
            data={**endpoint.as_data(), "device_id": info.device_id},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            return self.async_abort(reason="unknown")
        if user_input is None:
            return self.async_show_form(step_id="reconfigure", data_schema=_connection_schema(entry.data))
        defaults = entry.data
        if user_input.get(CONF_TRANSPORT) == TRANSPORT_BLE and CONF_BLE_SERVICE_UUID not in user_input:
            # An explicit BLE reconfigure without an override uses the
            # verified device service instead of inheriting a stale override.
            defaults = {**defaults, CONF_BLE_SERVICE_UUID: BLE_VERIFIED_SERVICE_UUID}
        try:
            endpoint = _parse_endpoint(user_input, defaults=defaults)
        except ValueError as err:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=_connection_schema(user_input),
                errors={"base": str(err)},
            )
        expected_device_id = str(entry.data.get("device_id", entry.unique_id or ""))
        try:
            coordinator = self.hass.data.get(DOMAIN, {}).get(entry.entry_id)
            if isinstance(coordinator, KC761Coordinator):
                # Preserve the old call shape for existing TCP entries and
                # third-party test doubles; the extended form is used for
                # BLE or an explicit transport switch.
                if (
                    endpoint.transport == TRANSPORT_TCP
                    and CONF_TRANSPORT not in user_input
                    and CONF_BLE_ADDRESS not in user_input
                ):
                    await coordinator.async_reconfigure(endpoint.host, endpoint.port)
                else:
                    await coordinator.async_reconfigure(
                        endpoint.host,
                        endpoint.port,
                        transport=endpoint.transport,
                        ble_address=endpoint.ble_address,
                        ble_service_uuid=endpoint.ble_service_uuid,
                    )
            else:
                await self._async_validate_reconfigure_endpoint(
                    endpoint.host,
                    endpoint.port,
                    transport=endpoint.transport,
                    ble_address=endpoint.ble_address,
                    ble_service_uuid=endpoint.ble_service_uuid,
                    expected_device_id=expected_device_id,
                )
        except DeviceIdentityError:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=_connection_schema(user_input),
                errors={"base": "device_mismatch"},
            )
        except Exception as err:
            _LOGGER.warning("KC761 reconfigure preflight failed: %s", type(err).__name__)
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=_connection_schema(user_input),
                errors={"base": "cannot_connect"},
            )
        updated_data = replace_endpoint_data(entry.data, endpoint)
        self.hass.config_entries.async_update_entry(entry, data=updated_data)
        return self.async_abort(reason="reconfigure_successful")

    async def _async_validate_reconfigure_endpoint(
        self,
        host: str,
        port: int,
        *,
        transport: str = TRANSPORT_TCP,
        ble_address: str = "",
        ble_service_uuid: str | None = None,
        expected_device_id: str | None = None,
    ) -> None:
        """Preflight an entry without a loaded coordinator.

        A setup-retry entry has no connection owner in ``hass.data``.  This
        temporary client is therefore safe and is always closed before the
        flow returns.
        """
        endpoint = _Endpoint(transport, host, port, ble_address, ble_service_uuid)
        client = self._new_flow_client(endpoint)
        try:
            await client.async_connect()
            info = await client.async_get_device_info()
            if info is None:
                raise ValueError("KC761 returned no device information")
            await client.async_get_status()
            if expected_device_id is None:
                entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
                expected_device_id = str(entry.data.get("device_id", entry.unique_id or ""))
            if info.device_id != expected_device_id:
                raise DeviceIdentityError("KC761 DEVICE_ID changed")
        finally:
            await client.async_close()

    def _new_flow_client(self, endpoint: _Endpoint) -> KC761Client:
        """Create a temporary client whose resources belong to this flow."""
        if endpoint.transport == TRANSPORT_BLE:
            return KC761Client(
                endpoint.ble_address,
                0,
                transport=BleTransport(
                    self.hass,
                    endpoint.ble_address,
                    service_uuid=endpoint.ble_service_uuid,
                ),
            )
        return KC761Client(endpoint.host, endpoint.port)

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
            vol.Optional(
                CONF_TRANSPORT,
                default=defaults.get(CONF_TRANSPORT, TRANSPORT_TCP),
            ): vol.In(SUPPORTED_TRANSPORTS),
            vol.Optional(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Optional(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Optional(CONF_BLE_ADDRESS, default=defaults.get(CONF_BLE_ADDRESS, "")): str,
            vol.Optional(
                CONF_BLE_SERVICE_UUID,
                default=defaults.get(CONF_BLE_SERVICE_UUID, BLE_VERIFIED_SERVICE_UUID),
            ): str,
        }
    )
