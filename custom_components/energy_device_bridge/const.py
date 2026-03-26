"""Constants for the Energy Device Bridge integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "energy_device_bridge"

PLATFORMS: Final = ["sensor"]

CONF_CONSUMER_UUID: Final = "consumer_uuid"
CONF_CONSUMER_NAME: Final = "consumer_name"
CONF_SOURCE_POWER_ENTITY_ID: Final = "source_power_entity_id"
CONF_SOURCE_ENERGY_ENTITY_ID: Final = "source_energy_entity_id"

STORAGE_VERSION: Final = 1
STORAGE_KEY_PREFIX: Final = f"{DOMAIN}_entry_"

ATTR_VIRTUAL_TOTAL_KWH: Final = "virtual_total_kwh"
ATTR_LAST_SOURCE_ENTITY_ID: Final = "last_source_entity_id"
ATTR_LAST_SOURCE_ENERGY_VALUE_KWH: Final = "last_source_energy_value_kwh"
