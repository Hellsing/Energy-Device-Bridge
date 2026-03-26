"""Tests for source history replay/import plumbing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from unittest.mock import AsyncMock, patch

from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_device_bridge.const import (
    CONF_CONSUMER_NAME,
    CONF_CONSUMER_UUID,
    CONF_SOURCE_ENERGY_ENTITY_ID,
    CONF_SOURCE_POWER_ENTITY_ID,
    DOMAIN,
    SERVICE_IMPORT_SOURCE_HISTORY,
)
from custom_components.energy_device_bridge.history_import import async_request_history_import


@dataclass
class _MockHistoricalState:
    state: str
    attributes: dict
    last_updated: datetime
    last_changed: datetime


@pytest.mark.asyncio
async def test_create_time_copy_is_scheduled_when_enabled(hass: HomeAssistant) -> None:
    """New entries schedule one-time create-time import when enabled."""
    hass.states.async_set(
        "sensor.src_energy",
        10,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-import-create",
            CONF_CONSUMER_NAME: "Import Create",
            CONF_SOURCE_POWER_ENTITY_ID: None,
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.src_energy",
        },
        options={},
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.energy_device_bridge.history_import.async_request_history_import",
        new=AsyncMock(return_value=True),
    ) as request_mock:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert request_mock.call_count >= 1
        assert request_mock.call_args.kwargs["trigger"] == "create"


@pytest.mark.asyncio
async def test_button_and_service_share_import_entrypoint(hass: HomeAssistant) -> None:
    """Manual import button and service call same internal request function."""
    hass.states.async_set(
        "sensor.src_energy",
        10,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-import-path",
            CONF_CONSUMER_NAME: "Import Path",
            CONF_SOURCE_POWER_ENTITY_ID: None,
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.src_energy",
        },
        options={"copy_source_history_on_create": False},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    energy_entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "consumer-import-path_energy"
    )
    import_button_id = entity_registry.async_get_entity_id(
        "button", DOMAIN, "consumer-import-path_import_source_history"
    )
    assert energy_entity_id is not None
    assert import_button_id is not None

    with (
        patch(
            "custom_components.energy_device_bridge.button.async_request_history_import",
            new=AsyncMock(return_value=True),
        ) as button_request_mock,
        patch(
            "custom_components.energy_device_bridge.async_request_history_import",
            new=AsyncMock(return_value=True),
        ) as service_request_mock,
    ):
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": import_button_id},
            blocking=True,
        )
        await hass.services.async_call(
            DOMAIN,
            SERVICE_IMPORT_SOURCE_HISTORY,
            {"entity_id": energy_entity_id},
            blocking=True,
        )
        assert button_request_mock.call_count == 1
        assert service_request_mock.call_count == 1
        assert button_request_mock.call_args.kwargs["trigger"] == "button"
        assert service_request_mock.call_args.kwargs["trigger"] == "service"


@pytest.mark.asyncio
async def test_history_import_uses_recorder_helper_apis(hass: HomeAssistant) -> None:
    """History import pipeline uses recorder helper APIs."""
    hass.states.async_set(
        "sensor.src_energy",
        10,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-import-api",
            CONF_CONSUMER_NAME: "Import APIs",
            CONF_SOURCE_POWER_ENTITY_ID: None,
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.src_energy",
        },
        options={"copy_source_history_on_create": False},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    source_states = [
        _MockHistoricalState(
            state="1.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=dt_util.as_utc(datetime(2024, 1, 1, 1, 10)),
            last_changed=dt_util.as_utc(datetime(2024, 1, 1, 1, 10)),
        ),
        _MockHistoricalState(
            state="2.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=dt_util.as_utc(datetime(2024, 1, 1, 2, 10)),
            last_changed=dt_util.as_utc(datetime(2024, 1, 1, 2, 10)),
        ),
    ]

    with (
        patch(
            "custom_components.energy_device_bridge.history_import._get_last_statistics",
            return_value={},
        ) as last_stats_mock,
        patch(
            "custom_components.energy_device_bridge.history_import._state_changes_during_period",
            return_value={"sensor.src_energy": source_states},
        ) as history_mock,
        patch(
            "custom_components.energy_device_bridge.history_import._async_import_statistics"
        ) as import_stats_mock,
    ):
        accepted = await async_request_history_import(
            hass, entry=entry, trigger="service", reject_if_running=True
        )
        assert accepted
        await hass.async_block_till_done()
        assert last_stats_mock.called
        assert history_mock.called
        assert import_stats_mock.called
