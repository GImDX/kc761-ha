"""KC761x Home Assistant integration."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

from .const import (
    CONF_CREATE_RAW_ENTITIES,
    DEFAULT_CREATE_RAW_ENTITIES,
    DOMAIN,
    PLATFORMS,
    VERSION,
)

try:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP
    from homeassistant.core import HomeAssistant
    from homeassistant.exceptions import ConfigEntryNotReady
    from homeassistant.helpers import config_validation as cv
    from homeassistant.helpers import entity_registry as er
except ImportError:  # pragma: no cover - permits protocol-only offline checks
    ConfigEntry = Any  # type: ignore[misc,assignment]
    EVENT_HOMEASSISTANT_STOP = "homeassistant_stop"
    HomeAssistant = Any  # type: ignore[misc,assignment]
    ConfigEntryNotReady = RuntimeError
    cv = None  # type: ignore[assignment]
    er = None  # type: ignore[assignment]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN) if cv is not None else None
CONFIG_ENTRY_VERSION = 7

_REMOVED_BINARY_SENSOR_KEYS = (
    "auto_upload_enabled",
    "gamma_spectrum_accumulating",
    "neutron_spectrum_accumulating",
    "pin_spectrum_accumulating",
)

_VISIBLE_SENSOR_KEYS = (
    "device_time",
    "clock_offset",
    "coprocessor_firmware",
    *(
        f"{channel}_{measurement}"
        for channel in ("gamma", "neutron", "pin")
        for measurement in (
            "avg_cps",
            "avg_dose_rate",
            "avg_dose_equivalent_rate",
        )
    ),
)

_RAW_SENSOR_KEYS = tuple(
    f"{channel}_{measurement}"
    for channel in ("gamma", "neutron", "pin")
    for measurement in ("raw_cps", "raw_dose_rate", "raw_dose_equivalent_rate")
)


def _frontend_build(path: Path) -> str:
    """Fingerprint the exact served bytes outside the event loop."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the KC761 domain (configuration is entry based)."""
    del config
    hass.data.setdefault(DOMAIN, {})
    marker = f"{DOMAIN}_api_registered"
    if not hass.data.get(marker):
        from homeassistant.components import frontend as ha_frontend
        from homeassistant.components.http import StaticPathConfig

        from .websocket_api import async_register as async_register_websocket_api

        frontend_dir = Path(__file__).parent / "frontend"
        build = await hass.async_add_executor_job(_frontend_build, frontend_dir / "kc761-spectrum-card.js")
        async_register_websocket_api(hass)
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    "/kc761/kc761-spectrum-card.js",
                    str(frontend_dir / "kc761-spectrum-card.js"),
                    True,
                ),
                StaticPathConfig(
                    "/kc761/echarts.esm.min.js",
                    str(frontend_dir / "echarts.esm.min.js"),
                    True,
                )
            ]
        )
        ha_frontend.add_extra_js_url(
            hass,
            f"/kc761/kc761-spectrum-card.js?v={VERSION}&build={build}",
        )
        hass.data[marker] = True
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one KC761 device."""
    from .coordinator import KC761Coordinator

    coordinator = KC761Coordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
        await coordinator.async_start()
    except asyncio.CancelledError:
        await coordinator.async_shutdown()
        raise
    except Exception as err:
        await coordinator.async_shutdown()
        raise ConfigEntryNotReady from err

    entry.runtime_data = coordinator
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    async def _async_stop(_event: Any) -> None:
        await coordinator.async_shutdown()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop))
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except asyncio.CancelledError:
        await coordinator.async_shutdown()
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        raise
    except Exception:
        await coordinator.async_shutdown()
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        raise
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate identity and normalize superseded registry entries."""
    if entry.version > CONFIG_ENTRY_VERSION:
        return False
    if entry.version == CONFIG_ENTRY_VERSION:
        return True
    device_id = str(entry.data.get("device_id", ""))
    if not device_id:
        return False
    if er is not None:
        registry = er.async_get(hass)
        if entry.version < 3:
            for key in _REMOVED_BINARY_SENSOR_KEYS:
                entity_id = registry.async_get_entity_id(
                    "binary_sensor",
                    DOMAIN,
                    f"{device_id}_{key}",
                )
                if entity_id is not None:
                    registry.async_remove(entity_id)
        if entry.version < 5:
            _migrate_sensor_registry(registry, entry, device_id)
    # Entries created before BLE support intentionally keep their original
    # host/port shape.  The coordinator treats a missing transport key as TCP,
    # so this migration changes only the schema version and preserves old data
    # exactly for rollback and downgrade safety.
    hass.config_entries.async_update_entry(
        entry,
        unique_id=device_id,
        version=CONFIG_ENTRY_VERSION,
    )
    return True


def _migrate_sensor_registry(registry: Any, entry: ConfigEntry, device_id: str) -> None:
    """Restore new ordinary sensors without overriding user disable choices."""
    for key in _VISIBLE_SENSOR_KEYS:
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{device_id}_{key}")
        if entity_id is None:
            continue
        registry_entry = registry.async_get(entity_id)
        if registry_entry is None:
            continue
        changes: dict[str, Any] = {"entity_category": None}
        if registry_entry.disabled_by == er.RegistryEntryDisabler.INTEGRATION:
            changes["disabled_by"] = None
        registry.async_update_entity(entity_id, **changes)

    create_raw = bool(
        entry.options.get(CONF_CREATE_RAW_ENTITIES, DEFAULT_CREATE_RAW_ENTITIES)
    )
    for key in _RAW_SENSOR_KEYS:
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{device_id}_{key}")
        if entity_id is None:
            continue
        if not create_raw:
            registry.async_remove(entity_id)
            continue
        registry_entry = registry.async_get(entity_id)
        if registry_entry is None:
            continue
        changes = {"entity_category": None}
        if registry_entry.disabled_by == er.RegistryEntryDisabler.INTEGRATION:
            changes["disabled_by"] = None
        registry.async_update_entity(entity_id, **changes)


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload after options or endpoint changes."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload one KC761 device and close its connection."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unloaded:
        return False
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is not None:
        await coordinator.async_shutdown()
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return True
