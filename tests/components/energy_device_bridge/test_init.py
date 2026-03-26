"""Core setup and lifecycle tests for Energy Device Bridge."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_device_bridge import (
    _async_purge_entity_history,
    async_remove_config_entry_device,
)
from custom_components.energy_device_bridge.const import (
    CONF_CONSUMER_NAME,
    CONF_CONSUMER_UUID,
    CONF_SOURCE_ENERGY_ENTITY_ID,
    CONF_SOURCE_POWER_ENTITY_ID,
    DOMAIN,
    SERVICE_ADOPT_CURRENT_SOURCE_AS_BASELINE,
    SERVICE_CLEANUP_RECORDER_DATA,
    SERVICE_IMPORT_SOURCE_HISTORY,
    SERVICE_RESET_TRACKER,
    SERVICE_SET_VIRTUAL_TOTAL,
)


def test_manifest_basics() -> None:
    """Manifest has required standalone custom integration metadata."""
    manifest = json.loads(
        Path("custom_components/energy_device_bridge/manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["domain"] == DOMAIN
    assert manifest["name"] == "Energy Device Bridge"
    assert manifest["config_flow"] is True
    assert manifest["integration_type"] == "device"
    assert manifest["iot_class"] == "calculated"
    assert manifest["version"]


@pytest.mark.asyncio
async def test_import_service_registered(hass) -> None:
    """Integration-level services are registered on setup."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    assert hass.services.has_service(DOMAIN, SERVICE_ADOPT_CURRENT_SOURCE_AS_BASELINE)
    assert hass.services.has_service(DOMAIN, SERVICE_RESET_TRACKER)
    assert hass.services.has_service(DOMAIN, SERVICE_SET_VIRTUAL_TOTAL)
    assert hass.services.has_service(DOMAIN, SERVICE_IMPORT_SOURCE_HISTORY)
    assert hass.services.has_service(DOMAIN, SERVICE_CLEANUP_RECORDER_DATA)


@pytest.mark.asyncio
async def test_setup_unload_and_remove_entry(hass) -> None:
    """Entry sets up entities, unloads cleanly, and removes storage file."""
    hass.states.async_set(
        "sensor.power_source", 100, {"unit_of_measurement": UnitOfPower.WATT}
    )
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

    with patch(
        "custom_components.energy_device_bridge._async_cleanup_recorder_for_entry"
    ) as clear_stats_mock:
        assert await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
        assert clear_stats_mock.called


@pytest.mark.asyncio
async def test_delete_device_removes_config_entry_and_child_entities(hass) -> None:
    """Removing the bridge device removes the owning config entry and entities."""
    hass.states.async_set(
        "sensor.power_source", 100, {"unit_of_measurement": UnitOfPower.WATT}
    )
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


@pytest.mark.asyncio
async def test_cleanup_recorder_for_entry_clears_history_and_statistics(hass) -> None:
    """Removing an entry clears recorder rows and statistics for owned entities."""
    hass.states.async_set(
        "sensor.energy_source",
        10,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-recorder-cleanup",
            CONF_CONSUMER_NAME: "Recorder Cleanup",
            CONF_SOURCE_POWER_ENTITY_ID: None,
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.energy_source",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    purge_history = AsyncMock()
    clear_statistics = Mock()
    with patch(
        "custom_components.energy_device_bridge._async_purge_entity_history",
        purge_history,
    ), patch(
        "custom_components.energy_device_bridge._get_recorder_instance",
        return_value=Mock(
            async_clear_statistics=clear_statistics,
        ),
    ):
        assert await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()

    purge_history.assert_awaited_once()
    cleared_entity_ids = purge_history.call_args.args[1]
    assert purge_history.call_args.kwargs["wait_for_completion"] is False
    assert any(
        entity_id.startswith("sensor.recorder_cleanup")
        for entity_id in cleared_entity_ids
    )

    clear_statistics.assert_called_once()
    cleared_statistic_ids = clear_statistics.call_args.args[0]
    assert all(
        statistic_id.startswith("sensor.") for statistic_id in cleared_statistic_ids
    )
    assert any(
        statistic_id.startswith("sensor.recorder_cleanup")
        for statistic_id in cleared_statistic_ids
    )


@pytest.mark.asyncio
async def test_cleanup_recorder_data_service_purges_entities_and_statistics(
    hass,
) -> None:
    """Maintenance service purges history and statistics for selected entities."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    purge_history = AsyncMock()
    clear_statistics = Mock()
    with patch(
        "custom_components.energy_device_bridge._async_purge_entity_history",
        purge_history,
    ), patch(
        "custom_components.energy_device_bridge._get_recorder_instance",
        return_value=Mock(async_clear_statistics=clear_statistics),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CLEANUP_RECORDER_DATA,
            {
                "entity_id": [
                    "sensor.legacy_bridge_energy",
                    "button.legacy_bridge_reset",
                ]
            },
            blocking=True,
        )

    purge_history.assert_awaited_once_with(
        hass,
        ["sensor.legacy_bridge_energy", "button.legacy_bridge_reset"],
        wait_for_completion=True,
    )
    clear_statistics.assert_called_once_with(["sensor.legacy_bridge_energy"])


@pytest.mark.asyncio
async def test_async_purge_entity_history_calls_recorder_service(hass) -> None:
    """Recorder purge_entities is invoked with keep_days=0 for full history removal."""
    purge_handler = AsyncMock()
    hass.services.async_register("recorder", "purge_entities", purge_handler)

    await _async_purge_entity_history(
        hass,
        ["sensor.bridge_old_energy", "button.bridge_old_reset"],
        wait_for_completion=True,
    )

    purge_handler.assert_awaited_once()
    data = purge_handler.call_args.args[0].data
    assert data["entity_id"] == ["sensor.bridge_old_energy", "button.bridge_old_reset"]
    assert data["keep_days"] == 0


@pytest.mark.asyncio
async def test_delete_device_rejects_unrelated_device(hass) -> None:
    """Device removal callback returns False for non-bridge devices."""
    hass.states.async_set(
        "sensor.energy_source",
        10,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-unrelated",
            CONF_CONSUMER_NAME: "Unrelated Device",
            CONF_SOURCE_POWER_ENTITY_ID: None,
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.energy_source",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    other_entry = MockConfigEntry(
        domain="test_domain",
        data={},
    )
    other_entry.add_to_hass(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=other_entry.entry_id,
        identifiers={("test_domain", "test-device")},
    )
    assert not await async_remove_config_entry_device(hass, entry, device)
