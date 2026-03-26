"""Constants for the Energy Device Bridge integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "energy_device_bridge"

PLATFORMS: Final = ["sensor", "button"]

CONF_CONSUMER_UUID: Final = "consumer_uuid"
CONF_CONSUMER_NAME: Final = "consumer_name"
CONF_SOURCE_POWER_ENTITY_ID: Final = "source_power_entity_id"
CONF_SOURCE_ENERGY_ENTITY_ID: Final = "source_energy_entity_id"

STORAGE_VERSION: Final = 1
STORAGE_KEY_PREFIX: Final = f"{DOMAIN}_entry_"

ATTR_VIRTUAL_TOTAL_KWH: Final = "virtual_total_kwh"
ATTR_LAST_SOURCE_ENTITY_ID: Final = "last_source_entity_id"
ATTR_LAST_SOURCE_ENERGY_VALUE_KWH: Final = "last_source_energy_value_kwh"
ATTR_LAST_VALID_SOURCE_SAMPLE_TS: Final = "last_valid_source_sample_ts"
ATTR_IGNORED_NEGATIVE_DELTA_COUNT: Final = "ignored_negative_delta_count"
ATTR_RESET_DETECTED_COUNT: Final = "reset_detected_count"
ATTR_CURRENT_NORMALIZED_SOURCE_UNIT: Final = "current_normalized_source_unit"

ATTR_VALUE_KWH: Final = "value_kwh"

SERVICE_ADOPT_CURRENT_SOURCE_AS_BASELINE: Final = "adopt_current_source_as_baseline"
SERVICE_RESET_TRACKER: Final = "reset_tracker"
SERVICE_SET_VIRTUAL_TOTAL: Final = "set_virtual_total"

ISSUE_SOURCE_ENTITY_MISSING: Final = "source_entity_missing"
ISSUE_ENERGY_UNIT_UNSUPPORTED: Final = "energy_unit_unsupported"
ISSUE_POWER_UNIT_UNSUPPORTED: Final = "power_unit_unsupported"
ISSUE_ENERGY_STATE_CLASS_INVALID: Final = "energy_state_class_invalid"
