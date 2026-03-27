"""Buttons for Energy Device Bridge maintenance actions."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EnergyDeviceBridgeConfigEntry, async_start_history_import
from .const import DOMAIN
from .models import ConsumerConfig

_ADOPT_BASELINE_DESCRIPTION = ButtonEntityDescription(
    key="adopt_current_source_as_baseline",
    translation_key="adopt_current_source_as_baseline",
)

_RESET_TRACKER_DESCRIPTION = ButtonEntityDescription(
    key="reset_tracker",
    translation_key="reset_tracker",
)

_IMPORT_SOURCE_HISTORY_DESCRIPTION = ButtonEntityDescription(
    key="import_source_history",
    translation_key="import_source_history",
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
            raise ServiceValidationError(
                "Energy Device Bridge action error",
                translation_domain=DOMAIN,
                translation_key="service_energy_sensor_unavailable",
            )
        return energy_sensor


class EnergyDeviceBridgeAdoptBaselineButton(EnergyDeviceBridgeButtonBase):
    """Stateless action to adopt current source value as baseline."""

    entity_description = _ADOPT_BASELINE_DESCRIPTION

    def __init__(self, entry: EnergyDeviceBridgeConfigEntry) -> None:
        super().__init__(entry)
        self._attr_unique_id = (
            f"{self._consumer.consumer_uuid}_adopt_current_source_as_baseline"
        )

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


class EnergyDeviceBridgeImportSourceHistoryButton(EnergyDeviceBridgeButtonBase):
    """Trigger one-time history replay/import for this bridge entry."""

    entity_description = _IMPORT_SOURCE_HISTORY_DESCRIPTION

    def __init__(self, entry: EnergyDeviceBridgeConfigEntry) -> None:
        super().__init__(entry)
        self._attr_unique_id = f"{self._consumer.consumer_uuid}_import_source_history"

    async def async_press(self) -> None:
        """Handle button press."""
        await async_start_history_import(
            self.hass,
            entry=self._entry,
            trigger="button",
            reinitialize_before_import=True,
        )


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
            EnergyDeviceBridgeImportSourceHistoryButton(entry),
        ]
    )
