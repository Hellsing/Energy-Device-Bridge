"""Sensors for Energy Device Bridge."""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import entity_platform
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter, PowerConverter

from . import EnergyDeviceBridgeConfigEntry
from .const import (
    ATTR_CURRENT_NORMALIZED_SOURCE_UNIT,
    ATTR_IGNORED_NEGATIVE_DELTA_COUNT,
    ATTR_LAST_SOURCE_ENERGY_VALUE_KWH,
    ATTR_LAST_SOURCE_ENTITY_ID,
    ATTR_LAST_VALID_SOURCE_SAMPLE_TS,
    ATTR_RESET_DETECTED_COUNT,
    ATTR_VALUE_KWH,
    DOMAIN,
    ISSUE_ENERGY_STATE_CLASS_INVALID,
    ISSUE_ENERGY_UNIT_UNSUPPORTED,
    ISSUE_POWER_UNIT_UNSUPPORTED,
    ISSUE_SOURCE_ENTITY_MISSING,
    SERVICE_ADOPT_CURRENT_SOURCE_AS_BASELINE,
    SERVICE_RESET_TRACKER,
    SERVICE_SET_VIRTUAL_TOTAL,
)
from .models import ConsumerConfig, EnergyTrackerState

_LOGGER = logging.getLogger(__name__)

_POWER_DESCRIPTION = SensorEntityDescription(
    key="power",
    translation_key="power",
    device_class=SensorDeviceClass.POWER,
    native_unit_of_measurement=UnitOfPower.KILO_WATT,
    state_class=SensorStateClass.MEASUREMENT,
)

_ENERGY_DESCRIPTION = SensorEntityDescription(
    key="energy",
    translation_key="energy",
    device_class=SensorDeviceClass.ENERGY,
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    state_class=SensorStateClass.TOTAL_INCREASING,
)


def _parse_numeric(value: StateType) -> float | None:
    """Parse numeric state values and ignore special states."""
    if value is None:
        return None
    if isinstance(value, str) and value.lower() in {"none", STATE_UNKNOWN, STATE_UNAVAILABLE}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _convert_energy_to_kwh(value: float, unit: str | None) -> float | None:
    """Convert source energy values to canonical kWh."""
    if not unit:
        return None
    try:
        return float(EnergyConverter.convert(value, unit, UnitOfEnergy.KILO_WATT_HOUR))
    except (TypeError, ValueError):
        return None


def _is_supported_energy_unit(unit: str | None) -> bool:
    if not unit:
        return False
    if unit not in {UnitOfEnergy.WATT_HOUR, UnitOfEnergy.KILO_WATT_HOUR}:
        return False
    return _convert_energy_to_kwh(1.0, unit) is not None


def _is_supported_power_unit(unit: str | None) -> bool:
    if not unit:
        return False
    if unit not in {UnitOfPower.WATT, UnitOfPower.KILO_WATT}:
        return False
    try:
        PowerConverter.convert(1.0, unit, UnitOfPower.KILO_WATT)
    except (TypeError, ValueError):
        return False
    return True


