"""Tests for Energy Device Bridge config and reconfigure flows."""

from __future__ import annotations

import json
from pathlib import Path

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from homeassistant.util.unit_conversion import EnergyConverter
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_device_bridge.const import (
    CONF_CONSUMER_NAME,
    CONF_CONSUMER_UUID,
    CONF_COPY_SOURCE_HISTORY_ON_CREATE,
    CONF_NOTIFY_ON_LOWER_NON_ZERO,
    CONF_SOURCE_ENERGY_ENTITY_ID,
    CONF_SOURCE_POWER_ENTITY_ID,
    CONF_ZERO_DROP_POLICY,
    DEFAULT_COPY_SOURCE_HISTORY_ON_CREATE,
    DEFAULT_NOTIFY_ON_LOWER_NON_ZERO,
    DEFAULT_ZERO_DROP_POLICY,
    DOMAIN,
    ZERO_DROP_POLICY_IGNORE_ZERO_UNTIL_NON_ZERO,
)


async def _init_integration(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "sensor", {})


@pytest.mark.asyncio
async def test_config_flow_happy_path(hass: HomeAssistant) -> None:
    """A consumer can be created from the UI flow."""
    await _init_integration(hass)
    hass.states.async_set(
        "sensor.test_power",
        130.0,
        {"unit_of_measurement": UnitOfPower.WATT},
    )
    hass.states.async_set(
        "sensor.test_energy",
        10.5,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            CONF_CONSUMER_NAME: "Kuhlschrank",
            CONF_SOURCE_POWER_ENTITY_ID: "sensor.test_power",
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.test_energy",
            CONF_ZERO_DROP_POLICY: ZERO_DROP_POLICY_IGNORE_ZERO_UNTIL_NON_ZERO,
            CONF_NOTIFY_ON_LOWER_NON_ZERO: True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Kuhlschrank"
    assert result["data"][CONF_CONSUMER_UUID]
    assert result["options"][CONF_ZERO_DROP_POLICY] == ZERO_DROP_POLICY_IGNORE_ZERO_UNTIL_NON_ZERO
    assert result["options"][CONF_NOTIFY_ON_LOWER_NON_ZERO] is True
    assert result["options"][CONF_COPY_SOURCE_HISTORY_ON_CREATE] is True


@pytest.mark.asyncio
async def test_config_flow_accepts_missing_power_source(hass: HomeAssistant) -> None:
    """A consumer can be created without a power source."""
    await _init_integration(hass)
    hass.states.async_set(
        "sensor.test_energy",
        10.5,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            CONF_CONSUMER_NAME: "Only Energy",
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.test_energy",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SOURCE_POWER_ENTITY_ID] is None
    assert result["options"][CONF_ZERO_DROP_POLICY] == DEFAULT_ZERO_DROP_POLICY
    assert result["options"][CONF_NOTIFY_ON_LOWER_NON_ZERO] == DEFAULT_NOTIFY_ON_LOWER_NON_ZERO
    assert (
        result["options"][CONF_COPY_SOURCE_HISTORY_ON_CREATE]
        == DEFAULT_COPY_SOURCE_HISTORY_ON_CREATE
    )


@pytest.mark.asyncio
async def test_duplicate_pair_is_blocked(hass: HomeAssistant) -> None:
    """Flow prevents duplicate power/energy pairs."""
    await _init_integration(hass)
    hass.states.async_set(
        "sensor.p1",
        100.0,
        {"unit_of_measurement": UnitOfPower.WATT},
    )
    hass.states.async_set(
        "sensor.e1",
        1.0,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )

    first = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            CONF_CONSUMER_NAME: "A",
            CONF_SOURCE_POWER_ENTITY_ID: "sensor.p1",
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.e1",
        },
    )
    assert first["type"] is FlowResultType.CREATE_ENTRY

    second = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            CONF_CONSUMER_NAME: "B",
            CONF_SOURCE_POWER_ENTITY_ID: "sensor.p1",
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.e1",
        },
    )
    assert second["type"] is FlowResultType.FORM
    assert second["errors"]["base"] == "duplicate_pair"


