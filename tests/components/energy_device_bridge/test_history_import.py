"""Tests for source history replay/import plumbing."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_device_bridge.const import (
    CONF_COPY_SOURCE_HISTORY_ON_CREATE,
    CONF_COPY_SOURCE_HISTORY_ON_CREATE_PENDING,
    CONF_CONSUMER_NAME,
    CONF_CONSUMER_UUID,
    ATTR_HISTORY_IMPORT_LAST_SOURCE_ENERGY_VALUE_KWH,
    ATTR_HISTORY_IMPORT_LAST_SOURCE_ENTITY_ID,
    ATTR_HISTORY_IMPORT_LAST_SOURCE_SAMPLE_TS,
    CONF_ZERO_DROP_POLICY,
    CONF_SOURCE_ENERGY_ENTITY_ID,
    CONF_SOURCE_POWER_ENTITY_ID,
    DOMAIN,
    SERVICE_IMPORT_SOURCE_HISTORY,
    ZERO_DROP_POLICY_IGNORE_ZERO_UNTIL_NON_ZERO,
)
from custom_components.energy_device_bridge.history_import import (
    _build_failure_notification_message,
    _build_success_notification_message,
    async_request_history_import,
)
from custom_components.energy_device_bridge.models import EnergyTrackerState


@dataclass
class _MockHistoricalState:
    state: str
    attributes: dict
    last_updated: datetime
    last_changed: datetime


def test_success_notification_includes_hourly_and_short_term_rows() -> None:
    """Success notification should summarize all imported row types."""
    message = _build_success_notification_message(
        trigger="service",
        source_entity_id="sensor.source_energy",
        bridge_entity_id="sensor.bridge_energy",
        period_start_iso=dt_util.as_utc(datetime(2024, 1, 1, 9, 0)).isoformat(),
        period_end_iso=dt_util.as_utc(datetime(2024, 1, 1, 10, 0)).isoformat(),
        sample_count=12,
        hourly_rows_imported=2,
        short_term_rows_imported=8,
        short_term_rows_skipped=0,
        retention_limited=False,
    )
    assert "Hourly rows imported:** 2" in message
    assert "5-minute rows imported:** 8" in message
    assert "Total rows imported:** 10" in message
    assert "Retention limited:** no" in message


def test_success_notification_reports_skipped_short_term_rows() -> None:
    """Success notification should explain skipped short-term rows."""
    message = _build_success_notification_message(
        trigger="service",
        source_entity_id="sensor.source_energy",
        bridge_entity_id="sensor.bridge_energy",
        period_start_iso=None,
        period_end_iso=None,
        sample_count=6,
        hourly_rows_imported=1,
        short_term_rows_imported=0,
        short_term_rows_skipped=4,
        retention_limited=True,
    )
    assert "5-minute rows imported:** 0 (4 skipped: unsupported recorder short-term import)" in message
    assert "Total rows imported:** 1" in message
    assert "Retention limited:** yes" in message


def test_failure_notification_is_structured_and_actionable() -> None:
    """Failure notification should include entities, error, and next step."""
    message = _build_failure_notification_message(
        source_entity_id="sensor.source_energy",
        bridge_entity_id=None,
        error=RuntimeError("boom"),
    )
    assert "Energy history import failed" in message
    assert "Source entity:** `sensor.source_energy`" in message
    assert "Bridge entity:** `unavailable`" in message
    assert "Error:** `boom`" in message
    assert "Review Home Assistant logs for full diagnostics." in message


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
        options={
            CONF_COPY_SOURCE_HISTORY_ON_CREATE: True,
            CONF_COPY_SOURCE_HISTORY_ON_CREATE_PENDING: True,
        },
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
            "custom_components.energy_device_bridge.button.async_start_history_import",
            new=AsyncMock(return_value=True),
        ) as button_request_mock,
        patch(
            "custom_components.energy_device_bridge.async_start_history_import",
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
            {"config_entry_id": entry.entry_id},
            blocking=True,
        )
        assert button_request_mock.call_count == 1
        assert service_request_mock.call_count == 1
        assert button_request_mock.call_args.kwargs["trigger"] == "button"
        assert (
            button_request_mock.call_args.kwargs["reinitialize_before_import"] is True
        )
        assert service_request_mock.call_args.kwargs["trigger"] == "service"
        assert (
            service_request_mock.call_args.kwargs["reinitialize_before_import"] is False
        )


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
            "custom_components.energy_device_bridge.history_import._async_clear_statistics_and_wait",
            new=AsyncMock(),
        ) as clear_stats_mock,
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
        assert not last_stats_mock.called
        assert clear_stats_mock.called
        assert history_mock.called
        assert import_stats_mock.called


@pytest.mark.asyncio
async def test_first_import_replays_from_full_available_history(
    hass: HomeAssistant,
) -> None:
    """First import must not anchor to existing bridge statistics window."""
    hass.states.async_set(
        "sensor.src_energy",
        10,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-import-full-history",
            CONF_CONSUMER_NAME: "Import Full History",
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
            last_updated=dt_util.as_utc(datetime(2024, 1, 1, 1, 20)),
            last_changed=dt_util.as_utc(datetime(2024, 1, 1, 1, 20)),
        ),
    ]
    with (
        patch(
            "custom_components.energy_device_bridge.history_import._async_clear_statistics_and_wait",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._state_changes_during_period",
            return_value={"sensor.src_energy": source_states},
        ) as history_mock,
        patch(
            "custom_components.energy_device_bridge.history_import._async_import_statistics"
        ) as import_stats_mock,
    ):
        accepted = await async_request_history_import(
            hass, entry=entry, trigger="button", reject_if_running=True
        )
        assert accepted
        await hass.async_block_till_done()
        assert history_mock.called
        assert import_stats_mock.called
        assert history_mock.call_args.args[1].year == 1970


@pytest.mark.asyncio
async def test_import_excludes_current_hour_rows(hass: HomeAssistant) -> None:
    """Import should not write statistics for the in-progress current hour."""
    hass.states.async_set(
        "sensor.src_energy",
        10,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-import-hour-boundary",
            CONF_CONSUMER_NAME: "Import Hour Boundary",
            CONF_SOURCE_POWER_ENTITY_ID: None,
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.src_energy",
        },
        options={"copy_source_history_on_create": False},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    now = dt_util.utcnow()
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    previous_hour = current_hour - timedelta(hours=1)
    source_states = [
        _MockHistoricalState(
            state="1.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=previous_hour + timedelta(minutes=10),
            last_changed=previous_hour + timedelta(minutes=10),
        ),
        _MockHistoricalState(
            state="1.5",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=previous_hour + timedelta(minutes=30),
            last_changed=previous_hour + timedelta(minutes=30),
        ),
        _MockHistoricalState(
            state="2.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=current_hour + timedelta(minutes=10),
            last_changed=current_hour + timedelta(minutes=10),
        ),
    ]

    with (
        patch(
            "custom_components.energy_device_bridge.history_import._async_clear_statistics_and_wait",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._state_changes_during_period",
            return_value={"sensor.src_energy": source_states},
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._async_import_statistics"
        ) as import_stats_mock,
    ):
        accepted = await async_request_history_import(
            hass, entry=entry, trigger="button", reject_if_running=True
        )
        assert accepted
        await hass.async_block_till_done()
        assert import_stats_mock.called
        rows = import_stats_mock.call_args.args[2]
        assert len(rows) == 1
        assert rows[0]["start"] == previous_hour


@pytest.mark.asyncio
async def test_import_keeps_only_first_leading_zero_row(hass: HomeAssistant) -> None:
    """Keep first leading zero row, drop additional zero-only prefix rows."""
    hass.states.async_set(
        "sensor.src_energy",
        10,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-import-no-leading-zero",
            CONF_CONSUMER_NAME: "Import No Leading Zero",
            CONF_SOURCE_POWER_ENTITY_ID: None,
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.src_energy",
        },
        options={"copy_source_history_on_create": False},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    hour_a = now - timedelta(hours=3)
    hour_b = now - timedelta(hours=2)
    hour_c = now - timedelta(hours=1)
    source_states = [
        _MockHistoricalState(
            state="100.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=hour_a + timedelta(minutes=5),
            last_changed=hour_a + timedelta(minutes=5),
        ),
        _MockHistoricalState(
            state="100.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=hour_b + timedelta(minutes=5),
            last_changed=hour_b + timedelta(minutes=5),
        ),
        _MockHistoricalState(
            state="101.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=hour_c + timedelta(minutes=5),
            last_changed=hour_c + timedelta(minutes=5),
        ),
    ]

    with (
        patch(
            "custom_components.energy_device_bridge.history_import._async_clear_statistics_and_wait",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._state_changes_during_period",
            return_value={"sensor.src_energy": source_states},
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._async_import_statistics"
        ) as import_stats_mock,
    ):
        accepted = await async_request_history_import(
            hass, entry=entry, trigger="button", reject_if_running=True
        )
        assert accepted
        await hass.async_block_till_done()
        rows = import_stats_mock.call_args.args[2]
        assert len(rows) == 2
        assert rows[0]["start"] == hour_a
        assert rows[0]["sum"] == 0.0
        assert rows[1]["start"] == hour_c
        assert rows[1]["sum"] == 1.0


@pytest.mark.asyncio
async def test_copy_on_create_not_invoked_again_after_reload(
    hass: HomeAssistant,
) -> None:
    """Create-time copy should run once and never re-run on restart/reload."""
    hass.states.async_set(
        "sensor.src_energy",
        10,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-import-once",
            CONF_CONSUMER_NAME: "Import Once",
            CONF_SOURCE_POWER_ENTITY_ID: None,
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.src_energy",
        },
        options={
            CONF_COPY_SOURCE_HISTORY_ON_CREATE: True,
            CONF_COPY_SOURCE_HISTORY_ON_CREATE_PENDING: True,
        },
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.energy_device_bridge.history_import.async_request_history_import",
        new=AsyncMock(return_value=True),
    ) as request_mock:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert request_mock.call_count >= 1

        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert request_mock.call_count == 1


@pytest.mark.asyncio
async def test_successful_import_clears_creation_pending_flag(
    hass: HomeAssistant,
) -> None:
    """Successful import clears pending flag so sensor can become available."""
    hass.states.async_set(
        "sensor.src_energy",
        10,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-import-clear-pending",
            CONF_CONSUMER_NAME: "Import Clear Pending",
            CONF_SOURCE_POWER_ENTITY_ID: None,
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.src_energy",
        },
        options={
            CONF_COPY_SOURCE_HISTORY_ON_CREATE: True,
            CONF_COPY_SOURCE_HISTORY_ON_CREATE_PENDING: True,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    source_states = [
        _MockHistoricalState(
            state="1.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=now - timedelta(hours=2, minutes=10),
            last_changed=now - timedelta(hours=2, minutes=10),
        ),
        _MockHistoricalState(
            state="2.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=now - timedelta(hours=1, minutes=10),
            last_changed=now - timedelta(hours=1, minutes=10),
        ),
    ]
    with (
        patch(
            "custom_components.energy_device_bridge.history_import._async_clear_statistics_and_wait",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._state_changes_during_period",
            return_value={"sensor.src_energy": source_states},
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._async_import_statistics"
        ),
    ):
        accepted = await async_request_history_import(
            hass, entry=entry, trigger="service", reject_if_running=True
        )
        assert accepted
        await hass.async_block_till_done()
        assert (
            entry.options.get(CONF_COPY_SOURCE_HISTORY_ON_CREATE_PENDING, True) is False
        )


@pytest.mark.asyncio
async def test_successful_import_with_no_rows_clears_creation_pending_flag(
    hass: HomeAssistant,
) -> None:
    """Empty import windows still clear pending create-time import state."""
    hass.states.async_set(
        "sensor.src_energy",
        10,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-import-empty-window",
            CONF_CONSUMER_NAME: "Import Empty Window",
            CONF_SOURCE_POWER_ENTITY_ID: None,
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.src_energy",
        },
        options={
            CONF_COPY_SOURCE_HISTORY_ON_CREATE: True,
            CONF_COPY_SOURCE_HISTORY_ON_CREATE_PENDING: True,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    with (
        patch(
            "custom_components.energy_device_bridge.history_import._async_clear_statistics_and_wait",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._state_changes_during_period",
            return_value={"sensor.src_energy": []},
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._async_import_statistics"
        ),
    ):
        accepted = await async_request_history_import(
            hass, entry=entry, trigger="service", reject_if_running=True
        )
        assert accepted
        await hass.async_block_till_done()

    assert entry.options.get(CONF_COPY_SOURCE_HISTORY_ON_CREATE_PENDING, True) is False


@pytest.mark.asyncio
async def test_failed_import_clears_creation_pending_flag(
    hass: HomeAssistant,
) -> None:
    """Failed imports clear pending create-time import to avoid stuck unavailable."""
    hass.states.async_set(
        "sensor.src_energy",
        10,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-import-fail-clears-pending",
            CONF_CONSUMER_NAME: "Import Fail Clears Pending",
            CONF_SOURCE_POWER_ENTITY_ID: None,
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.src_energy",
        },
        options={
            CONF_COPY_SOURCE_HISTORY_ON_CREATE: True,
            CONF_COPY_SOURCE_HISTORY_ON_CREATE_PENDING: True,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    with patch(
        "custom_components.energy_device_bridge.history_import._state_changes_during_period",
        side_effect=RuntimeError("boom"),
    ):
        accepted = await async_request_history_import(
            hass, entry=entry, trigger="service", reject_if_running=True
        )
        assert accepted
        await hass.async_block_till_done()

    tracker = await entry.runtime_data.store.async_load()
    assert tracker is not None
    assert tracker.history_import_last_result == "failed"
    assert entry.options.get(CONF_COPY_SOURCE_HISTORY_ON_CREATE_PENDING, True) is False
    energy_sensor = entry.runtime_data.energy_sensor
    assert energy_sensor is not None
    assert energy_sensor._tracker.history_import_in_progress is False
    entity_registry = er.async_get(hass)
    energy_entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "consumer-import-fail-clears-pending_energy"
    )
    assert energy_entity_id is not None
    state = hass.states.get(energy_entity_id)
    assert state is not None
    assert state.state != STATE_UNAVAILABLE


@pytest.mark.asyncio
async def test_cancelled_import_clears_creation_pending_flag(
    hass: HomeAssistant,
) -> None:
    """Cancelled imports clear pending create-time import and tracker in-progress flag."""
    hass.states.async_set(
        "sensor.src_energy",
        10,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-import-cancel-clears-pending",
            CONF_CONSUMER_NAME: "Import Cancel Clears Pending",
            CONF_SOURCE_POWER_ENTITY_ID: None,
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.src_energy",
        },
        options={
            CONF_COPY_SOURCE_HISTORY_ON_CREATE: True,
            CONF_COPY_SOURCE_HISTORY_ON_CREATE_PENDING: True,
        },
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.energy_device_bridge.async_schedule_copy_on_create",
        new=AsyncMock(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    load_gate = asyncio.Event()

    async def _blocked_load():
        await load_gate.wait()
        return EnergyTrackerState()

    with patch.object(entry.runtime_data.store, "async_load", side_effect=_blocked_load):
        accepted = await async_request_history_import(
            hass, entry=entry, trigger="service", reject_if_running=True
        )
        assert accepted
        task = entry.runtime_data.history_import_task
        assert task is not None
        await asyncio.sleep(0)
        task.cancel()
        load_gate.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await hass.async_block_till_done()

    tracker = await entry.runtime_data.store.async_load()
    assert tracker is not None
    assert tracker.history_import_in_progress is False
    assert tracker.history_import_last_result == "cancelled"
    assert entry.options.get(CONF_COPY_SOURCE_HISTORY_ON_CREATE_PENDING, True) is False
    energy_sensor = entry.runtime_data.energy_sensor
    assert energy_sensor is not None
    assert energy_sensor._tracker.history_import_in_progress is False
    entity_registry = er.async_get(hass)
    energy_entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "consumer-import-cancel-clears-pending_energy"
    )
    assert energy_entity_id is not None
    state = hass.states.get(energy_entity_id)
    assert state is not None
    assert state.state != STATE_UNAVAILABLE


@pytest.mark.asyncio
async def test_import_backfills_short_term_statistics_for_recent_buckets(
    hass: HomeAssistant,
) -> None:
    """Import writes 5-minute rows up to the current incomplete bucket."""
    hass.states.async_set(
        "sensor.src_energy",
        102.2,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-import-short-term",
            CONF_CONSUMER_NAME: "Import Short Term",
            CONF_SOURCE_POWER_ENTITY_ID: None,
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.src_energy",
        },
        options={"copy_source_history_on_create": False},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    now = dt_util.as_utc(datetime(2024, 1, 1, 10, 23))
    source_states = [
        _MockHistoricalState(
            state="100.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=dt_util.as_utc(datetime(2024, 1, 1, 9, 40)),
            last_changed=dt_util.as_utc(datetime(2024, 1, 1, 9, 40)),
        ),
        _MockHistoricalState(
            state="100.5",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=dt_util.as_utc(datetime(2024, 1, 1, 10, 2)),
            last_changed=dt_util.as_utc(datetime(2024, 1, 1, 10, 2)),
        ),
        _MockHistoricalState(
            state="101.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=dt_util.as_utc(datetime(2024, 1, 1, 10, 7)),
            last_changed=dt_util.as_utc(datetime(2024, 1, 1, 10, 7)),
        ),
        _MockHistoricalState(
            state="102.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=dt_util.as_utc(datetime(2024, 1, 1, 10, 16)),
            last_changed=dt_util.as_utc(datetime(2024, 1, 1, 10, 16)),
        ),
        _MockHistoricalState(
            state="102.2",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=dt_util.as_utc(datetime(2024, 1, 1, 10, 21)),
            last_changed=dt_util.as_utc(datetime(2024, 1, 1, 10, 21)),
        ),
    ]

    with (
        patch(
            "custom_components.energy_device_bridge.history_import.dt_util.utcnow",
            return_value=now,
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._async_clear_statistics_and_wait",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._state_changes_during_period",
            return_value={"sensor.src_energy": source_states},
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._async_import_statistics"
        ) as import_hourly_mock,
        patch(
            "custom_components.energy_device_bridge.history_import._async_import_short_term_statistics",
            return_value=True,
        ) as import_short_mock,
    ):
        accepted = await async_request_history_import(
            hass, entry=entry, trigger="service", reject_if_running=True
        )
        assert accepted
        await hass.async_block_till_done()

        hourly_rows = import_hourly_mock.call_args.args[2]
        assert len(hourly_rows) == 1
        assert hourly_rows[0]["start"] == dt_util.as_utc(datetime(2024, 1, 1, 9, 0))
        assert hourly_rows[0]["sum"] == 0.0

        short_rows = import_short_mock.call_args.args[2]
        assert [row["start"] for row in short_rows] == [
            dt_util.as_utc(datetime(2024, 1, 1, 9, 40)),
            dt_util.as_utc(datetime(2024, 1, 1, 10, 0)),
            dt_util.as_utc(datetime(2024, 1, 1, 10, 5)),
            dt_util.as_utc(datetime(2024, 1, 1, 10, 15)),
        ]
        assert short_rows[-1]["sum"] == 2.0
        assert all(
            row["start"] != dt_util.as_utc(datetime(2024, 1, 1, 10, 20))
            for row in short_rows
        )


@pytest.mark.asyncio
async def test_first_live_update_after_import_is_continuous_with_large_source_value(
    hass: HomeAssistant,
) -> None:
    """Post-import live update continues from imported total without jump."""
    hass.states.async_set(
        "sensor.src_energy",
        15000.0,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-import-live-continuity",
            CONF_CONSUMER_NAME: "Import Live Continuity",
            CONF_SOURCE_POWER_ENTITY_ID: None,
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.src_energy",
        },
        options={"copy_source_history_on_create": False},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    now = dt_util.as_utc(datetime(2024, 1, 1, 10, 23))
    source_states = [
        _MockHistoricalState(
            state="14990.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=dt_util.as_utc(datetime(2024, 1, 1, 8, 10)),
            last_changed=dt_util.as_utc(datetime(2024, 1, 1, 8, 10)),
        ),
        _MockHistoricalState(
            state="14995.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=dt_util.as_utc(datetime(2024, 1, 1, 9, 10)),
            last_changed=dt_util.as_utc(datetime(2024, 1, 1, 9, 10)),
        ),
        _MockHistoricalState(
            state="14996.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=dt_util.as_utc(datetime(2024, 1, 1, 10, 5)),
            last_changed=dt_util.as_utc(datetime(2024, 1, 1, 10, 5)),
        ),
    ]

    with (
        patch(
            "custom_components.energy_device_bridge.history_import.dt_util.utcnow",
            return_value=now,
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._async_clear_statistics_and_wait",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._state_changes_during_period",
            return_value={"sensor.src_energy": source_states},
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._async_import_statistics"
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._async_import_short_term_statistics",
            return_value=True,
        ),
    ):
        accepted = await async_request_history_import(
            hass, entry=entry, trigger="service", reject_if_running=True
        )
        assert accepted
        await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    energy_entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "consumer-import-live-continuity_energy"
    )
    assert energy_entity_id is not None

    energy_state = hass.states.get(energy_entity_id)
    assert energy_state is not None
    assert float(energy_state.state) == pytest.approx(6.0, abs=1e-6)

    hass.states.async_set(
        "sensor.src_energy",
        15000.75,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    await hass.async_block_till_done()
    energy_state = hass.states.get(energy_entity_id)
    assert energy_state is not None
    assert float(energy_state.state) == pytest.approx(6.75, abs=1e-6)


@pytest.mark.asyncio
async def test_incremental_import_preserves_delta_from_previous_source_baseline(
    hass: HomeAssistant,
) -> None:
    """Incremental import keeps continuity from last imported source reading."""
    hass.states.async_set(
        "sensor.src_energy",
        112.0,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-import-incremental-continuity",
            CONF_CONSUMER_NAME: "Import Incremental Continuity",
            CONF_SOURCE_POWER_ENTITY_ID: None,
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.src_energy",
        },
        options={"copy_source_history_on_create": False},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    now = dt_util.as_utc(datetime(2024, 1, 1, 12, 23))
    initial_states = [
        _MockHistoricalState(
            state="100.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=dt_util.as_utc(datetime(2024, 1, 1, 9, 10)),
            last_changed=dt_util.as_utc(datetime(2024, 1, 1, 9, 10)),
        ),
        _MockHistoricalState(
            state="110.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=dt_util.as_utc(datetime(2024, 1, 1, 10, 10)),
            last_changed=dt_util.as_utc(datetime(2024, 1, 1, 10, 10)),
        ),
    ]
    with (
        patch(
            "custom_components.energy_device_bridge.history_import.dt_util.utcnow",
            return_value=now,
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._async_clear_statistics_and_wait",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._state_changes_during_period",
            return_value={"sensor.src_energy": initial_states},
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._async_import_statistics"
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._async_import_short_term_statistics",
            return_value=True,
        ),
    ):
        accepted = await async_request_history_import(
            hass, entry=entry, trigger="service", reject_if_running=True
        )
        assert accepted
        await hass.async_block_till_done()

    incremental_states = [
        _MockHistoricalState(
            state="111.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=dt_util.as_utc(datetime(2024, 1, 1, 11, 5)),
            last_changed=dt_util.as_utc(datetime(2024, 1, 1, 11, 5)),
        ),
        _MockHistoricalState(
            state="112.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=dt_util.as_utc(datetime(2024, 1, 1, 11, 20)),
            last_changed=dt_util.as_utc(datetime(2024, 1, 1, 11, 20)),
        ),
    ]

    def _mock_last_statistics(
        _hass: HomeAssistant,
        _number_of_stats: int,
        statistic_id: str,
        _convert_units: bool,
        _types: set[str],
    ) -> dict[str, list[dict]]:
        return {statistic_id: [{"sum": 10.0}]}

    with (
        patch(
            "custom_components.energy_device_bridge.history_import.dt_util.utcnow",
            return_value=now,
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._get_last_statistics",
            side_effect=_mock_last_statistics,
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._state_changes_during_period",
            return_value={"sensor.src_energy": incremental_states},
        ) as history_mock,
        patch(
            "custom_components.energy_device_bridge.history_import._last_valid_source_kwh_before"
        ) as legacy_backfill_mock,
        patch(
            "custom_components.energy_device_bridge.history_import._async_import_statistics"
        ) as import_stats_mock,
        patch(
            "custom_components.energy_device_bridge.history_import._async_import_short_term_statistics",
            return_value=True,
        ),
    ):
        accepted = await async_request_history_import(
            hass, entry=entry, trigger="service", reject_if_running=True
        )
        assert accepted
        await hass.async_block_till_done()
        rows = import_stats_mock.call_args.args[2]
        assert len(rows) == 1
        # 10.0 existing total + (111-110) + (112-111) == 12.0
        assert rows[0]["sum"] == pytest.approx(12.0, abs=1e-6)
        assert legacy_backfill_mock.call_count == 0
        assert history_mock.call_count == 1
        assert history_mock.call_args.args[1] == dt_util.as_utc(datetime(2024, 1, 1, 11, 0))


@pytest.mark.asyncio
async def test_incremental_import_legacy_tracker_uses_fallback_once_and_self_heals(
    hass: HomeAssistant,
) -> None:
    """Legacy trackers without persisted replay baseline recover and persist it."""
    hass.states.async_set(
        "sensor.src_energy",
        112.0,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-import-legacy-self-heal",
            CONF_CONSUMER_NAME: "Import Legacy Self Heal",
            CONF_SOURCE_POWER_ENTITY_ID: None,
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.src_energy",
        },
        options={"copy_source_history_on_create": False},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    legacy_tracker = EnergyTrackerState(
        virtual_total_kwh=10.0,
        history_import_has_run=True,
        history_import_last_imported_hour_start=dt_util.as_utc(
            datetime(2024, 1, 1, 10, 0)
        ).isoformat(),
        last_source_entity_id="sensor.src_energy",
    )
    await entry.runtime_data.store.async_save(legacy_tracker)

    now = dt_util.as_utc(datetime(2024, 1, 1, 12, 23))
    incremental_states = [
        _MockHistoricalState(
            state="111.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=dt_util.as_utc(datetime(2024, 1, 1, 11, 5)),
            last_changed=dt_util.as_utc(datetime(2024, 1, 1, 11, 5)),
        ),
        _MockHistoricalState(
            state="112.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=dt_util.as_utc(datetime(2024, 1, 1, 11, 20)),
            last_changed=dt_util.as_utc(datetime(2024, 1, 1, 11, 20)),
        ),
    ]

    def _mock_last_statistics(
        _hass: HomeAssistant,
        _number_of_stats: int,
        statistic_id: str,
        _convert_units: bool,
        _types: set[str],
    ) -> dict[str, list[dict]]:
        return {statistic_id: [{"sum": 10.0}]}

    with (
        patch(
            "custom_components.energy_device_bridge.history_import.dt_util.utcnow",
            return_value=now,
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._get_last_statistics",
            side_effect=_mock_last_statistics,
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._last_valid_source_kwh_before",
            return_value=110.0,
        ) as legacy_backfill_mock,
        patch(
            "custom_components.energy_device_bridge.history_import._state_changes_during_period",
            return_value={"sensor.src_energy": incremental_states},
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._async_import_statistics"
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._async_import_short_term_statistics",
            return_value=True,
        ),
    ):
        accepted = await async_request_history_import(
            hass, entry=entry, trigger="service", reject_if_running=True
        )
        assert accepted
        await hass.async_block_till_done()
        assert legacy_backfill_mock.call_count == 1

    healed_tracker = await entry.runtime_data.store.async_load()
    assert healed_tracker is not None
    assert healed_tracker.history_import_last_source_energy_value_kwh == pytest.approx(
        112.0, abs=1e-6
    )
    assert healed_tracker.history_import_last_source_entity_id == "sensor.src_energy"
    assert healed_tracker.history_import_last_source_sample_ts is not None


@pytest.mark.asyncio
async def test_incremental_import_falls_back_when_persisted_baseline_source_mismatches(
    hass: HomeAssistant,
) -> None:
    """Source replacement should invalidate persisted replay baseline safely."""
    hass.states.async_set(
        "sensor.src_energy",
        112.0,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-import-source-replacement",
            CONF_CONSUMER_NAME: "Import Source Replacement",
            CONF_SOURCE_POWER_ENTITY_ID: None,
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.src_energy",
        },
        options={"copy_source_history_on_create": False},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    tracker_with_mismatched_baseline = EnergyTrackerState(
        virtual_total_kwh=10.0,
        history_import_has_run=True,
        history_import_last_imported_hour_start=dt_util.as_utc(
            datetime(2024, 1, 1, 10, 0)
        ).isoformat(),
        last_source_entity_id="sensor.src_energy",
        history_import_last_source_entity_id="sensor.replaced_source",
        history_import_last_source_energy_value_kwh=110.0,
        history_import_last_source_sample_ts=dt_util.as_utc(
            datetime(2024, 1, 1, 10, 10)
        ).isoformat(),
    )
    await entry.runtime_data.store.async_save(tracker_with_mismatched_baseline)

    now = dt_util.as_utc(datetime(2024, 1, 1, 12, 23))
    incremental_states = [
        _MockHistoricalState(
            state="111.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=dt_util.as_utc(datetime(2024, 1, 1, 11, 5)),
            last_changed=dt_util.as_utc(datetime(2024, 1, 1, 11, 5)),
        ),
        _MockHistoricalState(
            state="112.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=dt_util.as_utc(datetime(2024, 1, 1, 11, 20)),
            last_changed=dt_util.as_utc(datetime(2024, 1, 1, 11, 20)),
        ),
    ]

    def _mock_last_statistics(
        _hass: HomeAssistant,
        _number_of_stats: int,
        statistic_id: str,
        _convert_units: bool,
        _types: set[str],
    ) -> dict[str, list[dict]]:
        return {statistic_id: [{"sum": 10.0}]}

    with (
        patch(
            "custom_components.energy_device_bridge.history_import.dt_util.utcnow",
            return_value=now,
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._get_last_statistics",
            side_effect=_mock_last_statistics,
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._last_valid_source_kwh_before",
            return_value=110.0,
        ) as legacy_backfill_mock,
        patch(
            "custom_components.energy_device_bridge.history_import._state_changes_during_period",
            return_value={"sensor.src_energy": incremental_states},
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._async_import_statistics"
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._async_import_short_term_statistics",
            return_value=True,
        ),
    ):
        accepted = await async_request_history_import(
            hass, entry=entry, trigger="service", reject_if_running=True
        )
        assert accepted
        await hass.async_block_till_done()
        assert legacy_backfill_mock.call_count == 1


@pytest.mark.asyncio
async def test_reset_handling_remains_correct_after_import(
    hass: HomeAssistant,
) -> None:
    """Reset policy behavior remains unchanged after import replay."""
    hass.states.async_set(
        "sensor.src_energy",
        210.0,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-import-reset-policy",
            CONF_CONSUMER_NAME: "Import Reset Policy",
            CONF_SOURCE_POWER_ENTITY_ID: None,
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.src_energy",
        },
        options={
            "copy_source_history_on_create": False,
            CONF_ZERO_DROP_POLICY: ZERO_DROP_POLICY_IGNORE_ZERO_UNTIL_NON_ZERO,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    now = dt_util.as_utc(datetime(2024, 1, 1, 10, 23))
    source_states = [
        _MockHistoricalState(
            state="200.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=dt_util.as_utc(datetime(2024, 1, 1, 8, 10)),
            last_changed=dt_util.as_utc(datetime(2024, 1, 1, 8, 10)),
        ),
        _MockHistoricalState(
            state="205.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=dt_util.as_utc(datetime(2024, 1, 1, 9, 10)),
            last_changed=dt_util.as_utc(datetime(2024, 1, 1, 9, 10)),
        ),
    ]

    with (
        patch(
            "custom_components.energy_device_bridge.history_import.dt_util.utcnow",
            return_value=now,
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._async_clear_statistics_and_wait",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._state_changes_during_period",
            return_value={"sensor.src_energy": source_states},
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._async_import_statistics"
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._async_import_short_term_statistics",
            return_value=True,
        ),
    ):
        accepted = await async_request_history_import(
            hass, entry=entry, trigger="service", reject_if_running=True
        )
        assert accepted
        await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    energy_entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "consumer-import-reset-policy_energy"
    )
    assert energy_entity_id is not None

    hass.states.async_set(
        "sensor.src_energy",
        0,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    await hass.async_block_till_done()
    state = hass.states.get(energy_entity_id)
    assert state is not None
    assert float(state.state) == pytest.approx(5.0, abs=1e-6)
    assert state.attributes["awaiting_non_zero_after_zero_drop"] is True

    hass.states.async_set(
        "sensor.src_energy",
        0.5,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    await hass.async_block_till_done()
    state = hass.states.get(energy_entity_id)
    assert state is not None
    assert float(state.state) == pytest.approx(5.0, abs=1e-6)
    assert state.attributes["awaiting_non_zero_after_zero_drop"] is False

    hass.states.async_set(
        "sensor.src_energy",
        0.8,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    await hass.async_block_till_done()
    state = hass.states.get(energy_entity_id)
    assert state is not None
    assert float(state.state) == pytest.approx(5.3, abs=1e-6)


@pytest.mark.asyncio
async def test_manual_import_reinitializes_tracker_and_purges_recorder_data(
    hass: HomeAssistant,
) -> None:
    """Manual import path resets tracker, purges history, then imports."""
    hass.states.async_set(
        "sensor.src_energy",
        12.0,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-import-reinitialize",
            CONF_CONSUMER_NAME: "Import Reinitialize",
            CONF_SOURCE_POWER_ENTITY_ID: None,
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.src_energy",
        },
        options={"copy_source_history_on_create": False},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    energy_sensor = entry.runtime_data.energy_sensor
    assert energy_sensor is not None
    prepare_mock = AsyncMock()
    energy_sensor.async_prepare_for_manual_history_import = prepare_mock  # type: ignore[method-assign]

    now = dt_util.as_utc(datetime(2024, 1, 1, 10, 23))
    source_states = [
        _MockHistoricalState(
            state="10.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=dt_util.as_utc(datetime(2024, 1, 1, 9, 10)),
            last_changed=dt_util.as_utc(datetime(2024, 1, 1, 9, 10)),
        ),
        _MockHistoricalState(
            state="12.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=dt_util.as_utc(datetime(2024, 1, 1, 10, 10)),
            last_changed=dt_util.as_utc(datetime(2024, 1, 1, 10, 10)),
        ),
    ]

    with (
        patch(
            "custom_components.energy_device_bridge.history_import.dt_util.utcnow",
            return_value=now,
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._async_clear_statistics_and_wait",
            new=AsyncMock(),
        ) as clear_stats_mock,
        patch(
            "custom_components.energy_device_bridge.history_import._state_changes_during_period",
            return_value={"sensor.src_energy": source_states},
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._async_import_statistics"
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._async_import_short_term_statistics",
            return_value=True,
        ),
    ):
        accepted = await async_request_history_import(
            hass,
            entry=entry,
            trigger="button",
            reject_if_running=True,
            reinitialize_before_import=True,
        )
        assert accepted
        await hass.async_block_till_done()

    assert prepare_mock.await_count == 1
    assert clear_stats_mock.await_count >= 1
    tracker = await entry.runtime_data.store.async_load()
    assert tracker is not None
    assert tracker.as_dict()[ATTR_HISTORY_IMPORT_LAST_SOURCE_ENTITY_ID] == "sensor.src_energy"
    assert tracker.as_dict()[
        ATTR_HISTORY_IMPORT_LAST_SOURCE_ENERGY_VALUE_KWH
    ] == pytest.approx(12.0, abs=1e-6)
    assert tracker.as_dict()[ATTR_HISTORY_IMPORT_LAST_SOURCE_SAMPLE_TS] is not None


@pytest.mark.asyncio
async def test_manual_reinitialize_import_blocks_live_tracking_until_completion(
    hass: HomeAssistant,
) -> None:
    """Manual reinitialize import keeps sensor unavailable and ignores live updates."""
    hass.states.async_set(
        "sensor.src_energy",
        10.0,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-import-block-live",
            CONF_CONSUMER_NAME: "Import Block Live",
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
        "sensor", DOMAIN, "consumer-import-block-live_energy"
    )
    assert energy_entity_id is not None
    energy_sensor = entry.runtime_data.energy_sensor
    assert energy_sensor is not None

    now = dt_util.as_utc(datetime(2024, 1, 1, 10, 23))
    source_states = [
        _MockHistoricalState(
            state="9.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=dt_util.as_utc(datetime(2024, 1, 1, 9, 10)),
            last_changed=dt_util.as_utc(datetime(2024, 1, 1, 9, 10)),
        ),
        _MockHistoricalState(
            state="12.0",
            attributes={"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            last_updated=dt_util.as_utc(datetime(2024, 1, 1, 10, 10)),
            last_changed=dt_util.as_utc(datetime(2024, 1, 1, 10, 10)),
        ),
    ]
    import_gate = asyncio.Event()
    original_prepare = energy_sensor.async_prepare_for_manual_history_import

    async def _blocked_prepare() -> None:
        await original_prepare()
        await import_gate.wait()

    with (
        patch(
            "custom_components.energy_device_bridge.history_import.dt_util.utcnow",
            return_value=now,
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._async_clear_statistics_and_wait",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._async_import_statistics"
        ),
        patch(
            "custom_components.energy_device_bridge.history_import._async_import_short_term_statistics",
            return_value=True,
        ),
        patch.object(
            energy_sensor,
            "async_prepare_for_manual_history_import",
            side_effect=_blocked_prepare,
        ),
    ):
        with patch(
            "custom_components.energy_device_bridge.history_import._state_changes_during_period",
            side_effect=lambda *_args, **_kwargs: {"sensor.src_energy": source_states},
        ):
            accepted = await async_request_history_import(
                hass,
                entry=entry,
                trigger="button",
                reject_if_running=True,
                reinitialize_before_import=True,
            )
            assert accepted
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            energy_state = hass.states.get(energy_entity_id)
            assert energy_state is not None
            assert energy_state.state == STATE_UNAVAILABLE
            assert energy_sensor._tracker.history_import_in_progress is True
            assert energy_sensor._tracker.virtual_total_kwh == 0.0

            with patch(
                "custom_components.energy_device_bridge.sensor.apply_source_sample"
            ) as live_apply_mock:
                hass.states.async_set(
                    "sensor.src_energy",
                    0.01,
                    {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
                )
                hass.states.async_set(
                    "sensor.src_energy",
                    0.02,
                    {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
                )
                await hass.async_block_till_done()
                assert live_apply_mock.call_count == 0

            energy_state = hass.states.get(energy_entity_id)
            assert energy_state is not None
            assert energy_state.state == STATE_UNAVAILABLE
            assert energy_sensor._tracker.virtual_total_kwh == 0.0

            import_gate.set()
            await hass.async_block_till_done()

    final_state = hass.states.get(energy_entity_id)
    assert final_state is not None
    assert final_state.state != STATE_UNAVAILABLE
    assert float(final_state.state) == pytest.approx(3.0, abs=1e-6)
    final_sensor = entry.runtime_data.energy_sensor
    assert final_sensor is not None
    assert final_sensor._tracker.history_import_in_progress is False
