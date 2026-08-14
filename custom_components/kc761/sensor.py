"""Read-only KC761 sensors backed by one coordinator snapshot."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import KC761Coordinator
from .protocol.codec import CODEC_PROFILE
from .protocol.models import ChannelReading


CHANNEL_NAMES = ("gamma", "neutron", "pin")


@dataclass(frozen=True, kw_only=True)
class KC761SensorDescription(SensorEntityDescription):
    value_key: str
    channel_index: int | None = None
    raw_reading: bool = False


DEVICE_DESCRIPTIONS = (
    KC761SensorDescription(
        key="battery_percentage",
        translation_key="battery_percentage",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_key="battery_percent",
    ),
    KC761SensorDescription(
        key="air_pressure",
        translation_key="air_pressure",
        native_unit_of_measurement=UnitOfPressure.HPA,
        device_class=SensorDeviceClass.ATMOSPHERIC_PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_key="air_pressure_hpa",
    ),
    KC761SensorDescription(
        key="device_temperature",
        translation_key="device_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_key="device_temperature_c",
    ),
    KC761SensorDescription(
        key="device_time",
        translation_key="device_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_key="device_time",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    KC761SensorDescription(
        key="clock_offset",
        translation_key="clock_offset",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_key="clock_offset",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    KC761SensorDescription(
        key="coprocessor_firmware",
        translation_key="coprocessor_firmware",
        value_key="coprocessor_firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)


def _channel_descriptions() -> tuple[KC761SensorDescription, ...]:
    result: list[KC761SensorDescription] = []
    for index, channel in enumerate(CHANNEL_NAMES):
        result.extend(
            (
                KC761SensorDescription(
                    key=f"{channel}_avg_cps",
                    translation_key=f"{channel}_avg_cps",
                    native_unit_of_measurement="cps",
                    state_class=SensorStateClass.MEASUREMENT,
                    value_key="avg_cps",
                    channel_index=index,
                    icon="mdi:radioactive",
                ),
                KC761SensorDescription(
                    key=f"{channel}_avg_dose_rate",
                    translation_key=f"{channel}_avg_dose_rate",
                    native_unit_of_measurement="mGy/h",
                    state_class=SensorStateClass.MEASUREMENT,
                    value_key="avg_dose_rate",
                    channel_index=index,
                    icon="mdi:radioactive",
                ),
                KC761SensorDescription(
                    key=f"{channel}_avg_dose_equivalent_rate",
                    translation_key=f"{channel}_avg_dose_equivalent_rate",
                    native_unit_of_measurement="mSv/h",
                    state_class=SensorStateClass.MEASUREMENT,
                    value_key="avg_dose_equivalent_rate",
                    channel_index=index,
                    icon="mdi:radioactive",
                ),
                KC761SensorDescription(
                    key=f"{channel}_raw_cps",
                    translation_key=f"{channel}_raw_cps",
                    native_unit_of_measurement="cps",
                    state_class=SensorStateClass.MEASUREMENT,
                    value_key="raw_cps",
                    channel_index=index,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    entity_registry_enabled_default=False,
                    raw_reading=True,
                    icon="mdi:radioactive",
                ),
                KC761SensorDescription(
                    key=f"{channel}_raw_dose_rate",
                    translation_key=f"{channel}_raw_dose_rate",
                    native_unit_of_measurement="mGy/h",
                    state_class=SensorStateClass.MEASUREMENT,
                    value_key="raw_dose_rate",
                    channel_index=index,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    entity_registry_enabled_default=False,
                    raw_reading=True,
                    icon="mdi:radioactive",
                ),
                KC761SensorDescription(
                    key=f"{channel}_raw_dose_equivalent_rate",
                    translation_key=f"{channel}_raw_dose_equivalent_rate",
                    native_unit_of_measurement="mSv/h",
                    state_class=SensorStateClass.MEASUREMENT,
                    value_key="raw_dose_equivalent_rate",
                    channel_index=index,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    entity_registry_enabled_default=False,
                    raw_reading=True,
                    icon="mdi:radioactive",
                ),
            )
        )
    return tuple(result)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: Any,
) -> None:
    """Set up candidate read-only sensor entities."""
    del hass
    coordinator: KC761Coordinator = entry.runtime_data
    info = coordinator.device_info
    if info is None:
        return
    descriptions = list(DEVICE_DESCRIPTIONS)
    for index, sensor_type in enumerate(info.sensor_types):
        if sensor_type:
            for description in _channel_descriptions():
                if description.channel_index != index:
                    continue
                if description.raw_reading:
                    if not coordinator.create_raw_entities:
                        continue
                    description = replace(description, entity_registry_enabled_default=True)
                descriptions.append(description)
    async_add_entities([KC761Sensor(coordinator, description) for description in descriptions])


class KC761Sensor(CoordinatorEntity[KC761Coordinator], SensorEntity):
    """One scalar value from the immutable coordinator snapshot."""

    entity_description: KC761SensorDescription
    _attr_has_entity_name = True

    def __init__(self, coordinator: KC761Coordinator, description: KC761SensorDescription) -> None:
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
    def native_value(self) -> Any:
        snapshot = self.coordinator.snapshot
        if snapshot is None:
            return None
        if self.entity_description.channel_index is None:
            if self.entity_description.value_key == "clock_offset":
                if snapshot.status.device_time is None:
                    return None
                return (snapshot.status.device_time - snapshot.received_at).total_seconds()
            if self.entity_description.value_key == "coprocessor_firmware":
                return snapshot.device_info.co_fw_version_text
            return getattr(snapshot.status, self.entity_description.value_key)
        channel: ChannelReading = snapshot.status.channels[self.entity_description.channel_index]
        return getattr(channel, self.entity_description.value_key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "hardware_verified": self.coordinator.hardware_verified,
            "codec_profile": CODEC_PROFILE,
        }
