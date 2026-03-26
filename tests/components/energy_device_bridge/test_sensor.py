"""Tests for Energy Device Bridge sensors."""

from __future__ import annotations

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfEnergy, UnitOfPower
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

pytestmark = pytest.mark.asyncio


def _state_float(hass: HomeAssistant, entity_id: str) -> float:
    state = hass.states.get(entity_id)
    assert state is not None
    return float(state.state)


async def _setup_entry(
    hass: HomeAssistant, *, source_energy_entity_id: str = "sensor.src_energy"
) -> MockConfigEntry:
    hass.states.async_set("sensor.src_power", 100, {"unit_of_measurement": UnitOfPower.WATT})
    hass.states.async_set(
        source_energy_entity_id,
        10,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-1",
            CONF_CONSUMER_NAME: "Kuhlschrank",
            CONF_SOURCE_POWER_ENTITY_ID: "sensor.src_power",
            CONF_SOURCE_ENERGY_ENTITY_ID: source_energy_entity_id,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entity_ids(hass: HomeAssistant) -> tuple[str, str]:
    entity_registry = er.async_get(hass)
    power_entity_id = entity_registry.async_get_entity_id("sensor", DOMAIN, "consumer-1_power")
    energy_entity_id = entity_registry.async_get_entity_id("sensor", DOMAIN, "consumer-1_energy")
    assert power_entity_id is not None
    assert energy_entity_id is not None
    return power_entity_id, energy_entity_id


async def test_power_sensor_passthrough_behavior(hass: HomeAssistant) -> None:
    """Power sensor mirrors source state and converts to kW."""
    await _setup_entry(hass)
    power_entity_id, _ = _entity_ids(hass)

    assert _state_float(hass, power_entity_id) == 0.1
    hass.states.async_set("sensor.src_power", 250, {"unit_of_measurement": UnitOfPower.WATT})
    await hass.async_block_till_done()
    assert _state_float(hass, power_entity_id) == 0.25

    hass.states.async_set(
        "sensor.src_power",
        1.8,
        {"unit_of_measurement": UnitOfPower.KILO_WATT},
    )
    await hass.async_block_till_done()
    assert _state_float(hass, power_entity_id) == 1.8

    hass.states.async_set(
        "sensor.src_power",
        STATE_UNAVAILABLE,
        {"unit_of_measurement": UnitOfPower.WATT},
    )
    await hass.async_block_till_done()
    power_state = hass.states.get(power_entity_id)
    assert power_state is not None
    assert power_state.state == STATE_UNAVAILABLE


async def test_energy_accumulation_and_reset_handling(hass: HomeAssistant) -> None:
    """Energy sensor accumulates positive deltas and ignores negative reset deltas."""
    await _setup_entry(hass)
    _, energy_entity_id = _entity_ids(hass)

    # First sample establishes baseline, total remains 0
    assert _state_float(hass, energy_entity_id) == 0.0

    hass.states.async_set(
        "sensor.src_energy",
        11,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    await hass.async_block_till_done()
    assert _state_float(hass, energy_entity_id) == 1.0

    hass.states.async_set(
        "sensor.src_energy",
        12,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    await hass.async_block_till_done()
    assert _state_float(hass, energy_entity_id) == 2.0

    # Reset: do not decrease virtual total, only reset baseline.
    hass.states.async_set(
        "sensor.src_energy",
        0.4,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    await hass.async_block_till_done()
    assert _state_float(hass, energy_entity_id) == 2.0

    hass.states.async_set(
        "sensor.src_energy",
        0.9,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    await hass.async_block_till_done()
    assert _state_float(hass, energy_entity_id) == 2.5


async def test_energy_sensor_accepts_wh_and_converts_to_kwh(hass: HomeAssistant) -> None:
    """Energy source in Wh is normalized to kWh."""
    await _setup_entry(hass)
    _, energy_entity_id = _entity_ids(hass)

    hass.states.async_set("sensor.src_energy", 11000, {"unit_of_measurement": UnitOfEnergy.WATT_HOUR})
    await hass.async_block_till_done()
    assert _state_float(hass, energy_entity_id) == 1.0


async def test_source_replacement_after_reconfigure(hass: HomeAssistant) -> None:
    """After source entity replacement, first sample is baseline only."""
    await _setup_entry(hass)
    _, energy_entity_id = _entity_ids(hass)

    hass.states.async_set("sensor.src_energy", 11, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR})
    await hass.async_block_till_done()
    assert _state_float(hass, energy_entity_id) == 1.0

    hass.states.async_set("sensor.new_energy", 100, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR})
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.config_entries.async_update_entry(
        entry,
        data={
            CONF_CONSUMER_UUID: "consumer-1",
            CONF_CONSUMER_NAME: "Kuhlschrank",
            CONF_SOURCE_POWER_ENTITY_ID: "sensor.src_power",
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.new_energy",
        },
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    # First sample from the new source becomes baseline only.
    assert _state_float(hass, energy_entity_id) == 1.0

    hass.states.async_set(
        "sensor.new_energy",
        101.5,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    await hass.async_block_till_done()
    assert _state_float(hass, energy_entity_id) == 2.5


async def test_restore_total_and_baseline_across_reload(hass: HomeAssistant) -> None:
    """Stored total/baseline survive reload so next delta is correct."""
    await _setup_entry(hass)
    _, energy_entity_id = _entity_ids(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    hass.states.async_set("sensor.src_energy", 11, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR})
    await hass.async_block_till_done()
    assert _state_float(hass, energy_entity_id) == 1.0

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    # Baseline should be 11 after reload, so +1 increments total from 1 to 2.
    hass.states.async_set("sensor.src_energy", 12, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR})
    await hass.async_block_till_done()
    assert _state_float(hass, energy_entity_id) == 2.0
    restored_state = hass.states.get(energy_entity_id)
    assert restored_state is not None
    assert restored_state.attributes["state_class"] == "total_increasing"
    assert restored_state.attributes["device_class"] == "energy"
    assert restored_state.attributes["unit_of_measurement"] == UnitOfEnergy.KILO_WATT_HOUR


async def test_ignores_unknown_unavailable_and_nonnumeric(hass: HomeAssistant) -> None:
    """Invalid source states are ignored without changing total."""
    await _setup_entry(hass)
    _, energy_entity_id = _entity_ids(hass)

    hass.states.async_set("sensor.src_energy", 11, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR})
    await hass.async_block_till_done()
    assert _state_float(hass, energy_entity_id) == 1.0

    for value in (STATE_UNKNOWN, STATE_UNAVAILABLE, "none", "abc"):
        hass.states.async_set(
            "sensor.src_energy",
            value,
            {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
        )
        await hass.async_block_till_done()
        energy_state = hass.states.get(energy_entity_id)
        assert energy_state is not None
        if value == STATE_UNAVAILABLE:
            assert energy_state.state == STATE_UNAVAILABLE
        else:
            assert float(energy_state.state) == 1.0

    # Missing source entity should also set unavailable without reducing total.
    hass.states.async_remove("sensor.src_energy")
    await hass.async_block_till_done()
    missing_state = hass.states.get(energy_entity_id)
    assert missing_state is not None
    assert missing_state.state == STATE_UNAVAILABLE

    hass.states.async_set("sensor.src_energy", 12, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR})
    await hass.async_block_till_done()
    assert _state_float(hass, energy_entity_id) == 2.0


async def test_unload_cleans_up_listeners(hass: HomeAssistant) -> None:
    """After unload, source updates no longer affect virtual entities."""
    entry = await _setup_entry(hass)
    power_entity_id, energy_entity_id = _entity_ids(hass)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("sensor.src_power", 999, {"unit_of_measurement": UnitOfPower.WATT})
    hass.states.async_set("sensor.src_energy", 999, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR})
    await hass.async_block_till_done()

    power_state = hass.states.get(power_entity_id)
    energy_state = hass.states.get(energy_entity_id)
    assert power_state is not None
    assert energy_state is not None
    assert power_state.state == STATE_UNAVAILABLE
    assert energy_state.state == STATE_UNAVAILABLE


async def test_energy_sensor_statistics_metadata_and_monotonicity(hass: HomeAssistant) -> None:
    """Energy sensor exposes statistics-friendly metadata and never decreases."""
    await _setup_entry(hass)
    _, energy_entity_id = _entity_ids(hass)

    energy_state = hass.states.get(energy_entity_id)
    assert energy_state is not None
    assert energy_state.attributes["state_class"] == "total_increasing"
    assert energy_state.attributes["device_class"] == "energy"
    assert energy_state.attributes["unit_of_measurement"] == UnitOfEnergy.KILO_WATT_HOUR

    readings = [11, 12, 1.0, 1.1, 0.1, 0.2, 0.19, 0.3]
    expected = []
    last = 10.0
    total = 0.0
    for value in readings:
        delta = value - last
        if delta > 0:
            total += delta
        last = value
        expected.append(total)

    for value, total_value in zip(readings, expected, strict=True):
        hass.states.async_set(
            "sensor.src_energy",
            value,
            {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
        )
        await hass.async_block_till_done()
        assert _state_float(hass, energy_entity_id) == pytest.approx(total_value, abs=1e-6)


async def test_entity_names_include_consumer_name(hass: HomeAssistant) -> None:
    """Entity names include configured consumer name."""
    await _setup_entry(hass)
    power_entity_id, energy_entity_id = _entity_ids(hass)

    power_state = hass.states.get(power_entity_id)
    energy_state = hass.states.get(energy_entity_id)
    assert power_state is not None
    assert energy_state is not None
    assert power_state.name == "Kuhlschrank Power"
    assert energy_state.name == "Kuhlschrank Energy"
