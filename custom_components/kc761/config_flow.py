"""Config and options flows for KC761."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    BLE_VERIFIED_SERVICE_UUID,
    CONF_BLE_ADDRESS,
    CONF_BLE_SERVICE_UUID,
    CONF_CREATE_RAW_ENTITIES,
    CONF_ENABLE_DESTRUCTIVE_ACTIONS,
    CONF_ENABLE_DEVICE_CONTROLS,
    CONF_INFO_INTERVAL,
    CONF_SPECTRUM_SAVE_ENABLED,
    CONF_SPECTRUM_SAVE_INTERVAL,
    CONF_STATUS_INTERVAL,
    CONF_TIME_SYNC_ENABLED,
    CONF_TIME_SYNC_INTERVAL,
    DEFAULT_CREATE_RAW_ENTITIES,
    DEFAULT_ENABLE_DESTRUCTIVE_ACTIONS,
    DEFAULT_ENABLE_DEVICE_CONTROLS,
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

try:
    from homeassistant.components import bluetooth
except ImportError:  # pragma: no cover - protocol-only test environments
    bluetooth = None


class KC761ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle initial setup and endpoint reconfiguration."""

    VERSION = 6

    def __init__(self) -> None:
        """Initialize the flow's transport selection state."""
        self._selected_transport: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=_transport_schema())
        # Keep direct programmatic submissions from older flows working.  The
        # UI never presents these endpoint fields on the transport step.
        if _has_endpoint_input(user_input):
            return await self._async_validate_and_create(user_input)
        transport = _normalize_transport(user_input.get(CONF_TRANSPORT))
        if transport is None:
            return self.async_show_form(
                step_id="user",
                data_schema=_transport_schema(),
                errors={"base": "invalid_transport"},
            )
        self._selected_transport = transport
        if transport == TRANSPORT_BLE:
            return await self.async_step_ble()
        return await self.async_step_tcp()

    async def async_step_tcp(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Collect the TCP-only endpoint fields."""
        if user_input is None:
            return self.async_show_form(
                step_id="tcp",
                data_schema=_tcp_schema(),
            )
        return await self._async_validate_and_create(
            {CONF_TRANSPORT: TRANSPORT_TCP, **user_input}
        )

    async def async_step_ble(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Collect the BLE-only endpoint fields from discovered devices."""
        if user_input is None:
            options = await self._async_ble_address_options()
            return self.async_show_form(
                step_id="ble",
                data_schema=_ble_schema(options=options),
            )
        return await self._async_validate_and_create(
            {CONF_TRANSPORT: TRANSPORT_BLE, **user_input}
        )

    async def _async_ble_address_options(self, current: str = "") -> list[dict[str, str]]:
        """Return connectable HA discoveries as selectable BLE addresses.

        The scan is advertisement-only.  No GATT connection is attempted until
        the user submits the endpoint and the existing preflight runs.
        """
        options: dict[str, str] = {}
        current = current.strip()
        if current:
            options[current] = f"Saved device ({current})"
        try:
            if bluetooth is None:
                raise RuntimeError("Bluetooth API is unavailable")
            if bluetooth.async_scanner_count(self.hass, connectable=True):
                await bluetooth.async_request_active_scan(self.hass, duration=5)
            discoveries = bluetooth.async_discovered_service_info(self.hass, connectable=True)
        except Exception as err:  # Bluetooth may be disabled or not initialized.
            _LOGGER.debug("Unable to enumerate Bluetooth discoveries: %s", type(err).__name__)
            discoveries = ()
        for discovery in discoveries:
            address = str(getattr(discovery, "address", "")).strip()
            if not address:
                continue
            name = str(getattr(discovery, "name", "") or "Bluetooth device").strip()
            options.setdefault(address, f"{name} ({address})")
        if not options:
            # SelectSelector custom_value provides the manual-address fallback
            # when there are no active scanners or advertisements.
            options[""] = "No discovered device — enter address manually"
        return [{"value": address, "label": label} for address, label in options.items()]

    async def _async_validate_and_create(self, user_input: dict[str, Any]) -> ConfigFlowResult:
        try:
            endpoint = _parse_endpoint(user_input)
        except ValueError as err:
            return self.async_show_form(
                step_id=_endpoint_step(user_input),
                data_schema=_endpoint_schema(user_input),
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
            return self.async_show_form(
                step_id=_endpoint_step(user_input),
                data_schema=_endpoint_schema(user_input),
                errors=errors,
            )
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
            self._selected_transport = _normalize_transport(entry.data.get(CONF_TRANSPORT)) or TRANSPORT_TCP
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=_transport_schema(self._selected_transport),
            )
        # Backward-compatible direct submission for callers that bypass the
        # transport selection step; the rendered flow always uses two steps.
        if _has_endpoint_input(user_input):
            return await self._async_reconfigure_endpoint(entry, user_input)
        transport = _normalize_transport(user_input.get(CONF_TRANSPORT))
        if transport is None:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=_transport_schema(self._selected_transport),
                errors={"base": "invalid_transport"},
            )
        self._selected_transport = transport
        if transport == TRANSPORT_BLE:
            return await self.async_step_reconfigure_ble()
        return await self.async_step_reconfigure_tcp()

    async def async_step_reconfigure_tcp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect TCP-only fields while reconfiguring an entry."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            return self.async_abort(reason="unknown")
        if user_input is None:
            return self.async_show_form(
                step_id="reconfigure_tcp",
                data_schema=_tcp_schema(entry.data),
            )
        return await self._async_reconfigure_endpoint(
            entry, {CONF_TRANSPORT: TRANSPORT_TCP, **user_input}
        )

    async def async_step_reconfigure_ble(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect BLE-only fields while reconfiguring an entry."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            return self.async_abort(reason="unknown")
        if user_input is None:
            options = await self._async_ble_address_options(
                str(entry.data.get(CONF_BLE_ADDRESS, ""))
            )
            return self.async_show_form(
                step_id="reconfigure_ble",
                data_schema=_ble_schema(entry.data, options=options),
            )
        return await self._async_reconfigure_endpoint(
            entry, {CONF_TRANSPORT: TRANSPORT_BLE, **user_input}
        )

    async def _async_reconfigure_endpoint(
        self, entry: config_entries.ConfigEntry, user_input: dict[str, Any]
    ) -> ConfigFlowResult:
        """Validate and persist the endpoint selected by a two-step flow."""
        defaults = entry.data
        if user_input.get(CONF_TRANSPORT) == TRANSPORT_BLE and CONF_BLE_SERVICE_UUID not in user_input:
            # An explicit BLE reconfigure without an override uses the
            # verified device service instead of inheriting a stale override.
            defaults = {**defaults, CONF_BLE_SERVICE_UUID: BLE_VERIFIED_SERVICE_UUID}
        try:
            endpoint = _parse_endpoint(user_input, defaults=defaults)
        except ValueError as err:
            return self.async_show_form(
                step_id=_reconfigure_endpoint_step(user_input),
                data_schema=_reconfigure_endpoint_schema(user_input),
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
                step_id=_reconfigure_endpoint_step(user_input),
                data_schema=_reconfigure_endpoint_schema(user_input),
                errors={"base": "device_mismatch"},
            )
        except Exception as err:
            _LOGGER.warning("KC761 reconfigure preflight failed: %s", type(err).__name__)
            return self.async_show_form(
                step_id=_reconfigure_endpoint_step(user_input),
                data_schema=_reconfigure_endpoint_schema(user_input),
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
            normalized = dict(user_input)
            if not normalized.get(CONF_ENABLE_DEVICE_CONTROLS, False):
                normalized[CONF_ENABLE_DESTRUCTIVE_ACTIONS] = False
                normalized[CONF_TIME_SYNC_ENABLED] = False
            return self.async_create_entry(title="", data=normalized)
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
                    CONF_ENABLE_DEVICE_CONTROLS,
                    default=options.get(
                        CONF_ENABLE_DEVICE_CONTROLS,
                        DEFAULT_ENABLE_DEVICE_CONTROLS,
                    ),
                ): bool,
                vol.Required(
                    CONF_ENABLE_DESTRUCTIVE_ACTIONS,
                    default=options.get(
                        CONF_ENABLE_DESTRUCTIVE_ACTIONS,
                        DEFAULT_ENABLE_DESTRUCTIVE_ACTIONS,
                    ),
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


def _normalize_transport(value: Any) -> str | None:
    """Normalize a transport selector value without accepting unknown values."""
    transport = str(value or "").strip().lower()
    return transport if transport in SUPPORTED_TRANSPORTS else None


def _has_endpoint_input(user_input: dict[str, Any]) -> bool:
    """Return whether input contains an endpoint field from a previous flow."""
    return any(
        key in user_input
        for key in (CONF_HOST, CONF_PORT, CONF_BLE_ADDRESS, CONF_BLE_SERVICE_UUID)
    )


def _transport_schema(default: str = TRANSPORT_TCP) -> vol.Schema:
    """Build the first step containing only the TCP/BLE choice."""
    return vol.Schema(
        {
            vol.Required(CONF_TRANSPORT, default=default): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        {"value": TRANSPORT_TCP, "label": "TCP"},
                        {"value": TRANSPORT_BLE, "label": "BLE"},
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        }
    )


def _tcp_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Build a form containing only TCP endpoint fields."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
        }
    )


