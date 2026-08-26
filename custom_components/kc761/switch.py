"""KC761 configuration switches backed by masked 0x62 writes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CHANNEL_KEYS
from .coordinator import KC761Coordinator
from .entity import kc761_device_info


@dataclass(frozen=True, kw_only=True)
class KC761SwitchDescription(SwitchEntityDescription):
    kind: str
    bit: int | None = None
    source: int | None = None


DESCRIPTIONS = (
    KC761SwitchDescription(key="auto_upload", translation_key="auto_upload", kind="auto_upload"),
    KC761SwitchDescription(key="key_tone", translation_key="key_tone", kind="volume", bit=3),
    KC761SwitchDescription(key="count_tone", translation_key="count_tone", kind="volume", bit=2),
    KC761SwitchDescription(
        key="dense_count_effect", translation_key="dense_count_effect", kind="volume", bit=4
    ),
    KC761SwitchDescription(key="count_flash", translation_key="count_flash", kind="led", bit=0),
    KC761SwitchDescription(key="backlight", translation_key="backlight", kind="led", bit=1),
    *tuple(
        KC761SwitchDescription(
            key=f"{channel}_spectrum_accumulating",
            translation_key=f"{channel}_spectrum_accumulating",
            kind="spectrum",
            source=source,
        )
        for source, channel in enumerate(CHANNEL_KEYS)
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: Any,
) -> None:
    del hass
    coordinator: KC761Coordinator = entry.runtime_data
    if not coordinator.device_controls_enabled:
        return
    info = coordinator.device_info
    if info is None:
        return
    descriptions = [
        description
        for description in DESCRIPTIONS
        if description.source is None or bool(info.sensor_types[description.source])
    ]
    async_add_entities([KC761Switch(coordinator, description) for description in descriptions])


class KC761Switch(CoordinatorEntity[KC761Coordinator], SwitchEntity):
    """One current-state bit with a read-after-write update."""

    _attr_has_entity_name = True
    entity_description: KC761SwitchDescription

    def __init__(self, coordinator: KC761Coordinator, description: KC761SwitchDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        info = coordinator.device_info
        assert info is not None
        self._attr_unique_id = f"{info.device_id}_{description.key}"
        self._attr_device_info = kc761_device_info(info)

    @property
    def is_on(self) -> bool | None:
        snapshot = self.coordinator.snapshot
        if snapshot is None:
            return None
        description = self.entity_description
        if description.kind == "auto_upload":
            value = snapshot.status.auto_upload_status
            return None if value is None else bool(value)
        if description.kind == "spectrum":
            assert description.source is not None
            return snapshot.status.spectrum_accumulating[description.source]
        assert description.bit is not None
        value = (
            snapshot.status.volume_status
            if description.kind == "volume"
            else snapshot.status.led_screen_status
        )
        return bool(value & (1 << description.bit))

    async def async_turn_on(self, **kwargs: Any) -> None:
        del kwargs
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        del kwargs
        await self._async_set(False)

    async def _async_set(self, enabled: bool) -> None:
        description = self.entity_description
        if description.kind == "auto_upload":
            await self.coordinator.async_set_auto_upload(enabled)
        elif description.kind == "spectrum":
            assert description.source is not None
            await self.coordinator.async_set_spectrum_accumulating(description.source, enabled)
        elif description.kind == "volume":
            assert description.bit is not None
            await self.coordinator.async_set_volume_flag(description.bit, enabled)
        else:
            assert description.bit is not None
            await self.coordinator.async_set_led_flag(description.bit, enabled)
