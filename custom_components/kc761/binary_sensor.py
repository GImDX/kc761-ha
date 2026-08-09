"""Read-only KC761 binary sensors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import KC761Coordinator


@dataclass(frozen=True, kw_only=True)
class KC761BinaryDescription(BinarySensorEntityDescription):
    value_key: str


DESCRIPTIONS = (
    KC761BinaryDescription(
        key="auto_upload_enabled",
        translation_key="auto_upload_enabled",
        value_key="auto_upload_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    KC761BinaryDescription(
        key="gamma_spectrum_accumulating",
        translation_key="gamma_spectrum_accumulating",
        value_key="spectrum_gamma",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    KC761BinaryDescription(
        key="neutron_spectrum_accumulating",
        translation_key="neutron_spectrum_accumulating",
        value_key="spectrum_neutron",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    KC761BinaryDescription(
        key="pin_spectrum_accumulating",
        translation_key="pin_spectrum_accumulating",
        value_key="spectrum_pin",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: Any,
) -> None:
    del hass
    coordinator: KC761Coordinator = entry.runtime_data
    async_add_entities([KC761BinarySensor(coordinator, description) for description in DESCRIPTIONS])


class KC761BinarySensor(CoordinatorEntity[KC761Coordinator], BinarySensorEntity):
    """A diagnostic state derived from the coordinator status snapshot."""

    entity_description: KC761BinaryDescription
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: KC761Coordinator, description: KC761BinaryDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        info = coordinator.device_info
        assert info is not None
        self._attr_unique_id = f"{info.device_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, info.device_id)},
            "name": f"{info.model_name} {info.device_id[-4:]}",
            "model": info.model_name,
            "hw_version": info.hw_version_text,
            "sw_version": info.fw_version_text,
        }

    @property
    def is_on(self) -> bool | None:
        snapshot = self.coordinator.snapshot
        if snapshot is None:
            return None
        if self.entity_description.value_key == "auto_upload_status":
            return bool(snapshot.status.auto_upload_status)
        index = {"spectrum_gamma": 0, "spectrum_neutron": 1, "spectrum_pin": 2}[self.entity_description.value_key]
        return snapshot.status.spectrum_accumulating[index]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"hardware_verified": self.coordinator.hardware_verified}

