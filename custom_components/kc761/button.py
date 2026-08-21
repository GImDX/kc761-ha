"""KC761 manual actions, including explicit per-channel clear buttons."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CHANNEL_KEYS
from .coordinator import KC761Coordinator
from .entity import kc761_device_info


@dataclass(frozen=True, kw_only=True)
class KC761ButtonDescription(ButtonEntityDescription):
    action: str
    source: int | None = None


BASE_DESCRIPTIONS = (
    KC761ButtonDescription(
        key="sync_device_time",
        translation_key="sync_device_time",
        action="sync_time",
    ),
    KC761ButtonDescription(
        key="refresh_spectrum",
        translation_key="refresh_spectrum",
        action="capture_spectrum",
    ),
    KC761ButtonDescription(
        key="save_spectrum",
        translation_key="save_spectrum",
        action="save_spectrum",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: Any,
) -> None:
    del hass
    coordinator: KC761Coordinator = entry.runtime_data
    info = coordinator.device_info
    if info is None:
        return
    descriptions = list(BASE_DESCRIPTIONS)
    for source, sensor_type in enumerate(info.sensor_types):
        if not sensor_type:
            continue
        channel = CHANNEL_KEYS[source]
        for action in ("spectrum", "dose_curve", "cumulative_dose"):
            descriptions.append(
                KC761ButtonDescription(
                    key=f"clear_{channel}_{action}",
                    translation_key=f"clear_{channel}_{action}",
                    action=f"clear_{action}",
                    source=source,
                )
            )
    async_add_entities([KC761Button(coordinator, description) for description in descriptions])


class KC761Button(CoordinatorEntity[KC761Coordinator], ButtonEntity):
    """One explicit user-triggered device action."""

    _attr_has_entity_name = True
    _unrecorded_attributes = frozenset({"device_id"})
    entity_description: KC761ButtonDescription

    def __init__(self, coordinator: KC761Coordinator, description: KC761ButtonDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        info = coordinator.device_info
        assert info is not None
        self._attr_unique_id = f"{info.device_id}_{description.key}"
        self._attr_device_info = kc761_device_info(info)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        info = self.coordinator.device_info
        return {
            "device_model": None if info is None else info.model_name,
            "device_id": None if info is None else info.device_id,
            "target_channel": (
                None
                if self.entity_description.source is None
                else CHANNEL_KEYS[self.entity_description.source]
            ),
        }

    async def async_press(self) -> None:
        description = self.entity_description
        if description.action == "sync_time":
            await self.coordinator.async_sync_device_time()
        elif description.action == "capture_spectrum":
            await self.coordinator.async_capture_spectrum()
        elif description.action == "save_spectrum":
            await self.coordinator.async_save_spectrum()
        else:
            assert description.source is not None
            await self.coordinator.async_clear_statistics(
                description.source,
                description.action.removeprefix("clear_"),
            )
