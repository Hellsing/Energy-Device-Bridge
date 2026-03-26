"""Config flow for Energy Device Bridge."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any
from uuid import uuid4

import voluptuous as vol

from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.sensor import SensorStateClass
from homeassistant.config_entries import ConfigEntry, ConfigFlow
from homeassistant.const import (
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er, selector
from homeassistant.util.unit_conversion import EnergyConverter, PowerConverter

from .const import (
    CONF_CONSUMER_NAME,
    CONF_CONSUMER_UUID,
    CONF_SOURCE_ENERGY_ENTITY_ID,
    CONF_SOURCE_POWER_ENTITY_ID,
    DOMAIN,
)
from .models import resolve_consumer_config

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class FlowValidationResult:
    """Result of input validation."""

    errors: dict[str, str]
    validated_data: dict[str, str] | None = None


_ALLOWED_ENERGY_UNITS = {
    UnitOfEnergy.WATT_HOUR,
    UnitOfEnergy.KILO_WATT_HOUR,
}
_ALLOWED_POWER_UNITS = {
    UnitOfPower.WATT,
    UnitOfPower.KILO_WATT,
}


def _selector_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_CONSUMER_NAME,
                default=defaults.get(CONF_CONSUMER_NAME, ""),
            ): selector.TextSelector(selector.TextSelectorConfig()),
            vol.Required(
                CONF_SOURCE_POWER_ENTITY_ID,
                default=defaults.get(CONF_SOURCE_POWER_ENTITY_ID, ""),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=[SENSOR_DOMAIN],
                    device_class=[SensorDeviceClass.POWER],
                )
            ),
            vol.Required(
                CONF_SOURCE_ENERGY_ENTITY_ID,
                default=defaults.get(CONF_SOURCE_ENERGY_ENTITY_ID, ""),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=[SENSOR_DOMAIN],
                    device_class=[SensorDeviceClass.ENERGY],
                )
            ),
        }
    )


def _parse_numeric_state_value(state_value: str) -> float | None:
    if state_value.lower() in {"none", STATE_UNKNOWN, STATE_UNAVAILABLE}:
        return None
    try:
        return float(state_value)
    except (TypeError, ValueError):
        return None


def _is_energy_unit_supported(unit: str | None) -> bool:
    if not unit:
        return False
    if unit not in _ALLOWED_ENERGY_UNITS:
        return False
    try:
        EnergyConverter.convert(1, unit, UnitOfEnergy.KILO_WATT_HOUR)
    except (TypeError, ValueError, HomeAssistantError):
        return False
    return True


def _is_power_unit_supported(unit: str | None) -> bool:
    if not unit:
        return False
    if unit not in _ALLOWED_POWER_UNITS:
        return False
    try:
        PowerConverter.convert(1, unit, UnitOfPower.KILO_WATT)
    except (TypeError, ValueError, HomeAssistantError):
        return False
    return True


def _is_duplicate_pair(
    current_entries: list[ConfigEntry],
    source_power_entity_id: str,
    source_energy_entity_id: str,
    *,
    skip_entry_id: str | None = None,
) -> bool:
    for entry in current_entries:
        if skip_entry_id and entry.entry_id == skip_entry_id:
            continue
        consumer = resolve_consumer_config(entry.data)
        if (
            consumer.source_power_entity_id == source_power_entity_id
            and consumer.source_energy_entity_id == source_energy_entity_id
        ):
            return True
    return False


def _validate_entity_kind(entity_id: str) -> bool:
    domain, _, _ = entity_id.partition(".")
    return domain == SENSOR_DOMAIN


def _validate_user_input(
    hass: HomeAssistant,
    current_entries: list[ConfigEntry],
    user_input: dict[str, Any],
    *,
    skip_entry_id: str | None = None,
) -> FlowValidationResult:
    errors: dict[str, str] = {}
    entity_registry = er.async_get(hass)
    consumer_name = str(user_input[CONF_CONSUMER_NAME]).strip()
    source_power_entity_id = user_input[CONF_SOURCE_POWER_ENTITY_ID]
    source_energy_entity_id = user_input[CONF_SOURCE_ENERGY_ENTITY_ID]

    if not consumer_name:
        errors[CONF_CONSUMER_NAME] = "name_required"

    if source_power_entity_id == source_energy_entity_id:
        errors["base"] = "same_entity_pair"

    if _is_duplicate_pair(
        current_entries,
        source_power_entity_id,
        source_energy_entity_id,
        skip_entry_id=skip_entry_id,
    ):
        errors["base"] = "duplicate_pair"

    for field, entity_id in (
        (CONF_SOURCE_POWER_ENTITY_ID, source_power_entity_id),
        (CONF_SOURCE_ENERGY_ENTITY_ID, source_energy_entity_id),
    ):
        if not _validate_entity_kind(entity_id):
            errors[field] = "must_be_sensor"
            continue
        if not entity_registry.async_is_registered(entity_id) and hass.states.get(entity_id) is None:
            errors[field] = "entity_not_found"

    power_state = hass.states.get(source_power_entity_id)
    if power_state is not None:
        power_numeric = _parse_numeric_state_value(power_state.state)
        power_unit = power_state.attributes.get("unit_of_measurement")
        if power_numeric is None and power_state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            errors[CONF_SOURCE_POWER_ENTITY_ID] = "power_not_numeric"
        elif power_unit and not _is_power_unit_supported(power_unit):
            errors[CONF_SOURCE_POWER_ENTITY_ID] = "invalid_power_unit"

    energy_state = hass.states.get(source_energy_entity_id)
    if energy_state is not None:
        energy_numeric = _parse_numeric_state_value(energy_state.state)
        energy_unit = energy_state.attributes.get("unit_of_measurement")
        if energy_numeric is None and energy_state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            errors[CONF_SOURCE_ENERGY_ENTITY_ID] = "energy_not_numeric"
        if not _is_energy_unit_supported(energy_unit):
            errors[CONF_SOURCE_ENERGY_ENTITY_ID] = "invalid_energy_unit"
        state_class = energy_state.attributes.get("state_class")
        if state_class not in (
            SensorStateClass.TOTAL,
            SensorStateClass.TOTAL_INCREASING,
            None,
        ):
            errors[CONF_SOURCE_ENERGY_ENTITY_ID] = "invalid_energy_state_class"

    if errors:
        return FlowValidationResult(errors=errors)

    return FlowValidationResult(
        errors={},
        validated_data={
            CONF_CONSUMER_NAME: consumer_name,
            CONF_SOURCE_POWER_ENTITY_ID: source_power_entity_id,
            CONF_SOURCE_ENERGY_ENTITY_ID: source_energy_entity_id,
        },
    )


class EnergyDeviceBridgeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Energy Device Bridge."""

    VERSION = 1

    def _resolve_reconfigure_entry(self) -> ConfigEntry:
        """Resolve reconfigure entry across Home Assistant versions."""
        getter = getattr(self, "_get_reconfigure_entry", None)
        if getter is not None:
            return getter()

        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            raise ValueError("Reconfigure target entry was not found")
        return entry

    def _abort_if_unique_id_mismatch_if_supported(self) -> None:
        """Abort if unique_id mismatches when helper exists."""
        helper = getattr(self, "_abort_if_unique_id_mismatch", None)
        if helper is not None:
            helper()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Handle initial setup."""
        errors: dict[str, str] = {}
        if user_input is not None:
            result = _validate_user_input(
                self.hass,
                self._async_current_entries(),
                user_input,
            )
            if not result.errors and result.validated_data:
                consumer_uuid = str(uuid4())
                await self.async_set_unique_id(consumer_uuid)
                self._abort_if_unique_id_configured()
                data = {
                    CONF_CONSUMER_UUID: consumer_uuid,
                    **result.validated_data,
                }
                return self.async_create_entry(
                    title=result.validated_data[CONF_CONSUMER_NAME],
                    data=data,
                )
            errors = result.errors

        return self.async_show_form(
            step_id="user",
            data_schema=_selector_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Handle config entry reconfiguration."""
        entry = self._resolve_reconfigure_entry()
        defaults = resolve_consumer_config(entry.data)
        errors: dict[str, str] = {}

        if user_input is not None:
            result = _validate_user_input(
                self.hass,
                self._async_current_entries(),
                user_input,
                skip_entry_id=entry.entry_id,
            )
            if not result.errors and result.validated_data:
                await self.async_set_unique_id(entry.data[CONF_CONSUMER_UUID])
                self._abort_if_unique_id_mismatch_if_supported()
                _LOGGER.debug("Reconfiguring Energy Device Bridge entry %s", entry.entry_id)
                try:
                    return self.async_update_reload_and_abort(
                        entry,
                        title=result.validated_data[CONF_CONSUMER_NAME],
                        data_updates=result.validated_data,
                        reason="reconfigure_successful",
                    )
                except TypeError:
                    return self.async_update_reload_and_abort(
                        entry,
                        title=result.validated_data[CONF_CONSUMER_NAME],
                        data={
                            CONF_CONSUMER_UUID: entry.data[CONF_CONSUMER_UUID],
                            **result.validated_data,
                        },
                        reason="reconfigure_successful",
                    )
            errors = result.errors

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_selector_schema(
                {
                    CONF_CONSUMER_NAME: defaults.consumer_name,
                    CONF_SOURCE_POWER_ENTITY_ID: defaults.source_power_entity_id,
                    CONF_SOURCE_ENERGY_ENTITY_ID: defaults.source_energy_entity_id,
                }
            ),
            errors=errors,
        )
