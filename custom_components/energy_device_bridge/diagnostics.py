"""Diagnostics support for Energy Device Bridge."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import EnergyDeviceBridgeConfigEntry
from .const import (
    ATTR_CURRENT_NORMALIZED_SOURCE_UNIT,
    ATTR_HISTORY_IMPORT_HAS_RUN,
    ATTR_HISTORY_IMPORT_CREATE_INVOKED,
    ATTR_HISTORY_IMPORT_HOURS_IMPORTED,
    ATTR_HISTORY_IMPORT_IN_PROGRESS,
    ATTR_HISTORY_IMPORT_LAST_ERROR,
    ATTR_HISTORY_IMPORT_LAST_FINISHED_AT,
    ATTR_HISTORY_IMPORT_LAST_IMPORTED_HOUR_START,
    ATTR_HISTORY_IMPORT_LAST_RESULT,
    ATTR_HISTORY_IMPORT_LAST_STARTED_AT,
    ATTR_HISTORY_IMPORT_PERIOD_END,
    ATTR_HISTORY_IMPORT_PERIOD_START,
    ATTR_HISTORY_IMPORT_RETENTION_LIMITED,
    ATTR_HISTORY_IMPORT_SAMPLES_PROCESSED,
    ATTR_IGNORED_NEGATIVE_DELTA_COUNT,
    ATTR_LAST_SOURCE_ENTITY_ID,
    ATTR_LAST_VALID_SOURCE_SAMPLE_TS,
    ATTR_RESET_DETECTED_COUNT,
    CONF_COPY_SOURCE_HISTORY_ON_CREATE,
    CONF_CONSUMER_NAME,
    CONF_SOURCE_ENERGY_ENTITY_ID,
    CONF_SOURCE_POWER_ENTITY_ID,
)

TO_REDACT = {
    CONF_CONSUMER_NAME,
    CONF_SOURCE_POWER_ENTITY_ID,
    CONF_SOURCE_ENERGY_ENTITY_ID,
    ATTR_LAST_SOURCE_ENTITY_ID,
    "source_entity_id",
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
            "options": async_redact_data(dict(entry.options), TO_REDACT),
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
                    CONF_COPY_SOURCE_HISTORY_ON_CREATE: entry.options.get(
                        CONF_COPY_SOURCE_HISTORY_ON_CREATE, True
                    ),
                    ATTR_HISTORY_IMPORT_HAS_RUN: (
                        stored_state.history_import_has_run if stored_state else False
                    ),
                    ATTR_HISTORY_IMPORT_IN_PROGRESS: (
                        stored_state.history_import_in_progress if stored_state else False
                    ),
                    ATTR_HISTORY_IMPORT_LAST_STARTED_AT: (
                        stored_state.history_import_last_started_at if stored_state else None
                    ),
                    ATTR_HISTORY_IMPORT_LAST_FINISHED_AT: (
                        stored_state.history_import_last_finished_at if stored_state else None
                    ),
                    ATTR_HISTORY_IMPORT_LAST_RESULT: (
                        stored_state.history_import_last_result if stored_state else None
                    ),
                    ATTR_HISTORY_IMPORT_LAST_ERROR: (
                        stored_state.history_import_last_error if stored_state else None
                    ),
                    ATTR_HISTORY_IMPORT_RETENTION_LIMITED: (
                        stored_state.history_import_retention_limited
                        if stored_state
                        else False
                    ),
                    ATTR_HISTORY_IMPORT_SAMPLES_PROCESSED: (
                        stored_state.history_import_samples_processed if stored_state else 0
                    ),
                    ATTR_HISTORY_IMPORT_HOURS_IMPORTED: (
                        stored_state.history_import_hours_imported if stored_state else 0
                    ),
                    ATTR_HISTORY_IMPORT_PERIOD_START: (
                        stored_state.history_import_period_start if stored_state else None
                    ),
                    ATTR_HISTORY_IMPORT_PERIOD_END: (
                        stored_state.history_import_period_end if stored_state else None
                    ),
                    ATTR_HISTORY_IMPORT_LAST_IMPORTED_HOUR_START: (
                        stored_state.history_import_last_imported_hour_start
                        if stored_state
                        else None
                    ),
                    ATTR_HISTORY_IMPORT_CREATE_INVOKED: (
                        stored_state.history_import_create_invoked if stored_state else False
                    ),
                }
            ),
            TO_REDACT,
        ),
    }