class EnergyDeviceBridgeSensorBase(SensorEntity):
    """Base class for Energy Device Bridge sensors."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, consumer: ConsumerConfig) -> None:
        self._consumer = consumer

    async def async_adopt_current_source_as_baseline(self) -> None:
        """Default service handler for unsupported entity types."""
        raise HomeAssistantError("Action is only supported by the bridge energy sensor")

    async def async_reset_tracker(self) -> None:
        """Default service handler for unsupported entity types."""
        raise HomeAssistantError("Action is only supported by the bridge energy sensor")

    async def async_set_virtual_total(self, value_kwh: float) -> None:
        """Default service handler for unsupported entity types."""
        _ = value_kwh
        raise HomeAssistantError("Action is only supported by the bridge energy sensor")


class EnergyDeviceBridgePowerSensor(EnergyDeviceBridgeSensorBase):
    """Mirror source power sensor state."""

    entity_description = _POWER_DESCRIPTION

    def __init__(self, entry: EnergyDeviceBridgeConfigEntry) -> None:
        super().__init__(entry.runtime_data.consumer)
        self._entry = entry
        source_entity_id = entry.runtime_data.consumer.source_power_entity_id
        if source_entity_id is None:
            raise ValueError("Power source entity is required for power sensor setup")
        self._source_entity_id = source_entity_id
        self._unsub_source: Callable[[], None] | None = None
        self._attr_unique_id = f"{self._consumer.consumer_uuid}_power"
        self._attr_device_info = entry.runtime_data.device_info
        self._attr_available = False
        self._native_value: float | None = None

    @property
    def native_value(self) -> StateType:
        """Return mirrored source value."""
        return self._native_value

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return canonical power unit."""
        return UnitOfPower.KILO_WATT

    async def async_added_to_hass(self) -> None:
        """Handle entity addition."""
        self._refresh_from_source()
        self._unsub_source = async_track_state_change_event(
            self.hass,
            [self._source_entity_id],
            self._async_handle_source_change,
        )

    async def async_will_remove_from_hass(self) -> None:
        """Handle cleanup on remove."""
        if self._unsub_source is not None:
            self._unsub_source()
            self._unsub_source = None

    @callback
    def _async_handle_source_change(self, _event: Event) -> None:
        self._refresh_from_source()
        self.async_write_ha_state()

    @callback
    def _refresh_from_source(self) -> None:
        source_state = self.hass.states.get(self._source_entity_id)
        if source_state is None or source_state.state == STATE_UNAVAILABLE:
            self._attr_available = False
            self._native_value = None
            self._entry.runtime_data.set_issue(
                self.hass, ISSUE_POWER_UNIT_UNSUPPORTED, is_active=False
            )
            return

        self._attr_available = True
        source_numeric = _parse_numeric(source_state.state)
        if source_numeric is None:
            self._native_value = None
            return
        source_unit = source_state.attributes.get("unit_of_measurement")
        power_unit_supported = _is_supported_power_unit(source_unit)
        self._entry.runtime_data.set_issue(
            self.hass,
            ISSUE_POWER_UNIT_UNSUPPORTED,
            is_active=not power_unit_supported,
            translation_placeholders={"unit": str(source_unit)},
        )
        if not power_unit_supported:
            self._native_value = None
            return
        try:
            self._native_value = float(
                PowerConverter.convert(source_numeric, source_unit, UnitOfPower.KILO_WATT)
            )
        except (TypeError, ValueError):
            self._native_value = None


