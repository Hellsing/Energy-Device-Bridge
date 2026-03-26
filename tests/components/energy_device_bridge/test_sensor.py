"""Tests for Energy Device Bridge sensors."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.const import (
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_device_bridge.const import (
    ATTR_AWAITING_NON_ZERO_AFTER_ZERO_DROP,
    ATTR_LAST_SOURCE_ENERGY_VALUE_KWH,
    ATTR_LOWER_VALUE_COUNT,
    ATTR_VALUE_KWH,
    CONF_CONSUMER_NAME,
    CONF_CONSUMER_UUID,
    CONF_COPY_SOURCE_HISTORY_ON_CREATE,
    CONF_COPY_SOURCE_HISTORY_ON_CREATE_PENDING,
    CONF_NOTIFY_ON_LOWER_NON_ZERO,
    CONF_SOURCE_ENERGY_ENTITY_ID,
    CONF_SOURCE_POWER_ENTITY_ID,
    CONF_ZERO_DROP_POLICY,
    DEFAULT_NOTIFY_ON_LOWER_NON_ZERO,
    DEFAULT_ZERO_DROP_POLICY,
    DOMAIN,
    ISSUE_ENERGY_STATE_CLASS_INVALID,
    ISSUE_ENERGY_UNIT_UNSUPPORTED,
    ISSUE_SOURCE_ENTITY_MISSING,
    SERVICE_ADOPT_CURRENT_SOURCE_AS_BASELINE,
    SERVICE_RESET_TRACKER,
    SERVICE_SET_VIRTUAL_TOTAL,
    ZERO_DROP_POLICY_ACCEPT_ZERO_AS_NEW_CYCLE,
    ZERO_DROP_POLICY_IGNORE_ZERO_UNTIL_NON_ZERO,
)

pytestmark = pytest.mark.asyncio


def _state_float(hass: HomeAssistant, entity_id: str) -> float:
    state = hass.states.get(entity_id)
    assert state is not None
    return float(state.state)


async def _setup_entry(
    hass: HomeAssistant,
    *,
    source_energy_entity_id: str = "sensor.src_energy",
    options: dict | None = None,
) -> MockConfigEntry:
    hass.states.async_set(
        "sensor.src_power", 100, {"unit_of_measurement": UnitOfPower.WATT}
    )
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
        options=options
        or {
            CONF_ZERO_DROP_POLICY: DEFAULT_ZERO_DROP_POLICY,
            CONF_NOTIFY_ON_LOWER_NON_ZERO: DEFAULT_NOTIFY_ON_LOWER_NON_ZERO,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entity_ids(hass: HomeAssistant) -> tuple[str, str]:
    entity_registry = er.async_get(hass)
    power_entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "consumer-1_power"
    )
    energy_entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "consumer-1_energy"
    )
    assert power_entity_id is not None
    assert energy_entity_id is not None
    return power_entity_id, energy_entity_id


async def test_power_sensor_passthrough_behavior(hass: HomeAssistant) -> None:
    """Power sensor mirrors source state and converts to kW."""
    await _setup_entry(hass)
    power_entity_id, _ = _entity_ids(hass)

    assert _state_float(hass, power_entity_id) == 0.1
    hass.states.async_set(
        "sensor.src_power", 250, {"unit_of_measurement": UnitOfPower.WATT}
    )
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


async def test_energy_sensor_accepts_wh_and_converts_to_kwh(
    hass: HomeAssistant,
) -> None:
    """Energy source in Wh is normalized to kWh."""
    await _setup_entry(hass)
    _, energy_entity_id = _entity_ids(hass)

    hass.states.async_set(
        "sensor.src_energy", 11000, {"unit_of_measurement": UnitOfEnergy.WATT_HOUR}
    )
    await hass.async_block_till_done()
    assert _state_float(hass, energy_entity_id) == 1.0


async def test_source_replacement_after_entry_update(hass: HomeAssistant) -> None:
    """After source entity replacement, first sample is baseline only."""
    await _setup_entry(hass)
    _, energy_entity_id = _entity_ids(hass)

    hass.states.async_set(
        "sensor.src_energy", 11, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR}
    )
    await hass.async_block_till_done()
    assert _state_float(hass, energy_entity_id) == 1.0

    hass.states.async_set(
        "sensor.new_energy", 100, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR}
    )
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

    hass.states.async_set(
        "sensor.src_energy", 11, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR}
    )
    await hass.async_block_till_done()
    assert _state_float(hass, energy_entity_id) == 1.0

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    # Baseline should be 11 after reload, so +1 increments total from 1 to 2.
    hass.states.async_set(
        "sensor.src_energy", 12, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR}
    )
    await hass.async_block_till_done()
    assert _state_float(hass, energy_entity_id) == 2.0
    restored_state = hass.states.get(energy_entity_id)
    assert restored_state is not None
    assert restored_state.attributes["state_class"] == "total_increasing"
    assert restored_state.attributes["device_class"] == "energy"
    assert (
        restored_state.attributes["unit_of_measurement"] == UnitOfEnergy.KILO_WATT_HOUR
    )


async def test_ignores_unknown_unavailable_and_nonnumeric(hass: HomeAssistant) -> None:
    """Invalid source states are ignored without changing total."""
    await _setup_entry(hass)
    _, energy_entity_id = _entity_ids(hass)

    hass.states.async_set(
        "sensor.src_energy", 11, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR}
    )
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

    hass.states.async_set(
        "sensor.src_energy", 12, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR}
    )
    await hass.async_block_till_done()
    assert _state_float(hass, energy_entity_id) == 2.0


async def test_unload_cleans_up_listeners(hass: HomeAssistant) -> None:
    """After unload, source updates no longer affect virtual entities."""
    entry = await _setup_entry(hass)
    power_entity_id, energy_entity_id = _entity_ids(hass)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set(
        "sensor.src_power", 999, {"unit_of_measurement": UnitOfPower.WATT}
    )
    hass.states.async_set(
        "sensor.src_energy", 999, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR}
    )
    await hass.async_block_till_done()

    power_state = hass.states.get(power_entity_id)
    energy_state = hass.states.get(energy_entity_id)
    assert power_state is not None
    assert energy_state is not None
    assert power_state.state == STATE_UNAVAILABLE
    assert energy_state.state == STATE_UNAVAILABLE


async def test_energy_sensor_statistics_metadata_and_monotonicity(
    hass: HomeAssistant,
) -> None:
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
        assert _state_float(hass, energy_entity_id) == pytest.approx(
            total_value, abs=1e-6
        )


async def test_entity_names_are_translation_backed(hass: HomeAssistant) -> None:
    """Entity names use translated suffixes instead of hard-coded full names."""
    await _setup_entry(hass)
    power_entity_id, energy_entity_id = _entity_ids(hass)

    power_state = hass.states.get(power_entity_id)
    energy_state = hass.states.get(energy_entity_id)
    assert power_state is not None
    assert energy_state is not None
    assert power_state.name.endswith("Power")
    assert energy_state.name.endswith("Energy")


async def test_setup_without_power_source_only_creates_energy_sensor(
    hass: HomeAssistant,
) -> None:
    """Power sensor entity is not created when power source is omitted."""
    hass.states.async_set(
        "sensor.src_energy",
        10,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-no-power",
            CONF_CONSUMER_NAME: "Only Energy",
            CONF_SOURCE_POWER_ENTITY_ID: None,
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.src_energy",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    power_entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "consumer-no-power_power"
    )
    energy_entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "consumer-no-power_energy"
    )
    assert power_entity_id is None
    assert energy_entity_id is not None


async def test_energy_sensor_unavailable_while_initial_history_import_pending(
    hass: HomeAssistant,
) -> None:
    """Creation-time import pending keeps bridge sensor unavailable."""
    hass.states.async_set(
        "sensor.src_energy",
        10,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMER_UUID: "consumer-pending-import",
            CONF_CONSUMER_NAME: "Pending Import",
            CONF_SOURCE_POWER_ENTITY_ID: None,
            CONF_SOURCE_ENERGY_ENTITY_ID: "sensor.src_energy",
        },
        options={
            CONF_COPY_SOURCE_HISTORY_ON_CREATE: True,
            CONF_COPY_SOURCE_HISTORY_ON_CREATE_PENDING: True,
            CONF_ZERO_DROP_POLICY: DEFAULT_ZERO_DROP_POLICY,
            CONF_NOTIFY_ON_LOWER_NON_ZERO: DEFAULT_NOTIFY_ON_LOWER_NON_ZERO,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    energy_entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "consumer-pending-import_energy"
    )
    assert energy_entity_id is not None
    energy_state = hass.states.get(energy_entity_id)
    assert energy_state is not None
    assert energy_state.state == STATE_UNAVAILABLE


async def test_adopt_current_source_as_baseline_does_not_change_virtual_total(
    hass: HomeAssistant,
) -> None:
    """Adopting baseline stores current source value without changing total."""
    await _setup_entry(hass)
    _, energy_entity_id = _entity_ids(hass)

    hass.states.async_set(
        "sensor.src_energy", 11, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR}
    )
    await hass.async_block_till_done()
    assert _state_float(hass, energy_entity_id) == 1.0

    await hass.services.async_call(
        DOMAIN,
        SERVICE_ADOPT_CURRENT_SOURCE_AS_BASELINE,
        {"entity_id": [energy_entity_id]},
        blocking=True,
    )
    await hass.async_block_till_done()

    state = hass.states.get(energy_entity_id)
    assert state is not None
    assert float(state.state) == 1.0
    assert float(state.attributes[ATTR_LAST_SOURCE_ENERGY_VALUE_KWH]) == 11.0

    hass.states.async_set(
        "sensor.src_energy", 12, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR}
    )
    await hass.async_block_till_done()
    assert _state_float(hass, energy_entity_id) == 2.0


async def test_reset_tracker_clears_tracker_and_resets_total(
    hass: HomeAssistant,
) -> None:
    """Reset tracker clears baseline metadata and sets virtual total to zero."""
    await _setup_entry(hass)
    _, energy_entity_id = _entity_ids(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    hass.states.async_set(
        "sensor.src_energy", 11, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR}
    )
    await hass.async_block_till_done()
    assert _state_float(hass, energy_entity_id) == 1.0

    await hass.services.async_call(
        DOMAIN,
        SERVICE_RESET_TRACKER,
        {"entity_id": [energy_entity_id]},
        blocking=True,
    )
    await hass.async_block_till_done()
    state = hass.states.get(energy_entity_id)
    assert state is not None
    assert float(state.state) == 0.0
    assert state.attributes[ATTR_LAST_SOURCE_ENERGY_VALUE_KWH] is None

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    reloaded = hass.states.get(energy_entity_id)
    assert reloaded is not None
    assert float(reloaded.state) == 0.0


async def test_set_virtual_total_persists_and_restores(hass: HomeAssistant) -> None:
    """Set virtual total action persists and restores after reload."""
    await _setup_entry(hass)
    _, energy_entity_id = _entity_ids(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_VIRTUAL_TOTAL,
        {"entity_id": [energy_entity_id], ATTR_VALUE_KWH: 7.25},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert _state_float(hass, energy_entity_id) == 7.25

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _state_float(hass, energy_entity_id) == 7.25


async def test_energy_sensor_uses_delayed_save_not_one_task_per_update(
    hass: HomeAssistant,
) -> None:
    """Rapid updates coalesce through delayed save path."""
    await _setup_entry(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    with patch.object(entry.runtime_data.store, "async_schedule_save") as schedule_save:
        for value in (11, 12, 13, 14):
            hass.states.async_set(
                "sensor.src_energy",
                value,
                {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            )
            await hass.async_block_till_done()
        assert schedule_save.call_count >= 1
        assert schedule_save.call_count <= 4


async def test_runtime_repairs_created_and_dismissed(hass: HomeAssistant) -> None:
    """Repairs are created for invalid source states and dismissed when fixed."""
    await _setup_entry(hass)
    _, energy_entity_id = _entity_ids(hass)
    issue_registry = ir.async_get(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    issue_prefix = entry.data[CONF_CONSUMER_UUID]

    hass.states.async_remove("sensor.src_energy")
    await hass.async_block_till_done()
    assert (
        issue_registry.async_get_issue(
            DOMAIN, f"{issue_prefix}_{ISSUE_SOURCE_ENTITY_MISSING}"
        )
        is not None
    )

    hass.states.async_set(
        "sensor.src_energy",
        10,
        {
            "unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
            "state_class": "measurement",
        },
    )
    await hass.async_block_till_done()
    assert (
        issue_registry.async_get_issue(
            DOMAIN, f"{issue_prefix}_{ISSUE_SOURCE_ENTITY_MISSING}"
        )
        is None
    )
    assert (
        issue_registry.async_get_issue(
            DOMAIN, f"{issue_prefix}_{ISSUE_ENERGY_STATE_CLASS_INVALID}"
        )
        is not None
    )

    hass.states.async_set(
        "sensor.src_energy",
        10,
        {"unit_of_measurement": "MWh", "state_class": "total"},
    )
    await hass.async_block_till_done()
    assert (
        issue_registry.async_get_issue(
            DOMAIN, f"{issue_prefix}_{ISSUE_ENERGY_UNIT_UNSUPPORTED}"
        )
        is not None
    )

    hass.states.async_set(
        "sensor.src_energy",
        10,
        {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR, "state_class": "total"},
    )
    await hass.async_block_till_done()
    assert (
        issue_registry.async_get_issue(
            DOMAIN, f"{issue_prefix}_{ISSUE_ENERGY_STATE_CLASS_INVALID}"
        )
        is None
    )
    assert (
        issue_registry.async_get_issue(
            DOMAIN, f"{issue_prefix}_{ISSUE_ENERGY_UNIT_UNSUPPORTED}"
        )
        is None
    )
    assert hass.states.get(energy_entity_id) is not None


async def test_zero_drop_policy_accept_zero_as_new_cycle(hass: HomeAssistant) -> None:
    """When configured, zero drop becomes baseline immediately."""
    await _setup_entry(
        hass,
        options={
            CONF_ZERO_DROP_POLICY: ZERO_DROP_POLICY_ACCEPT_ZERO_AS_NEW_CYCLE,
            CONF_NOTIFY_ON_LOWER_NON_ZERO: False,
        },
    )
    _, energy_entity_id = _entity_ids(hass)

    hass.states.async_set(
        "sensor.src_energy", 12, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR}
    )
    await hass.async_block_till_done()
    assert _state_float(hass, energy_entity_id) == 2.0

    hass.states.async_set(
        "sensor.src_energy", 0, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR}
    )
    await hass.async_block_till_done()
    state = hass.states.get(energy_entity_id)
    assert state is not None
    assert float(state.state) == 2.0
    assert float(state.attributes[ATTR_LAST_SOURCE_ENERGY_VALUE_KWH]) == 0.0

    hass.states.async_set(
        "sensor.src_energy", 0.7, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR}
    )
    await hass.async_block_till_done()
    assert _state_float(hass, energy_entity_id) == 2.7


async def test_zero_drop_policy_ignore_zero_until_non_zero(hass: HomeAssistant) -> None:
    """Zero reading is ignored until first non-zero, then adopted as baseline only."""
    await _setup_entry(
        hass,
        options={
            CONF_ZERO_DROP_POLICY: ZERO_DROP_POLICY_IGNORE_ZERO_UNTIL_NON_ZERO,
            CONF_NOTIFY_ON_LOWER_NON_ZERO: False,
        },
    )
    _, energy_entity_id = _entity_ids(hass)

    hass.states.async_set(
        "sensor.src_energy", 15, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR}
    )
    await hass.async_block_till_done()
    assert _state_float(hass, energy_entity_id) == 5.0

    hass.states.async_set(
        "sensor.src_energy", 0, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR}
    )
    await hass.async_block_till_done()
    state = hass.states.get(energy_entity_id)
    assert state is not None
    assert float(state.state) == 5.0
    assert state.attributes[ATTR_AWAITING_NON_ZERO_AFTER_ZERO_DROP] is True
    assert float(state.attributes[ATTR_LAST_SOURCE_ENERGY_VALUE_KWH]) == 15.0

    # Repeated zeros are ignored while awaiting first non-zero.
    hass.states.async_set(
        "sensor.src_energy", 0, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR}
    )
    await hass.async_block_till_done()
    state = hass.states.get(energy_entity_id)
    assert state is not None
    assert float(state.state) == 5.0
    assert state.attributes[ATTR_AWAITING_NON_ZERO_AFTER_ZERO_DROP] is True

    # First non-zero becomes baseline only (no artificial +delta from 15 -> 0.8).
    hass.states.async_set(
        "sensor.src_energy", 0.8, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR}
    )
    await hass.async_block_till_done()
    state = hass.states.get(energy_entity_id)
    assert state is not None
    assert float(state.state) == 5.0
    assert state.attributes[ATTR_AWAITING_NON_ZERO_AFTER_ZERO_DROP] is False
    assert float(state.attributes[ATTR_LAST_SOURCE_ENERGY_VALUE_KWH]) == 0.8

    hass.states.async_set(
        "sensor.src_energy", 1.0, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR}
    )
    await hass.async_block_till_done()
    assert _state_float(hass, energy_entity_id) == 5.2


async def test_lower_non_zero_adopts_baseline_and_optional_notification(
    hass: HomeAssistant,
) -> None:
    """Lower non-zero readings adopt baseline and optionally notify deterministically."""
    await _setup_entry(
        hass,
        options={
            CONF_ZERO_DROP_POLICY: ZERO_DROP_POLICY_ACCEPT_ZERO_AS_NEW_CYCLE,
            CONF_NOTIFY_ON_LOWER_NON_ZERO: True,
        },
    )
    _, energy_entity_id = _entity_ids(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    issue_registry = ir.async_get(hass)
    issue_prefix = entry.data[CONF_CONSUMER_UUID]

    hass.states.async_set(
        "sensor.src_energy", 20, {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR}
    )
    await hass.async_block_till_done()
    assert _state_float(hass, energy_entity_id) == 10.0

    with patch(
        "custom_components.energy_device_bridge.sensor.persistent_notification.async_create"
    ) as notify_mock:
        hass.states.async_set(
            "sensor.src_energy",
            3.5,
            {"unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
        )
        await hass.async_block_till_done()
        assert notify_mock.call_count == 1
        assert notify_mock.call_args.kwargs["notification_id"] == (
            f"{DOMAIN}_{entry.entry_id}_lower_non_zero"
        )

    state = hass.states.get(energy_entity_id)
    assert state is not None
    assert float(state.state) == 10.0
    assert float(state.attributes[ATTR_LAST_SOURCE_ENERGY_VALUE_KWH]) == 3.5
    assert int(state.attributes[ATTR_LOWER_VALUE_COUNT]) >= 1
    assert (
        issue_registry.async_get_issue(
            DOMAIN, f"{issue_prefix}_{ISSUE_SOURCE_ENTITY_MISSING}"
        )
        is None
    )
    assert (
        issue_registry.async_get_issue(
            DOMAIN, f"{issue_prefix}_{ISSUE_ENERGY_STATE_CLASS_INVALID}"
        )
        is None
    )
    assert (
        issue_registry.async_get_issue(
            DOMAIN, f"{issue_prefix}_{ISSUE_ENERGY_UNIT_UNSUPPORTED}"
        )
        is None
    )
