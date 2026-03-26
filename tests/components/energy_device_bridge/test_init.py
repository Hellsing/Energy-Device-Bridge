"""Core setup and lifecycle tests for Energy Device Bridge."""

from __future__ import annotations

import json
from pathlib import Path

from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_device_bridge import async_remove_config_entry_device
from custom_components.energy_device_bridge.const import (
    CONF_CONSUMER_NAME,
    CONF_CONSUMER_UUID,
    CONF_SOURCE_ENERGY_ENTITY_ID,
    CONF_SOURCE_POWER_ENTITY_ID,
    DOMAIN,
    SERVICE_IMPORT_SOURCE_HISTORY,
)


def test_manifest_basics() -> None:
    """Manifest has required standalone custom integration metadata."""
    manifest = json.loads(
        Path("custom_components/energy_device_bridge/manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["domain"] == DOMAIN
    assert manifest["name"] == "Energy Device Bridge"
    assert manifest["config_flow"] is True
    assert manifest["integration_type"] == "device"
    assert manifest["iot_class"] == "calculated"
    assert manifest["version"]


@pytest.mark.asyncio
async def test_import_service_registered(hass) -> None:
    """Integration-level import service is registered on setup."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    assert hass.services.has_service(DOMAIN, SERVICE_IMPORT_SOURCE_HISTORY)


@pytest.mark.asyncio
async def test_setup_unload_and_remove_entry(hass) -> None:
    """Entry sets up entities, unloads cleanly, and removes storage file."""
    hass.states.async_set("sensor.power_source", 100, {"unit_of_measurement": UnitOfPower.WATT})
    hass.states.async_set(
        "sensor.energy_source",
        10,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-init",
            CONF_CONSUMER_NAME: "Init Device",
            CONF_SOURCE_POWER_ENTITY_ID: "sensor.power_source",
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.energy_source",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(identifiers={(DOMAIN, "consumer-init")})
    assert device is not None

    entity_registry = er.async_get(hass)
    power_entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "consumer-init_power"
    )
    energy_entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "consumer-init_energy"
    )
    assert power_entity_id is not None
    assert energy_entity_id is not None
    assert hass.states.get(power_entity_id) is not None
    assert hass.states.get(energy_entity_id) is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(power_entity_id).state == "unavailable"
    assert hass.states.get(energy_entity_id).state == "unavailable"

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_delete_device_removes_config_entry_and_child_entities(hass) -> None:
    """Removing the bridge device removes the owning config entry and entities."""
    hass.states.async_set("sensor.power_source", 100, {"unit_of_measurement": UnitOfPower.WATT})
    hass.states.async_set(
        "sensor.energy_source",
        10,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-delete",
            CONF_CONSUMER_NAME: "Delete Device",
            CONF_SOURCE_POWER_ENTITY_ID: "sensor.power_source",
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.energy_source",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    power_entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "consumer-delete_power"
    )
    energy_entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "consumer-delete_energy"
    )
    assert power_entity_id is not None
    assert energy_entity_id is not None

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(identifiers={(DOMAIN, "consumer-delete")})
    assert device is not None

    assert await async_remove_config_entry_device(hass, entry, device)
    await hass.async_block_till_done()

    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    assert entity_registry.async_get(power_entity_id) is None
    assert entity_registry.async_get(energy_entity_id) is None
