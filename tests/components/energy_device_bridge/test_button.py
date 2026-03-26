"""Tests for Energy Device Bridge maintenance buttons."""

from __future__ import annotations

from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_device_bridge.const import (
    CONF_CONSUMER_NAME,
    CONF_CONSUMER_UUID,
    CONF_SOURCE_ENERGY_ENTITY_ID,
    CONF_SOURCE_POWER_ENTITY_ID,
    DOMAIN,
)


@pytest.mark.asyncio
async def test_button_entities_are_created_with_translation_names(hass: HomeAssistant) -> None:
    """Button entities are available and use translation-backed names."""
    hass.states.async_set(
        "sensor.src_energy",
        10,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-button",
            CONF_CONSUMER_NAME: "Button Device",
            CONF_SOURCE_POWER_ENTITY_ID: None,
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.src_energy",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    adopt_button_id = entity_registry.async_get_entity_id(
        "button", DOMAIN, "consumer-button_adopt_current_source_as_baseline"
    )
    reset_button_id = entity_registry.async_get_entity_id(
        "button", DOMAIN, "consumer-button_reset_tracker"
    )
    import_button_id = entity_registry.async_get_entity_id(
        "button", DOMAIN, "consumer-button_import_source_history"
    )
    assert adopt_button_id is not None
    assert reset_button_id is not None
    assert import_button_id is not None

    adopt_state = hass.states.get(adopt_button_id)
    reset_state = hass.states.get(reset_button_id)
    import_state = hass.states.get(import_button_id)
    assert adopt_state is not None
    assert reset_state is not None
    assert import_state is not None
    assert adopt_state.name.endswith("Adopt current source as baseline")
    assert reset_state.name.endswith("Reset tracker")
    assert import_state.name.endswith("Import source history")
