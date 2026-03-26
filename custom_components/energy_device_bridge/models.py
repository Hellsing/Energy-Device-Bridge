"""Runtime models for Energy Device Bridge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .const import (
    ATTR_LAST_SOURCE_ENERGY_VALUE_KWH,
    ATTR_LAST_SOURCE_ENTITY_ID,
    ATTR_VIRTUAL_TOTAL_KWH,
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

    def as_dict(self) -> dict[str, Any]:
        """Serialize state for storage."""
        return {
            ATTR_VIRTUAL_TOTAL_KWH: self.virtual_total_kwh,
            ATTR_LAST_SOURCE_ENTITY_ID: self.last_source_entity_id,
            ATTR_LAST_SOURCE_ENERGY_VALUE_KWH: self.last_source_energy_value_kwh,
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
        )


def resolve_consumer_config(data: dict[str, Any]) -> ConsumerConfig:
    """Resolve consumer config from config entry data."""
    return ConsumerConfig(
        consumer_uuid=data[CONF_CONSUMER_UUID],
        consumer_name=data[CONF_CONSUMER_NAME],
        source_power_entity_id=data.get(CONF_SOURCE_POWER_ENTITY_ID),
        source_energy_entity_id=data[CONF_SOURCE_ENERGY_ENTITY_ID],
    )
