"""Read-only KC761 status flags backed by the coordinator snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CHANNEL_KEYS
from .coordinator import KC761Coordinator
from .entity import kc761_device_info


@dataclass(frozen=True, kw_only=True)
class KC761BinarySensorDescription(BinarySensorEntityDescription):
    kind: str
    bit: int | None = None
    source: int | None = None


DESCRIPTIONS = (
    KC761BinarySensorDescription(
        key="auto_upload_status",
        translation_key="auto_upload_status",
        kind="auto_upload",
    ),
    KC761BinarySensorDescription(
        key="key_tone_status",
        translation_key="key_tone_status",
        kind="volume",
        bit=3,
    ),
    KC761BinarySensorDescription(
        key="count_tone_status",
        translation_key="count_tone_status",
        kind="volume",
        bit=2,
    ),
    KC761BinarySensorDescription(
        key="dense_count_effect_status",
        translation_key="dense_count_effect_status",
        kind="volume",
        bit=4,
    ),
    KC761BinarySensorDescription(
        key="count_flash_status",
        translation_key="count_flash_status",
        kind="led",
        bit=0,
    ),
    KC761BinarySensorDescription(
        key="backlight_status",
        translation_key="backlight_status",
        kind="led",
        bit=1,
    ),
)


def _channel_descriptions(info: Any) -> tuple[KC761BinarySensorDescription, ...]:
    return tuple(
        KC761BinarySensorDescription(
            key=f"{channel}_spectrum_accumulating_status",
            translation_key=f"{channel}_spectrum_accumulating_status",
            kind="spectrum",
            source=source,
        )
        for source, channel in enumerate(CHANNEL_KEYS)
        if bool(info.sensor_types[source])
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: Any,
) -> None:
    """Set up ordinary read-only state entities independently of controls."""
    del hass
    coordinator: KC761Coordinator = entry.runtime_data
    info = coordinator.device_info
    if info is None:
        return
    descriptions = (*DESCRIPTIONS, *_channel_descriptions(info))
    async_add_entities(
        [KC761BinarySensor(coordinator, description) for description in descriptions]
    )


class KC761BinarySensor(CoordinatorEntity[KC761Coordinator], BinarySensorEntity):
    """One status bit exposed without issuing any network request."""

    _attr_has_entity_name = True
    entity_description: KC761BinarySensorDescription

    def __init__(
        self,
        coordinator: KC761Coordinator,
        description: KC761BinarySensorDescription,
    ) -> None:
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
