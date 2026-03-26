"""Constants for the Energy Device Bridge integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "energy_device_bridge"

PLATFORMS: Final = ["sensor", "button"]

CONF_CONSUMER_UUID: Final = "consumer_uuid"
CONF_CONSUMER_NAME: Final = "consumer_name"
CONF_SOURCE_POWER_ENTITY_ID: Final = "source_power_entity_id"
CONF_SOURCE_ENERGY_ENTITY_ID: Final = "source_energy_entity_id"
CONF_ZERO_DROP_POLICY: Final = "zero_drop_policy"
CONF_NOTIFY_ON_LOWER_NON_ZERO: Final = "notify_on_lower_non_zero"

ZERO_DROP_POLICY_IGNORE_ZERO_UNTIL_NON_ZERO: Final = "ignore_zero_until_non_zero"
ZERO_DROP_POLICY_ACCEPT_ZERO_AS_NEW_CYCLE: Final = "accept_zero_as_new_cycle"
ZERO_DROP_POLICIES: Final = (
    ZERO_DROP_POLICY_IGNORE_ZERO_UNTIL_NON_ZERO,
    ZERO_DROP_POLICY_ACCEPT_ZERO_AS_NEW_CYCLE,
)
DEFAULT_ZERO_DROP_POLICY: Final = ZERO_DROP_POLICY_ACCEPT_ZERO_AS_NEW_CYCLE
DEFAULT_NOTIFY_ON_LOWER_NON_ZERO: Final = False

STORAGE_VERSION: Final = 1
STORAGE_KEY_PREFIX: Final = f"{DOMAIN}_entry_"

ATTR_VIRTUAL_TOTAL_KWH: Final = "virtual_total_kwh"
ATTR_LAST_SOURCE_ENTITY_ID: Final = "last_source_entity_id"
ATTR_LAST_SOURCE_ENERGY_VALUE_KWH: Final = "last_source_energy_value_kwh"
ATTR_LAST_VALID_SOURCE_SAMPLE_TS: Final = "last_valid_source_sample_ts"
ATTR_IGNORED_NEGATIVE_DELTA_COUNT: Final = "ignored_negative_delta_count"
ATTR_RESET_DETECTED_COUNT: Final = "reset_detected_count"
ATTR_CURRENT_NORMALIZED_SOURCE_UNIT: Final = "current_normalized_source_unit"
ATTR_AWAITING_NON_ZERO_AFTER_ZERO_DROP: Final = "awaiting_non_zero_after_zero_drop"
ATTR_LAST_ZERO_DROP_AT: Final = "last_zero_drop_at"
ATTR_LOWER_VALUE_COUNT: Final = "lower_value_count"
ATTR_ZERO_DROP_COUNT: Final = "zero_drop_count"
ATTR_LAST_LOWER_VALUE_EVENT: Final = "last_lower_value_event"

ATTR_VALUE_KWH: Final = "value_kwh"

SERVICE_ADOPT_CURRENT_SOURCE_AS_BASELINE: Final = "adopt_current_source_as_baseline"
SERVICE_RESET_TRACKER: Final = "reset_tracker"
SERVICE_SET_VIRTUAL_TOTAL: Final = "set_virtual_total"

ISSUE_SOURCE_ENTITY_MISSING: Final = "source_entity_missing"
ISSUE_ENERGY_UNIT_UNSUPPORTED: Final = "energy_unit_unsupported"
ISSUE_POWER_UNIT_UNSUPPORTED: Final = "power_unit_unsupported"
ISSUE_ENERGY_STATE_CLASS_INVALID: Final = "energy_state_class_invalid"
