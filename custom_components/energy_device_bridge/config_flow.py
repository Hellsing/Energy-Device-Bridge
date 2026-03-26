"""Config flow for Energy Device Bridge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import voluptuous as vol

from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.sensor import SensorStateClass
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    OptionsFlowWithConfigEntry,
)
from homeassistant.const import (
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er, selector
from homeassistant.util.unit_conversion import EnergyConverter, PowerConverter

from .const import (
    CONF_CONSUMER_NAME,
    CONF_CONSUMER_UUID,
    CONF_COPY_SOURCE_HISTORY_ON_CREATE,
    CONF_COPY_SOURCE_HISTORY_ON_CREATE_PENDING,
    CONF_NOTIFY_ON_LOWER_NON_ZERO,
    CONF_SOURCE_ENERGY_ENTITY_ID,
    CONF_SOURCE_POWER_ENTITY_ID,
    CONF_ZERO_DROP_POLICY,
    DEFAULT_COPY_SOURCE_HISTORY_ON_CREATE,
    DEFAULT_NOTIFY_ON_LOWER_NON_ZERO,
    DEFAULT_ZERO_DROP_POLICY,
    DOMAIN,
    ZERO_DROP_POLICIES,
)
from .models import resolve_consumer_config


@dataclass(slots=True)
class FlowValidationResult:
    """Result of input validation."""

    errors: dict[str, str]
    validated_data: dict[str, str | None] | None = None


_ALLOWED_ENERGY_UNITS = {
    UnitOfEnergy.WATT_HOUR,
    UnitOfEnergy.KILO_WATT_HOUR,
}
_ALLOWED_POWER_UNITS = {
    UnitOfPower.WATT,
    UnitOfPower.KILO_WATT,
}


def _selector_schema(defaults: dict[str, Any]) -> vol.Schema:
    source_power_default = defaults.get(CONF_SOURCE_POWER_ENTITY_ID)
    power_key: vol.Optional
    if source_power_default is None:
        power_key = vol.Optional(CONF_SOURCE_POWER_ENTITY_ID)
    else:
        power_key = vol.Optional(
            CONF_SOURCE_POWER_ENTITY_ID,
            default=source_power_default,
        )
    return vol.Schema(
        {
            vol.Required(
                CONF_CONSUMER_NAME,
                default=defaults.get(CONF_CONSUMER_NAME, ""),
            ): selector.TextSelector(selector.TextSelectorConfig()),
            power_key: selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=[SENSOR_DOMAIN],
                )
            ),
            vol.Required(
                CONF_SOURCE_ENERGY_ENTITY_ID,
                default=defaults.get(CONF_SOURCE_ENERGY_ENTITY_ID, ""),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=[SENSOR_DOMAIN],
                )
            ),
        }
    )


def _options_selector_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_ZERO_DROP_POLICY,
                default=defaults.get(CONF_ZERO_DROP_POLICY, DEFAULT_ZERO_DROP_POLICY),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(ZERO_DROP_POLICIES),
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key=CONF_ZERO_DROP_POLICY,
                )
            ),
            vol.Required(
                CONF_NOTIFY_ON_LOWER_NON_ZERO,
                default=defaults.get(
                    CONF_NOTIFY_ON_LOWER_NON_ZERO,
                    DEFAULT_NOTIFY_ON_LOWER_NON_ZERO,
                ),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_COPY_SOURCE_HISTORY_ON_CREATE,
                default=defaults.get(
                    CONF_COPY_SOURCE_HISTORY_ON_CREATE,
                    DEFAULT_COPY_SOURCE_HISTORY_ON_CREATE,
                ),
            ): selector.BooleanSelector(),
        }
    )


def _combined_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Merge base entity schema and behavior options schema."""
    return vol.Schema(
        {
            **_selector_schema(defaults).schema,
            **_options_selector_schema(defaults).schema,
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
    source_power_entity_id: str | None,
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
    def _set_error(field: str, value: str) -> None:
        errors.setdefault(field, value)

    errors: dict[str, str] = {}
    entity_registry = er.async_get(hass)
    consumer_name = str(user_input[CONF_CONSUMER_NAME]).strip()
    source_power_entity_id: str | None = user_input.get(CONF_SOURCE_POWER_ENTITY_ID)
    if source_power_entity_id == "":
        source_power_entity_id = None
    source_energy_entity_id = user_input[CONF_SOURCE_ENERGY_ENTITY_ID]

    if not consumer_name:
        _set_error(CONF_CONSUMER_NAME, "name_required")

    if source_power_entity_id and source_power_entity_id == source_energy_entity_id:
        _set_error("base", "same_entity_pair")

    if _is_duplicate_pair(
        current_entries,
        source_power_entity_id,
        source_energy_entity_id,
        skip_entry_id=skip_entry_id,
    ):
        _set_error("base", "duplicate_pair")

    entities_to_validate = [
        (CONF_SOURCE_ENERGY_ENTITY_ID, source_energy_entity_id),
    ]
    if source_power_entity_id:
        entities_to_validate.append(
            (CONF_SOURCE_POWER_ENTITY_ID, source_power_entity_id)
        )

    for field, entity_id in entities_to_validate:
        if not _validate_entity_kind(entity_id):
            _set_error(field, "must_be_sensor")
            continue
        if (
            not entity_registry.async_is_registered(entity_id)
            and hass.states.get(entity_id) is None
        ):
            _set_error(field, "entity_not_found")

    if source_power_entity_id:
        power_state = hass.states.get(source_power_entity_id)
        if power_state is not None:
            power_numeric = _parse_numeric_state_value(power_state.state)
            power_unit = power_state.attributes.get("unit_of_measurement")
            if power_numeric is None and power_state.state not in (
                STATE_UNKNOWN,
                STATE_UNAVAILABLE,
            ):
                _set_error(CONF_SOURCE_POWER_ENTITY_ID, "power_not_numeric")
            elif power_unit and not _is_power_unit_supported(power_unit):
                _set_error(CONF_SOURCE_POWER_ENTITY_ID, "invalid_power_unit")

    energy_state = hass.states.get(source_energy_entity_id)
    if energy_state is not None:
        energy_numeric = _parse_numeric_state_value(energy_state.state)
        energy_unit = energy_state.attributes.get("unit_of_measurement")
        if energy_numeric is None and energy_state.state not in (
            STATE_UNKNOWN,
            STATE_UNAVAILABLE,
        ):
            _set_error(CONF_SOURCE_ENERGY_ENTITY_ID, "energy_not_numeric")
        if not _is_energy_unit_supported(energy_unit):
            _set_error(CONF_SOURCE_ENERGY_ENTITY_ID, "invalid_energy_unit")
        state_class = energy_state.attributes.get("state_class")
        if state_class not in (
            SensorStateClass.TOTAL,
            SensorStateClass.TOTAL_INCREASING,
            None,
        ):
            _set_error(CONF_SOURCE_ENERGY_ENTITY_ID, "invalid_energy_state_class")

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

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> EnergyDeviceBridgeOptionsFlow:
        """Create the options flow to edit entry configuration from the gear menu."""
        return EnergyDeviceBridgeOptionsFlow(config_entry)

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
                selected_zero_drop_policy = user_input.get(
                    CONF_ZERO_DROP_POLICY, DEFAULT_ZERO_DROP_POLICY
                )
                selected_notify_on_lower_non_zero = bool(
                    user_input.get(
                        CONF_NOTIFY_ON_LOWER_NON_ZERO,
                        DEFAULT_NOTIFY_ON_LOWER_NON_ZERO,
                    )
                )
                selected_copy_source_history_on_create = bool(
                    user_input.get(
                        CONF_COPY_SOURCE_HISTORY_ON_CREATE,
                        DEFAULT_COPY_SOURCE_HISTORY_ON_CREATE,
                    )
                )
                data = {
                    CONF_CONSUMER_UUID: consumer_uuid,
                    **result.validated_data,
                }
                return self.async_create_entry(
                    title=result.validated_data[CONF_CONSUMER_NAME],
                    data=data,
                    options={
                        CONF_ZERO_DROP_POLICY: selected_zero_drop_policy,
                        CONF_NOTIFY_ON_LOWER_NON_ZERO: selected_notify_on_lower_non_zero,
                        CONF_COPY_SOURCE_HISTORY_ON_CREATE: (
                            selected_copy_source_history_on_create
                        ),
                        CONF_COPY_SOURCE_HISTORY_ON_CREATE_PENDING: (
                            selected_copy_source_history_on_create
                        ),
                    },
                )
            errors = result.errors

        return self.async_show_form(
            step_id="user",
            data_schema=_combined_schema(user_input or {}),
            errors=errors,
        )


class EnergyDeviceBridgeOptionsFlow(OptionsFlowWithConfigEntry):
    """Options flow used by the config entry gear menu."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Update the config entry through the options flow."""
        config_entry = self.config_entry
        defaults = resolve_consumer_config(config_entry.data)
        option_defaults = {
            CONF_ZERO_DROP_POLICY: config_entry.options.get(
                CONF_ZERO_DROP_POLICY, DEFAULT_ZERO_DROP_POLICY
            ),
            CONF_NOTIFY_ON_LOWER_NON_ZERO: config_entry.options.get(
                CONF_NOTIFY_ON_LOWER_NON_ZERO, DEFAULT_NOTIFY_ON_LOWER_NON_ZERO
            ),
            CONF_COPY_SOURCE_HISTORY_ON_CREATE: config_entry.options.get(
                CONF_COPY_SOURCE_HISTORY_ON_CREATE,
                DEFAULT_COPY_SOURCE_HISTORY_ON_CREATE,
            ),
        }
        errors: dict[str, str] = {}

        if user_input is not None:
            result = _validate_user_input(
                self.hass,
                self.hass.config_entries.async_entries(DOMAIN),
                user_input,
                skip_entry_id=config_entry.entry_id,
            )
            if not result.errors and result.validated_data:
                selected_zero_drop_policy = user_input[CONF_ZERO_DROP_POLICY]
                selected_notify_on_lower_non_zero = bool(
                    user_input[CONF_NOTIFY_ON_LOWER_NON_ZERO]
                )
                selected_copy_source_history_on_create = bool(
                    user_input[CONF_COPY_SOURCE_HISTORY_ON_CREATE]
                )
                self.hass.config_entries.async_update_entry(
                    config_entry,
                    title=result.validated_data[CONF_CONSUMER_NAME],
                    data={
                        CONF_CONSUMER_UUID: config_entry.data[CONF_CONSUMER_UUID],
                        **result.validated_data,
                    },
                )
                await self.hass.config_entries.async_reload(config_entry.entry_id)
                return self.async_create_entry(
                    title="",
                    data={
                        **config_entry.options,
                        CONF_ZERO_DROP_POLICY: selected_zero_drop_policy,
                        CONF_NOTIFY_ON_LOWER_NON_ZERO: selected_notify_on_lower_non_zero,
                        CONF_COPY_SOURCE_HISTORY_ON_CREATE: (
                            selected_copy_source_history_on_create
                        ),
                    },
                )
            errors = result.errors

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                _combined_schema({}),
                {
                    CONF_CONSUMER_NAME: defaults.consumer_name,
                    CONF_SOURCE_POWER_ENTITY_ID: defaults.source_power_entity_id,
                    CONF_SOURCE_ENERGY_ENTITY_ID: defaults.source_energy_entity_id,
                    **option_defaults,
                },
            ),
            errors=errors,
        )
