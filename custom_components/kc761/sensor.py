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
from .protocol.models import CalibrationData, ChannelReading
from .protocol.codec import sensor_name


CHANNEL_NAMES = ("gamma", "neutron", "pin")


@dataclass(frozen=True, kw_only=True)
class KC761SensorDescription(SensorEntityDescription):
    value_key: str
    channel_index: int | None = None
    raw_reading: bool = False
    cumulative_key: str | None = None
    calibration_key: str | None = None


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
    KC761SensorDescription(
        key="rad0_trigger_offset",
        translation_key="rad0_trigger_offset",
        value_key="",
        calibration_key="rad0_trigger_offset",
    ),
    KC761SensorDescription(
        key="rad2_trigger_offset",
        translation_key="rad2_trigger_offset",
        value_key="",
        calibration_key="rad2_trigger_offset",
    ),
    KC761SensorDescription(
        key="rad0_dose_zoom",
        translation_key="rad0_dose_zoom",
        value_key="",
        calibration_key="rad0_dose_zoom",
    ),
    KC761SensorDescription(
        key="rad1_dose_zoom",
        translation_key="rad1_dose_zoom",
        value_key="",
        calibration_key="rad1_dose_zoom",
    ),
    KC761SensorDescription(
        key="rad2_dose_zoom",
        translation_key="rad2_dose_zoom",
        value_key="",
        calibration_key="rad2_dose_zoom",
    ),
    KC761SensorDescription(
        key="neu_window_center",
        translation_key="neu_window_center",
        native_unit_of_measurement="ch",
        value_key="",
        calibration_key="neu_window_center",
    ),
    KC761SensorDescription(
        key="altitude_m_offset",
        translation_key="altitude_m_offset",
        native_unit_of_measurement="m",
        value_key="",
        calibration_key="altitude_m_offset",
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
                KC761SensorDescription(
                    key=f"{channel}_spectrum_runtime",
                    translation_key=f"{channel}_spectrum_runtime",
                    native_unit_of_measurement=UnitOfTime.SECONDS,
                    device_class=SensorDeviceClass.DURATION,
                    value_key="spectrum_runtime",
                    channel_index=index,
                    cumulative_key="spectrum_runtime",
                    icon="mdi:timer-outline",
                ),
                KC761SensorDescription(
                    key=f"{channel}_dose_runtime",
                    translation_key=f"{channel}_dose_runtime",
                    native_unit_of_measurement=UnitOfTime.SECONDS,
                    device_class=SensorDeviceClass.DURATION,
                    value_key="dose_runtime",
                    channel_index=index,
                    cumulative_key="dose_runtime",
                    icon="mdi:timer-outline",
                ),
                KC761SensorDescription(
                    key=f"{channel}_cumulative_dose",
                    translation_key=f"{channel}_cumulative_dose",
                    native_unit_of_measurement="mGy",
                    value_key="cumulative_dose",
                    channel_index=index,
                    cumulative_key="cumulative_dose",
                    icon="mdi:radioactive",
                ),
                KC761SensorDescription(
                    key=f"{channel}_cumulative_dose_equivalent",
                    translation_key=f"{channel}_cumulative_dose_equivalent",
                    native_unit_of_measurement="mSv",
                    value_key="cumulative_dose_equivalent",
                    channel_index=index,
                    cumulative_key="cumulative_dose_equivalent",
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
        if self.entity_description.calibration_key is not None:
            calibration = self.coordinator.calibration
            if not isinstance(calibration, CalibrationData):
                return None
            return getattr(calibration, self.entity_description.calibration_key)
        if self.entity_description.cumulative_key is not None:
            assert self.entity_description.channel_index is not None
            cumulative = snapshot.device_info.cumulative[self.entity_description.channel_index]
            return getattr(cumulative, self.entity_description.cumulative_key)
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
