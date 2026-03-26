"""Runtime models for Energy Device Bridge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .const import (
    ATTR_AWAITING_NON_ZERO_AFTER_ZERO_DROP,
    ATTR_CURRENT_NORMALIZED_SOURCE_UNIT,
    ATTR_IGNORED_NEGATIVE_DELTA_COUNT,
    ATTR_LAST_LOWER_VALUE_EVENT,
    ATTR_LAST_SOURCE_ENERGY_VALUE_KWH,
    ATTR_LAST_SOURCE_ENTITY_ID,
    ATTR_LAST_VALID_SOURCE_SAMPLE_TS,
    ATTR_LAST_ZERO_DROP_AT,
    ATTR_LOWER_VALUE_COUNT,
    ATTR_RESET_DETECTED_COUNT,
    ATTR_VIRTUAL_TOTAL_KWH,
    ATTR_ZERO_DROP_COUNT,
    CONF_CONSUMER_NAME,
    CONF_CONSUMER_UUID,
    CONF_SOURCE_ENERGY_ENTITY_ID,
    CONF_SOURCE_POWER_ENTITY_ID,
)


@dataclass(slots=True)
class ConsumerConfig:
    """Resolved consumer configuration from config entry data."""

    consumer_uuid: str
    consumer_name: str
    source_power_entity_id: str | None
    source_energy_entity_id: str


@dataclass(slots=True)
class EnergyTrackerState:
    """Persisted state for the virtual energy tracker."""

    virtual_total_kwh: float = 0.0
    last_source_entity_id: str | None = None
    last_source_energy_value_kwh: float | None = None
    last_valid_source_sample_ts: str | None = None
    ignored_negative_delta_count: int = 0
    reset_detected_count: int = 0
    current_normalized_source_unit: str | None = None
    awaiting_non_zero_after_zero_drop: bool = False
    last_zero_drop_at: str | None = None
    lower_value_count: int = 0
    zero_drop_count: int = 0
    last_lower_value_event: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialize state for storage."""
        return {
            ATTR_VIRTUAL_TOTAL_KWH: self.virtual_total_kwh,
            ATTR_LAST_SOURCE_ENTITY_ID: self.last_source_entity_id,
            ATTR_LAST_SOURCE_ENERGY_VALUE_KWH: self.last_source_energy_value_kwh,
            ATTR_LAST_VALID_SOURCE_SAMPLE_TS: self.last_valid_source_sample_ts,
            ATTR_IGNORED_NEGATIVE_DELTA_COUNT: self.ignored_negative_delta_count,
            ATTR_RESET_DETECTED_COUNT: self.reset_detected_count,
            ATTR_CURRENT_NORMALIZED_SOURCE_UNIT: self.current_normalized_source_unit,
            ATTR_AWAITING_NON_ZERO_AFTER_ZERO_DROP: self.awaiting_non_zero_after_zero_drop,
            ATTR_LAST_ZERO_DROP_AT: self.last_zero_drop_at,
            ATTR_LOWER_VALUE_COUNT: self.lower_value_count,
            ATTR_ZERO_DROP_COUNT: self.zero_drop_count,
            ATTR_LAST_LOWER_VALUE_EVENT: self.last_lower_value_event,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> EnergyTrackerState:
        """Deserialize state from storage."""
        if not data:
            return cls()

        return cls(
            virtual_total_kwh=float(data.get(ATTR_VIRTUAL_TOTAL_KWH, 0.0) or 0.0),
            last_source_entity_id=data.get(ATTR_LAST_SOURCE_ENTITY_ID),
            last_source_energy_value_kwh=(
                float(data[ATTR_LAST_SOURCE_ENERGY_VALUE_KWH])
                if data.get(ATTR_LAST_SOURCE_ENERGY_VALUE_KWH) is not None
                else None
            ),
            last_valid_source_sample_ts=data.get(ATTR_LAST_VALID_SOURCE_SAMPLE_TS),
            ignored_negative_delta_count=int(data.get(ATTR_IGNORED_NEGATIVE_DELTA_COUNT, 0)),
            reset_detected_count=int(data.get(ATTR_RESET_DETECTED_COUNT, 0)),
            current_normalized_source_unit=data.get(ATTR_CURRENT_NORMALIZED_SOURCE_UNIT),
            awaiting_non_zero_after_zero_drop=bool(
                data.get(ATTR_AWAITING_NON_ZERO_AFTER_ZERO_DROP, False)
            ),
            last_zero_drop_at=data.get(ATTR_LAST_ZERO_DROP_AT),
            lower_value_count=int(data.get(ATTR_LOWER_VALUE_COUNT, 0)),
            zero_drop_count=int(data.get(ATTR_ZERO_DROP_COUNT, 0)),
            last_lower_value_event=data.get(ATTR_LAST_LOWER_VALUE_EVENT),
        )


def resolve_consumer_config(data: dict[str, Any]) -> ConsumerConfig:
    """Resolve consumer config from config entry data."""
    return ConsumerConfig(
        consumer_uuid=data[CONF_CONSUMER_UUID],
        consumer_name=data[CONF_CONSUMER_NAME],
        source_power_entity_id=data.get(CONF_SOURCE_POWER_ENTITY_ID),
        source_energy_entity_id=data[CONF_SOURCE_ENERGY_ENTITY_ID],
    )