@pytest.mark.asyncio
async def test_reconfigure_flow_updates_data(hass: HomeAssistant) -> None:
    """Reconfigure updates editable fields and reloads entry."""
    await _init_integration(hass)
    hass.states.async_set("sensor.p1", 120, {"unit_of_measurement": UnitOfPower.WATT})
    hass.states.async_set("sensor.e1", 2, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR})
    hass.states.async_set("sensor.p2", 220, {"unit_of_measurement": UnitOfPower.WATT})
    hass.states.async_set("sensor.e2", 3, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR})

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-1",
            CONF_CONSUMER_NAME: "Initial",
            CONF_SOURCE_POWER_ENTITY_ID: "sensor.p1",
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.e1",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    start = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )
    assert start["type"] is FlowResultType.FORM
    assert start["step_id"] == "reconfigure"

    done = await hass.config_entries.flow.async_configure(
        start["flow_id"],
        {
            CONF_CONSUMER_NAME: "Updated",
            CONF_SOURCE_POWER_ENTITY_ID: "sensor.p2",
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.e2",
        },
    )
    assert done["type"] is FlowResultType.ABORT
    assert done["reason"] == "reconfigure_successful"
    assert entry.data[CONF_CONSUMER_UUID] == "consumer-1"
    assert entry.data[CONF_CONSUMER_NAME] == "Updated"
    assert entry.data[CONF_SOURCE_ENERGY_ENTITY_ID] == "sensor.e2"


@pytest.mark.asyncio
async def test_options_flow_updates_entry_from_gear_path(hass: HomeAssistant) -> None:
    """Options flow updates config entry data via the gear menu path."""
    await _init_integration(hass)
    hass.states.async_set("sensor.p1", 120, {"unit_of_measurement": UnitOfPower.WATT})
    hass.states.async_set("sensor.e1", 2, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR})
    hass.states.async_set("sensor.e2", 3, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR})

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-1",
            CONF_CONSUMER_NAME: "Initial",
            CONF_SOURCE_POWER_ENTITY_ID: "sensor.p1",
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.e1",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    start = await hass.config_entries.options.async_init(entry.entry_id)
    assert start["type"] is FlowResultType.FORM
    assert start["step_id"] == "init"

    done = await hass.config_entries.options.async_configure(
        start["flow_id"],
        {
            CONF_CONSUMER_NAME: "Updated via options",
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.e2",
            CONF_ZERO_DROP_POLICY: ZERO_DROP_POLICY_IGNORE_ZERO_UNTIL_NON_ZERO,
            CONF_NOTIFY_ON_LOWER_NON_ZERO: True,
            CONF_COPY_SOURCE_HISTORY_ON_CREATE: True,
        },
    )
    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_CONSUMER_UUID] == "consumer-1"
    assert entry.data[CONF_CONSUMER_NAME] == "Updated via options"
    assert entry.data[CONF_SOURCE_POWER_ENTITY_ID] is None
    assert entry.data[CONF_SOURCE_ENERGY_ENTITY_ID] == "sensor.e2"
    assert entry.options[CONF_ZERO_DROP_POLICY] == ZERO_DROP_POLICY_IGNORE_ZERO_UNTIL_NON_ZERO
    assert entry.options[CONF_NOTIFY_ON_LOWER_NON_ZERO] is True
    assert entry.options[CONF_COPY_SOURCE_HISTORY_ON_CREATE] is True


@pytest.mark.asyncio
async def test_existing_entry_defaults_for_new_options(hass: HomeAssistant) -> None:
    """Existing entries with no options still use compatibility defaults."""
    await _init_integration(hass)
    hass.states.async_set(
        "sensor.e1",
        1,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-legacy",
            CONF_CONSUMER_NAME: "Legacy",
            CONF_SOURCE_POWER_ENTITY_ID: None,
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.e1",
        },
        options={},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.options.get(CONF_ZERO_DROP_POLICY, DEFAULT_ZERO_DROP_POLICY) == DEFAULT_ZERO_DROP_POLICY
    assert (
        entry.options.get(CONF_NOTIFY_ON_LOWER_NON_ZERO, DEFAULT_NOTIFY_ON_LOWER_NON_ZERO)
        == DEFAULT_NOTIFY_ON_LOWER_NON_ZERO
    )
    assert (
        entry.options.get(
            CONF_COPY_SOURCE_HISTORY_ON_CREATE, DEFAULT_COPY_SOURCE_HISTORY_ON_CREATE
        )
        == DEFAULT_COPY_SOURCE_HISTORY_ON_CREATE
    )


@pytest.mark.asyncio
async def test_config_flow_rejects_invalid_entities(hass: HomeAssistant) -> None:
    """Flow rejects invalid source entity combinations."""
    await _init_integration(hass)
    hass.states.async_set("sensor.good_power", 5, {"unit_of_measurement": UnitOfPower.WATT})
    hass.states.async_set("sensor.bad_energy", "nope", {"unit_of_measurement": "widgets"})

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            CONF_CONSUMER_NAME: "Invalid",
            CONF_SOURCE_POWER_ENTITY_ID: "sensor.good_power",
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.bad_energy",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"][CONF_SOURCE_ENERGY_ENTITY_ID] in {
        "energy_not_numeric",
        "invalid_energy_unit",
    }