def _ble_schema(
    defaults: dict[str, Any] | None = None,
    *,
    options: list[dict[str, str]] | None = None,
) -> vol.Schema:
    """Build a form containing only BLE address and service UUID fields."""
    defaults = defaults or {}
    options = options or [{"value": "", "label": "No discovered device — enter address manually"}]
    return vol.Schema(
        {
            vol.Required(
                CONF_BLE_ADDRESS,
                default=defaults.get(CONF_BLE_ADDRESS, ""),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=options,
                    custom_value=True,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_BLE_SERVICE_UUID,
                default=defaults.get(CONF_BLE_SERVICE_UUID, BLE_VERIFIED_SERVICE_UUID),
            ): str,
        }
    )


def _endpoint_step(user_input: dict[str, Any]) -> str:
    """Return the endpoint step matching an input payload."""
    return "ble" if _normalize_transport(user_input.get(CONF_TRANSPORT)) == TRANSPORT_BLE else "tcp"


def _endpoint_schema(user_input: dict[str, Any]) -> vol.Schema:
    """Return a transport-specific schema for validation errors."""
    if _endpoint_step(user_input) == "ble":
        return _ble_schema(user_input)
    return _tcp_schema(user_input)


def _reconfigure_endpoint_step(user_input: dict[str, Any]) -> str:
    """Return the transport-specific reconfigure endpoint step."""
    if _normalize_transport(user_input.get(CONF_TRANSPORT)) == TRANSPORT_BLE:
        return "reconfigure_ble"
    return "reconfigure_tcp"


def _reconfigure_endpoint_schema(user_input: dict[str, Any]) -> vol.Schema:
    """Return a transport-specific reconfigure schema for validation errors."""
    if _reconfigure_endpoint_step(user_input) == "reconfigure_ble":
        return _ble_schema(user_input)
    return _tcp_schema(user_input)


def _connection_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Compatibility wrapper returning a transport-specific schema."""
    defaults = defaults or {}
    transport = _normalize_transport(defaults.get(CONF_TRANSPORT))
    if transport == TRANSPORT_BLE:
        return _ble_schema(defaults)
    if transport == TRANSPORT_TCP or _has_endpoint_input(defaults):
        return _tcp_schema(defaults)
    return _transport_schema()
