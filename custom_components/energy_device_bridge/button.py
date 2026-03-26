"""Buttons for Energy Device Bridge maintenance actions."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EnergyDeviceBridgeConfigEntry
from .models import ConsumerConfig

_ADOPT_BASELINE_DESCRIPTION = ButtonEntityDescription(
    key="adopt_current_source_as_baseline",
    translation_key="adopt_current_source_as_baseline",
)

_RESET_TRACKER_DESCRIPTION = ButtonEntityDescription(
    key="reset_tracker",
    translation_key="reset_tracker",
)


class EnergyDeviceBridgeButtonBase(ButtonEntity):
    """Base class for bridge maintenance buttons."""

    _attr_has_entity_name = True

    def __init__(self, entry: EnergyDeviceBridgeConfigEntry) -> None:
        self._entry = entry
        self._consumer: ConsumerConfig = entry.runtime_data.consumer
        self._attr_device_info = entry.runtime_data.device_info

    @property
    def _energy_sensor(self):
        """Return active energy sensor or raise a user-facing error."""
        energy_sensor = self._entry.runtime_data.energy_sensor
        if energy_sensor is None:
            raise HomeAssistantError("Bridge energy tracker is not available yet")
        return energy_sensor


class EnergyDeviceBridgeAdoptBaselineButton(EnergyDeviceBridgeButtonBase):
    """Stateless action to adopt current source value as baseline."""

    entity_description = _ADOPT_BASELINE_DESCRIPTION

    def __init__(self, entry: EnergyDeviceBridgeConfigEntry) -> None:
        super().__init__(entry)
        self._attr_unique_id = f"{self._consumer.consumer_uuid}_adopt_current_source_as_baseline"

    async def async_press(self) -> None:
        """Handle button press."""
        await self._energy_sensor.async_adopt_current_source_as_baseline()


class EnergyDeviceBridgeResetTrackerButton(EnergyDeviceBridgeButtonBase):
    """Stateless action to reset tracker metadata and virtual total."""

    entity_description = _RESET_TRACKER_DESCRIPTION

    def __init__(self, entry: EnergyDeviceBridgeConfigEntry) -> None:
        super().__init__(entry)
        self._attr_unique_id = f"{self._consumer.consumer_uuid}_reset_tracker"

    async def async_press(self) -> None:
        """Handle button press."""
        await self._energy_sensor.async_reset_tracker()


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EnergyDeviceBridgeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up maintenance buttons for a config entry."""
    async_add_entities(
        [
            EnergyDeviceBridgeAdoptBaselineButton(entry),
            EnergyDeviceBridgeResetTrackerButton(entry),
        ]
    )
