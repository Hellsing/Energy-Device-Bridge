"""Tests for persisted model compatibility."""

from __future__ import annotations

from custom_components.energy_device_bridge.const import (
    ATTR_LAST_SOURCE_ENERGY_VALUE_KWH,
    ATTR_LAST_SOURCE_ENTITY_ID,
    ATTR_VIRTUAL_TOTAL_KWH,
)
from custom_components.energy_device_bridge.models import EnergyTrackerState


def test_energy_tracker_state_from_legacy_data_defaults_new_fields() -> None:
    """Older stored data restores safely with defaults for newly added fields."""
    legacy_data = {
        ATTR_VIRTUAL_TOTAL_KWH: 12.5,
        ATTR_LAST_SOURCE_ENTITY_ID: "sensor.legacy_energy",
        ATTR_LAST_SOURCE_ENERGY_VALUE_KWH: 3.2,
    }
    restored = EnergyTrackerState.from_dict(legacy_data)
    assert restored.virtual_total_kwh == 12.5
    assert restored.last_source_entity_id == "sensor.legacy_energy"
    assert restored.last_source_energy_value_kwh == 3.2
    assert restored.awaiting_non_zero_after_zero_drop is False
    assert restored.zero_drop_count == 0
    assert restored.lower_value_count == 0