class EnergyDeviceBridgeEnergySensor(EnergyDeviceBridgeSensorBase, RestoreSensor):
    """Virtual total energy sensor that survives source resets/replacements."""

    entity_description = _ENERGY_DESCRIPTION
    _attr_suggested_display_precision = 3

    def __init__(self, entry: EnergyDeviceBridgeConfigEntry) -> None:
        super().__init__(entry.runtime_data.consumer)
        self._entry = entry
        self._source_entity_id = entry.runtime_data.consumer.source_energy_entity_id
        self._unsub_source: Callable[[], None] | None = None
        self._tracker = EnergyTrackerState()
        self._attr_unique_id = f"{self._consumer.consumer_uuid}_energy"
        self._attr_device_info = entry.runtime_data.device_info
        self._attr_native_value = 0.0
        self._attr_available = True
        entry.runtime_data.energy_sensor = self

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose baseline metadata for debugging/traceability."""
        return {
            ATTR_LAST_SOURCE_ENTITY_ID: self._tracker.last_source_entity_id,
            ATTR_LAST_SOURCE_ENERGY_VALUE_KWH: self._tracker.last_source_energy_value_kwh,
            ATTR_LAST_VALID_SOURCE_SAMPLE_TS: self._tracker.last_valid_source_sample_ts,
            ATTR_IGNORED_NEGATIVE_DELTA_COUNT: self._tracker.ignored_negative_delta_count,
            ATTR_RESET_DETECTED_COUNT: self._tracker.reset_detected_count,
            ATTR_CURRENT_NORMALIZED_SOURCE_UNIT: self._tracker.current_normalized_source_unit,
        }

    async def async_added_to_hass(self) -> None:
        """Restore previous state and subscribe to source updates."""
        if stored := await self._entry.runtime_data.store.async_load():
            self._tracker = stored
            self._attr_native_value = round(self._tracker.virtual_total_kwh, 6)
        elif (last_state := await self.async_get_last_state()) is not None:
            restored_total = _parse_numeric(last_state.state)
            if restored_total is not None:
                self._tracker.virtual_total_kwh = restored_total
                self._attr_native_value = round(restored_total, 6)

        self._unsub_source = async_track_state_change_event(
            self.hass,
            [self._source_entity_id],
            self._async_handle_source_change,
        )
        # Process current source state as an immediate baseline/update.
        self._async_process_source_state()
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Handle cleanup on remove."""
        if self._unsub_source is not None:
            self._unsub_source()
            self._unsub_source = None
        if self._entry.runtime_data.energy_sensor is self:
            self._entry.runtime_data.energy_sensor = None
        await self._entry.runtime_data.store.async_flush_pending(self._tracker)
        self._entry.runtime_data.dismiss_all_issues(self.hass)

    @callback
    def _async_handle_source_change(self, _event: Event) -> None:
        self._async_process_source_state()
        self.async_write_ha_state()

    @callback
    def _async_process_source_state(self) -> None:
        """Apply required non-decreasing accumulation algorithm."""
        source_state = self.hass.states.get(self._source_entity_id)
        if source_state is None:
            self._attr_available = False
            self._entry.runtime_data.set_issue(
                self.hass,
                ISSUE_SOURCE_ENTITY_MISSING,
                is_active=True,
                translation_placeholders={"entity_id": self._source_entity_id},
            )
            return

        self._entry.runtime_data.set_issue(
            self.hass, ISSUE_SOURCE_ENTITY_MISSING, is_active=False
        )
        if source_state.state == STATE_UNAVAILABLE:
            self._attr_available = False
            return
        self._attr_available = True

        source_numeric = _parse_numeric(source_state.state)
        if source_numeric is None:
            return

        source_unit = source_state.attributes.get("unit_of_measurement")
        state_class = source_state.attributes.get("state_class")
        self._entry.runtime_data.set_issue(
            self.hass,
            ISSUE_ENERGY_STATE_CLASS_INVALID,
            is_active=state_class not in (
                SensorStateClass.TOTAL,
                SensorStateClass.TOTAL_INCREASING,
                None,
            ),
            translation_placeholders={"state_class": str(state_class)},
        )
        energy_unit_supported = _is_supported_energy_unit(source_unit)
        self._entry.runtime_data.set_issue(
            self.hass,
            ISSUE_ENERGY_UNIT_UNSUPPORTED,
            is_active=not energy_unit_supported,
            translation_placeholders={"unit": str(source_unit)},
        )
        source_kwh = _convert_energy_to_kwh(source_numeric, source_unit)
        if source_kwh is None:
            return

        if self._tracker.last_source_entity_id != self._source_entity_id:
            self._tracker.last_source_entity_id = self._source_entity_id
            self._tracker.last_source_energy_value_kwh = None

        if self._tracker.last_source_energy_value_kwh is None:
            self._tracker.last_source_energy_value_kwh = source_kwh
            self._tracker.last_valid_source_sample_ts = dt_util.utcnow().isoformat()
            self._tracker.current_normalized_source_unit = UnitOfEnergy.KILO_WATT_HOUR
            self._schedule_save()
            return

        delta = source_kwh - self._tracker.last_source_energy_value_kwh
        if delta > 0:
            self._tracker.virtual_total_kwh += delta
        elif delta < 0:
            self._tracker.ignored_negative_delta_count += 1
            self._tracker.reset_detected_count += 1
            _LOGGER.debug(
                "Source energy reset/rollover for %s: previous=%s current=%s",
                self._source_entity_id,
                self._tracker.last_source_energy_value_kwh,
                source_kwh,
            )

        self._tracker.last_source_energy_value_kwh = source_kwh
        self._tracker.last_valid_source_sample_ts = dt_util.utcnow().isoformat()
        self._tracker.current_normalized_source_unit = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_native_value = round(self._tracker.virtual_total_kwh, 6)
        self._schedule_save()

    def _schedule_save(self) -> None:
        """Persist tracker state and keep a reference to task."""
        self._entry.runtime_data.store.async_schedule_save(self._tracker)

    def _get_current_source_kwh(self) -> float:
        """Read current valid source value in kWh for maintenance actions."""
        source_state = self.hass.states.get(self._source_entity_id)
        if source_state is None:
            raise HomeAssistantError("Configured source energy sensor no longer exists")
        if source_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            raise HomeAssistantError("Configured source energy sensor is unavailable")

        source_numeric = _parse_numeric(source_state.state)
        if source_numeric is None:
            raise HomeAssistantError("Configured source energy sensor state is not numeric")

        source_kwh = _convert_energy_to_kwh(
            source_numeric, source_state.attributes.get("unit_of_measurement")
        )
        if source_kwh is None:
            raise HomeAssistantError("Configured source energy sensor unit is unsupported")
        return source_kwh

    async def async_adopt_current_source_as_baseline(self) -> None:
        """Adopt current valid source value as new baseline without changing total."""
        source_kwh = self._get_current_source_kwh()
        self._tracker.last_source_entity_id = self._source_entity_id
        self._tracker.last_source_energy_value_kwh = source_kwh
        self._tracker.last_valid_source_sample_ts = dt_util.utcnow().isoformat()
        self._tracker.current_normalized_source_unit = UnitOfEnergy.KILO_WATT_HOUR
        self._schedule_save()
        self.async_write_ha_state()

    async def async_reset_tracker(self) -> None:
        """Reset virtual total and clear baseline metadata."""
        self._tracker = EnergyTrackerState()
        self._attr_native_value = 0.0
        self._schedule_save()
        self.async_write_ha_state()

    async def async_set_virtual_total(self, value_kwh: float) -> None:
        """Set virtual total and reset/adopt baseline metadata."""
        if value_kwh < 0:
            raise HomeAssistantError("Virtual total cannot be negative")
        self._tracker.virtual_total_kwh = float(value_kwh)
        self._attr_native_value = round(self._tracker.virtual_total_kwh, 6)
        try:
            source_kwh = self._get_current_source_kwh()
            self._tracker.last_source_entity_id = self._source_entity_id
            self._tracker.last_source_energy_value_kwh = source_kwh
            self._tracker.last_valid_source_sample_ts = dt_util.utcnow().isoformat()
            self._tracker.current_normalized_source_unit = UnitOfEnergy.KILO_WATT_HOUR
        except HomeAssistantError:
            self._tracker.last_source_entity_id = None
            self._tracker.last_source_energy_value_kwh = None
            self._tracker.last_valid_source_sample_ts = None
            self._tracker.current_normalized_source_unit = None
        self._schedule_save()
        self.async_write_ha_state()

    @property
    def runtime_diagnostics(self) -> dict[str, Any]:
        """Return redacted runtime diagnostics details."""
        return {
            ATTR_LAST_VALID_SOURCE_SAMPLE_TS: self._tracker.last_valid_source_sample_ts,
            ATTR_IGNORED_NEGATIVE_DELTA_COUNT: self._tracker.ignored_negative_delta_count,
            ATTR_RESET_DETECTED_COUNT: self._tracker.reset_detected_count,
            ATTR_CURRENT_NORMALIZED_SOURCE_UNIT: self._tracker.current_normalized_source_unit,
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnergyDeviceBridgeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Energy Device Bridge sensors from config entry."""
    hass.data.setdefault(DOMAIN, {})
    if not hass.data[DOMAIN].get("entity_services_registered"):
        platform = entity_platform.async_get_current_platform()
        platform.async_register_entity_service(
            SERVICE_ADOPT_CURRENT_SOURCE_AS_BASELINE,
            {},
            "async_adopt_current_source_as_baseline",
        )
        platform.async_register_entity_service(
            SERVICE_RESET_TRACKER,
            {},
            "async_reset_tracker",
        )
        platform.async_register_entity_service(
            SERVICE_SET_VIRTUAL_TOTAL,
            {vol.Required(ATTR_VALUE_KWH): vol.Coerce(float)},
            "async_set_virtual_total",
        )
        hass.data[DOMAIN]["entity_services_registered"] = True

    entities: list[SensorEntity] = [EnergyDeviceBridgeEnergySensor(entry)]
    if entry.runtime_data.consumer.source_power_entity_id:
        entities.insert(0, EnergyDeviceBridgePowerSensor(entry))
    async_add_entities(entities)
