"""KC761 selects for sensor, volume, and backlight brightness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CHANNEL_KEYS
from .coordinator import KC761Coordinator
from .entity import kc761_device_info

VOLUME_OPTIONS = ("mute", "low", "high")
BRIGHTNESS_OPTIONS = ("dim", "bright")


@dataclass(frozen=True, kw_only=True)
class KC761SelectDescription(SelectEntityDescription):
    kind: str


DESCRIPTIONS = (
    KC761SelectDescription(key="active_sensor", translation_key="active_sensor", kind="sensor"),
    KC761SelectDescription(key="global_volume", translation_key="global_volume", kind="volume"),
    KC761SelectDescription(
        key="backlight_brightness",
        translation_key="backlight_brightness",
        kind="brightness",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: Any,
) -> None:
    del hass
    coordinator: KC761Coordinator = entry.runtime_data
    if coordinator.device_info is None:
        return
    async_add_entities([KC761Select(coordinator, description) for description in DESCRIPTIONS])


class KC761Select(CoordinatorEntity[KC761Coordinator], SelectEntity):
    """One select that preserves unrelated status bits."""

    _attr_has_entity_name = True
    entity_description: KC761SelectDescription

    def __init__(self, coordinator: KC761Coordinator, description: KC761SelectDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        info = coordinator.device_info
        assert info is not None
        self._attr_unique_id = f"{info.device_id}_{description.key}"
        self._attr_device_info = kc761_device_info(info)

    @property
    def options(self) -> list[str]:
        if self.entity_description.kind == "sensor":
            info = self.coordinator.device_info
            return (
                []
                if info is None
                else [CHANNEL_KEYS[index] for index, value in enumerate(info.sensor_types) if value]
            )
        if self.entity_description.kind == "volume":
            return list(VOLUME_OPTIONS)
        return list(BRIGHTNESS_OPTIONS)

    @property
    def current_option(self) -> str | None:
        snapshot = self.coordinator.snapshot
        if snapshot is None:
            return None
        if self.entity_description.kind == "sensor":
            source = snapshot.status.active_sensor
            return CHANNEL_KEYS[source] if source < len(CHANNEL_KEYS) else None
        if self.entity_description.kind == "volume":
            value = snapshot.status.volume_status & 0x03
            return VOLUME_OPTIONS[value] if value < len(VOLUME_OPTIONS) else None
        return BRIGHTNESS_OPTIONS[bool(snapshot.status.led_screen_status & 0x04)]

    async def async_select_option(self, option: str) -> None:
        if option not in self.options:
            raise ValueError(f"unsupported KC761 option: {option}")
        if self.entity_description.kind == "sensor":
            await self.coordinator.async_set_active_sensor(CHANNEL_KEYS.index(option))
        elif self.entity_description.kind == "volume":
            await self.coordinator.async_set_volume(VOLUME_OPTIONS.index(option))
        else:
            await self.coordinator.async_set_led_flag(2, option == "bright")
