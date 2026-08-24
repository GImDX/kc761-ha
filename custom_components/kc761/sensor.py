"""Read-only KC761 sensors backed by one coordinator snapshot."""

from __future__ import annotations

from dataclasses import dataclass
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
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import KC761Coordinator
from .entity import kc761_device_info
from .protocol.codec import CODEC_PROFILE
from .protocol.models import ChannelReading
from .protocol.codec import sensor_name


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
    ),
    KC761SensorDescription(
        key="clock_offset",
        translation_key="clock_offset",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_key="clock_offset",
    ),
    KC761SensorDescription(
        key="coprocessor_firmware",
        translation_key="coprocessor_firmware",
        value_key="coprocessor_firmware",
    ),
    KC761SensorDescription(
        key="device_id",
        translation_key="device_id",
        value_key="device_id",
    ),
    KC761SensorDescription(
        key="gamma_sensor_model",
        translation_key="gamma_sensor_model",
        value_key="sensor_type_0",
    ),
    KC761SensorDescription(
        key="neutron_sensor_model",
        translation_key="neutron_sensor_model",
        value_key="sensor_type_1",
    ),
    KC761SensorDescription(
        key="pin_sensor_model",
        translation_key="pin_sensor_model",
        value_key="sensor_type_2",
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
                descriptions.append(description)
    entities: list[SensorEntity] = [KC761Sensor(coordinator, description) for description in descriptions]
    entities.append(KC761SpectrumSensor(coordinator))
    async_add_entities(entities)


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
        self._attr_device_info = kc761_device_info(info)

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
            if self.entity_description.value_key == "device_id":
                return snapshot.device_info.device_id
            if self.entity_description.value_key.startswith("sensor_type_"):
                index = int(self.entity_description.value_key[-1])
                return sensor_name(snapshot.device_info.sensor_types[index])
            return getattr(snapshot.status, self.entity_description.value_key)
        channel: ChannelReading = snapshot.status.channels[self.entity_description.channel_index]
        return getattr(channel, self.entity_description.value_key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "hardware_verified": self.coordinator.hardware_verified,
            "codec_profile": CODEC_PROFILE,
        }


class KC761SpectrumSensor(CoordinatorEntity[KC761Coordinator], SensorEntity):
    """One structured latest-spectrum entity; the large point array is not recorded."""

    _attr_has_entity_name = True
    _attr_translation_key = "spectrum"
    _attr_icon = "mdi:chart-bell-curve-cumulative"
    _attr_native_unit_of_measurement = "counts"
    _unrecorded_attributes = frozenset({"points"})

    def __init__(self, coordinator: KC761Coordinator) -> None:
        super().__init__(coordinator)
        info = coordinator.device_info
        assert info is not None
        self._attr_unique_id = f"{info.device_id}_spectrum"
        self._attr_device_info = kc761_device_info(info)
        self._spectrum_update_seen = False
        self._last_spectrum = None
        self._last_available = None
        self._last_hardware_verified = None
        self._last_device_id = None

    def _handle_coordinator_update(self) -> None:
        """Avoid a large state serialization when the spectrum did not change."""
        spectrum = self.coordinator.latest_spectrum
        available = self.available
        info = self.coordinator.device_info
        hardware_verified = self.coordinator.hardware_verified
        device_id = None if info is None else info.device_id
        if (
            self._spectrum_update_seen
            and spectrum is self._last_spectrum
            and available == self._last_available
            and hardware_verified == self._last_hardware_verified
            and device_id == self._last_device_id
        ):
            return
        self._spectrum_update_seen = True
        self._last_spectrum = spectrum
        self._last_available = available
        self._last_hardware_verified = hardware_verified
        self._last_device_id = device_id
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> int | None:
        spectrum = self.coordinator.latest_spectrum
        return None if spectrum is None else spectrum.total_counts

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        spectrum = self.coordinator.latest_spectrum
        if spectrum is None:
            return {
                "config_entry_id": self.coordinator.entry.entry_id,
                "hardware_verified": self.coordinator.hardware_verified,
                "codec_profile": CODEC_PROFILE,
                "points": [],
            }
        return {
            **spectrum.entity_attributes(),
            "config_entry_id": self.coordinator.entry.entry_id,
            "hardware_verified": self.coordinator.hardware_verified,
            "codec_profile": CODEC_PROFILE,
        }