@pytest.mark.asyncio
async def test_config_flow_rejects_non_wh_kwh_and_non_w_kw_units(hass: HomeAssistant) -> None:
    """Flow enforces source units to W/kW and Wh/kWh only."""
    await _init_integration(hass)
    hass.states.async_set("sensor.bad_power", 5, {"unit_of_measurement": "MW"})
    hass.states.async_set("sensor.bad_energy", 2, {"unit_of_measurement": "MWh"})

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            CONF_CONSUMER_NAME: "Invalid Units",
            CONF_SOURCE_POWER_ENTITY_ID: "sensor.bad_power",
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.bad_energy",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"][CONF_SOURCE_POWER_ENTITY_ID] == "invalid_power_unit"
    assert result["errors"][CONF_SOURCE_ENERGY_ENTITY_ID] == "invalid_energy_unit"


@pytest.mark.asyncio
async def test_config_flow_accepts_w_and_wh_units(hass: HomeAssistant) -> None:
    """Flow accepts W for power and Wh for energy."""
    await _init_integration(hass)
    hass.states.async_set("sensor.src_power", 500, {"unit_of_measurement": UnitOfPower.WATT})
    hass.states.async_set("sensor.src_energy", 5000, {"unit_of_measurement": UnitOfEnergy.WATT_HOUR})

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            CONF_CONSUMER_NAME: "Valid Units",
            CONF_SOURCE_POWER_ENTITY_ID: "sensor.src_power",
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.src_energy",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_config_flow_accepts_generic_sensor_without_device_class(
    hass: HomeAssistant,
) -> None:
    """Flow accepts plain sensor entities when units and values are valid."""
    await _init_integration(hass)
    hass.states.async_set("sensor.generic_power", 300, {"unit_of_measurement": UnitOfPower.WATT})
    hass.states.async_set(
        "sensor.generic_energy",
        3,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            CONF_CONSUMER_NAME: "Generic",
            CONF_SOURCE_POWER_ENTITY_ID: "sensor.generic_power",
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.generic_energy",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


def test_translation_files_and_runtime_localization_contract() -> None:
    """Ensure translation files carry flow and entity keys for custom components."""
    base = Path("custom_components/energy_device_bridge")
    assert not (base / "strings.json").exists()

    en = json.loads((base / "translations" / "en.json").read_text(encoding="utf-8"))
    de = json.loads((base / "translations" / "de.json").read_text(encoding="utf-8"))

    for lang in (en, de):
        assert "config" in lang
        assert "options" in lang
        assert "entity" in lang
        assert lang["config"]["step"]["user"]["data"]["consumer_name"]
        assert lang["config"]["step"]["user"]["data"]["zero_drop_policy"]
        assert lang["config"]["step"]["user"]["data"]["notify_on_lower_non_zero"]
        assert lang["config"]["step"]["user"]["data"]["copy_source_history_on_create"]
        assert lang["config"]["step"]["reconfigure"]["data"]["source_energy_entity_id"]
        assert lang["config"]["abort"]["reconfigure_successful"]
        assert lang["options"]["step"]["init"]["data"]["source_energy_entity_id"]
        assert lang["options"]["step"]["init"]["data"]["zero_drop_policy"]
        assert lang["options"]["step"]["init"]["data"]["notify_on_lower_non_zero"]
        assert lang["options"]["step"]["init"]["data"]["copy_source_history_on_create"]
        assert lang["selector"]["zero_drop_policy"]["options"]["ignore_zero_until_non_zero"]
        assert lang["entity"]["sensor"]["power"]["name"]
        assert lang["entity"]["sensor"]["energy"]["name"]
        assert lang["entity"]["button"]["adopt_current_source_as_baseline"]["name"]
        assert lang["entity"]["button"]["reset_tracker"]["name"]
        assert lang["entity"]["button"]["import_source_history"]["name"]


@pytest.mark.asyncio
async def test_user_flow_sets_unique_id(hass: HomeAssistant) -> None:
    """Created config entries have a stable unique_id."""
    await _init_integration(hass)
    hass.states.async_set("sensor.test_power", 130, {"unit_of_measurement": UnitOfPower.WATT})
    hass.states.async_set(
        "sensor.test_energy",
        EnergyConverter.convert(7500, UnitOfEnergy.WATT_HOUR, UnitOfEnergy.KILO_WATT_HOUR),
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            CONF_CONSUMER_NAME: "Unique",
            CONF_SOURCE_POWER_ENTITY_ID: "sensor.test_power",
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.test_energy",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == result["data"][CONF_CONSUMER_UUID]
