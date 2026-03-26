"""Diagnostics support for Energy Device Bridge."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import EnergyDeviceBridgeConfigEntry
from .const import (
    ATTR_CURRENT_NORMALIZED_SOURCE_UNIT,
    ATTR_IGNORED_NEGATIVE_DELTA_COUNT,
    ATTR_LAST_SOURCE_ENTITY_ID,
    ATTR_LAST_VALID_SOURCE_SAMPLE_TS,
    ATTR_RESET_DETECTED_COUNT,
    CONF_CONSUMER_NAME,
    CONF_SOURCE_ENERGY_ENTITY_ID,
    CONF_SOURCE_POWER_ENTITY_ID,
)

TO_REDACT = {
    CONF_CONSUMER_NAME,
    CONF_SOURCE_POWER_ENTITY_ID,
    CONF_SOURCE_ENERGY_ENTITY_ID,
    ATTR_LAST_SOURCE_ENTITY_ID,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: EnergyDeviceBridgeConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    stored_state = await entry.runtime_data.store.async_load()
    return {
        "entry": {
            "entry_id": entry.entry_id,
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
        },
        "stored_state": async_redact_data(
            stored_state.as_dict() if stored_state else {},
            TO_REDACT,
        ),
        "runtime": async_redact_data(
            (
                entry.runtime_data.energy_sensor.runtime_diagnostics
                if entry.runtime_data.energy_sensor is not None
                else {
                    ATTR_LAST_VALID_SOURCE_SAMPLE_TS: (
                        stored_state.last_valid_source_sample_ts if stored_state else None
                    ),
                    ATTR_IGNORED_NEGATIVE_DELTA_COUNT: (
                        stored_state.ignored_negative_delta_count if stored_state else 0
                    ),
                    ATTR_RESET_DETECTED_COUNT: (
                        stored_state.reset_detected_count if stored_state else 0
                    ),
                    ATTR_CURRENT_NORMALIZED_SOURCE_UNIT: (
                        stored_state.current_normalized_source_unit if stored_state else None
                    ),
                }
            ),
            TO_REDACT,
        ),
    }
