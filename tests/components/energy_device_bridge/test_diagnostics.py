"""Tests for Energy Device Bridge diagnostics."""

from __future__ import annotations

from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_device_bridge.const import (
    CONF_CONSUMER_NAME,
    CONF_COPY_SOURCE_HISTORY_ON_CREATE,
    CONF_CONSUMER_UUID,
    CONF_NOTIFY_ON_LOWER_NON_ZERO,
    CONF_SOURCE_ENERGY_ENTITY_ID,
    CONF_SOURCE_POWER_ENTITY_ID,
    CONF_ZERO_DROP_POLICY,
    ZERO_DROP_POLICY_IGNORE_ZERO_UNTIL_NON_ZERO,
    DOMAIN,
)
from custom_components.energy_device_bridge.diagnostics import (
    async_get_config_entry_diagnostics,
)


@pytest.mark.asyncio
async def test_config_entry_diagnostics_redacts_sensitive_fields(
    hass: HomeAssistant,
) -> None:
    """Diagnostics redact user-specific and source entity details."""
    hass.states.async_set(
        "sensor.src_power", 400, {"unit_of_measurement": UnitOfPower.WATT}
    )
    hass.states.async_set(
        "sensor.src_energy",
        15,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-diag",
            CONF_CONSUMER_NAME: "Kitchen",
            CONF_SOURCE_POWER_ENTITY_ID: "sensor.src_power",
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.src_energy",
        },
        options={
            CONF_ZERO_DROP_POLICY: ZERO_DROP_POLICY_IGNORE_ZERO_UNTIL_NON_ZERO,
            CONF_NOTIFY_ON_LOWER_NON_ZERO: True,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["entry"]["data"][CONF_CONSUMER_NAME] == "**REDACTED**"
    assert diagnostics["entry"]["data"][CONF_SOURCE_POWER_ENTITY_ID] == "**REDACTED**"
    assert diagnostics["entry"]["data"][CONF_SOURCE_ENERGY_ENTITY_ID] == "**REDACTED**"
    assert diagnostics["stored_state"]["last_source_entity_id"] == "**REDACTED**"
    assert "runtime" in diagnostics
    assert "ignored_negative_delta_count" in diagnostics["runtime"]
    assert (
        diagnostics["entry"]["options"][CONF_ZERO_DROP_POLICY]
        == ZERO_DROP_POLICY_IGNORE_ZERO_UNTIL_NON_ZERO
    )
    assert diagnostics["entry"]["options"][CONF_NOTIFY_ON_LOWER_NON_ZERO] is True
    assert (
        diagnostics["runtime"][CONF_COPY_SOURCE_HISTORY_ON_CREATE] is True
        or diagnostics["entry"]["options"].get(CONF_COPY_SOURCE_HISTORY_ON_CREATE, True)
        is True
    )
