"""Historical source replay and statistics import for bridge sensors."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import datetime, timedelta
from time import monotonic
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

from .bridge_logic import apply_source_sample
from .const import (
    DEFAULT_COPY_SOURCE_HISTORY_ON_CREATE,
    DEFAULT_ZERO_DROP_POLICY,
    DOMAIN,
    CONF_COPY_SOURCE_HISTORY_ON_CREATE,
    CONF_COPY_SOURCE_HISTORY_ON_CREATE_INVOKED,
    CONF_COPY_SOURCE_HISTORY_ON_CREATE_PENDING,
    CONF_ZERO_DROP_POLICY,
)
from .models import EnergyTrackerState

_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=dt_util.UTC)


def _get_last_statistics(
    hass: HomeAssistant,
    number_of_stats: int,
    statistic_id: str,
    convert_units: bool,
    types: set[str],
) -> dict[str, list[dict[str, Any]]]:
    from homeassistant.components.recorder.statistics import get_last_statistics

    return get_last_statistics(hass, number_of_stats, statistic_id, convert_units, types)


def _state_changes_during_period(
    hass: HomeAssistant,
    start_time: datetime,
    end_time: datetime,
    entity_id: str,
) -> dict[str, list[State]]:
    from homeassistant.components.recorder import history

    return history.state_changes_during_period(
        hass,
        start_time,
        end_time,
        entity_id,
        False,
        False,
        None,
        True,
    )


def _async_import_statistics(
    hass: HomeAssistant,
    metadata: dict[str, Any],
    statistics: list[dict[str, Any]],
) -> None:
    from homeassistant.components.recorder.statistics import async_import_statistics

    async_import_statistics(hass, metadata, statistics)


def _async_clear_statistics(hass: HomeAssistant, statistic_ids: list[str]) -> None:
    from homeassistant.components.recorder import get_instance

    get_instance(hass).async_clear_statistics(statistic_ids)


def _supports_statistics_metadata_field(field_name: str) -> bool:
    """Return whether recorder metadata model supports a field."""
    try:
        from homeassistant.components.recorder.db_schema import StatisticsMeta
    except Exception:  # noqa: BLE001 - import safety across HA versions
        return False
    return hasattr(StatisticsMeta, field_name)


def _build_statistics_metadata(
    *,
    name: str,
    statistic_id: str,
) -> dict[str, Any]:
    """Build metadata compatible with multiple Home Assistant versions."""
    metadata: dict[str, Any] = {
        "has_mean": False,
        "has_sum": True,
        "name": name,
        "source": "recorder",
        "statistic_id": statistic_id,
        "unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
    }
    if _supports_statistics_metadata_field("mean_type"):
        mean_type_none: Any = "none"
        try:
            from homeassistant.components.recorder.models.statistics import StatisticMeanType

            mean_type_none = StatisticMeanType.NONE
        except Exception:  # noqa: BLE001 - fallback for version differences
            pass
        metadata["mean_type"] = mean_type_none
    if _supports_statistics_metadata_field("unit_class"):
        metadata["unit_class"] = EnergyConverter.UNIT_CLASS
    return metadata


async def _async_clear_statistics_and_wait(
    hass: HomeAssistant,
    statistic_id: str,
    *,
    timeout_seconds: float = 30.0,
) -> None:
    """Clear statistics and wait until rows are no longer returned."""
    _async_clear_statistics(hass, [statistic_id])
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        latest_stats = await hass.async_add_executor_job(
            _get_last_statistics,
            hass,
            1,
            statistic_id,
            False,
            {"sum"},
        )
        if not latest_stats.get(statistic_id):
            return
        await asyncio.sleep(0.25)
    raise RuntimeError("Timed out waiting for recorder statistics clear operation")


def _parse_numeric(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and value.lower() in {"none", "unknown", "unavailable"}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _convert_energy_to_kwh(value: float, unit: str | None) -> float | None:
    if not unit:
        return None
    try:
        return float(EnergyConverter.convert(value, unit, UnitOfEnergy.KILO_WATT_HOUR))
    except (TypeError, ValueError):
        return None


def _bridge_energy_entity_id(
    hass: HomeAssistant, entry: ConfigEntry
) -> str | None:
    if entry.runtime_data.energy_sensor and entry.runtime_data.energy_sensor.entity_id:
        return entry.runtime_data.energy_sensor.entity_id
    entity_registry = er.async_get(hass)
    return entity_registry.async_get_entity_id(
        "sensor",
        DOMAIN,
        f"{entry.runtime_data.consumer.consumer_uuid}_energy",
    )


def _manual_validation_error(key: str, placeholders: dict[str, str] | None = None) -> None:
    raise ServiceValidationError(
        "Energy Device Bridge history import validation error",
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders,
    )


def _build_notification_id(entry_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_history_import"


def _build_stats_rows(
    states: Iterable[State],
    tracker: EnergyTrackerState,
    source_entity_id: str,
    zero_drop_policy: str,
    import_start_hour: datetime,
    import_end_exclusive_hour: datetime,
) -> tuple[list[dict[str, Any]], int, datetime | None, datetime | None]:
    hours: dict[datetime, float] = {}
    sample_count = 0
    period_start: datetime | None = None
    period_end: datetime | None = None

    sorted_states = sorted(
        list(states),
        key=lambda state: dt_util.as_utc(
            state.last_updated or state.last_changed or dt_util.utcnow()
        ),
    )
    for source_state in sorted_states:
        source_numeric = _parse_numeric(source_state.state)
        if source_numeric is None:
            continue
        source_kwh = _convert_energy_to_kwh(
            source_numeric, source_state.attributes.get("unit_of_measurement")
        )
        if source_kwh is None:
            continue

        sample_ts = source_state.last_updated or source_state.last_changed or dt_util.utcnow()
        sample_ts = dt_util.as_utc(sample_ts)
        sample_count += 1
        result = apply_source_sample(
            tracker,
            source_entity_id=source_entity_id,
            source_kwh=source_kwh,
            sample_ts_iso=sample_ts.isoformat(),
            zero_drop_policy=zero_drop_policy,
        )

        hour_start = sample_ts.replace(minute=0, second=0, microsecond=0)
        if hour_start < import_start_hour:
            continue
        if hour_start >= import_end_exclusive_hour:
            continue
        if period_start is None or hour_start < period_start:
            period_start = hour_start
        if period_end is None or hour_start > period_end:
            period_end = hour_start
        hours[hour_start] = round(tracker.virtual_total_kwh, 6)

        # Keep variable referenced to make intent explicit.
        _ = result

    rows = [
        {"start": hour_start, "state": total, "sum": total}
        for hour_start, total in sorted(hours.items())
    ]
    # Keep exactly one initial zero row (if present) and drop additional
    # zero-only prefix rows before the first positive total.
    if rows:
        first_positive_index: int | None = None
        for idx, row in enumerate(rows):
            if row["sum"] > 0:
                first_positive_index = idx
                break
        if first_positive_index is not None and first_positive_index > 1:
            rows = [rows[0], *rows[first_positive_index:]]
    if rows:
        period_start = rows[0]["start"]
        period_end = rows[-1]["start"]
    else:
        period_start = None
        period_end = None
    return rows, sample_count, period_start, period_end


async def async_schedule_copy_on_create(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Schedule one-time copy-on-create import when enabled."""
    if not bool(
        entry.options.get(
            CONF_COPY_SOURCE_HISTORY_ON_CREATE,
            DEFAULT_COPY_SOURCE_HISTORY_ON_CREATE,
        )
    ):
        return
    if bool(entry.options.get(CONF_COPY_SOURCE_HISTORY_ON_CREATE_INVOKED, False)):
        return
    if not bool(entry.options.get(CONF_COPY_SOURCE_HISTORY_ON_CREATE_PENDING, False)):
        return
    tracker = await entry.runtime_data.store.async_load() or EnergyTrackerState()
    if tracker.history_import_create_invoked:
        return
    if tracker.history_import_has_run:
        return
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_COPY_SOURCE_HISTORY_ON_CREATE_INVOKED: True,
        },
    )
    # Persist this immediately so restart/reload never re-triggers create-time import.
    tracker.history_import_create_invoked = True
    await entry.runtime_data.store.async_save(tracker)
    await async_request_history_import(
        hass,
        entry=entry,
        trigger="create",
        reject_if_running=False,
    )


async def async_request_history_import(
    hass: HomeAssistant,
    *,
    entry: ConfigEntry,
    trigger: str,
    reject_if_running: bool = True,
) -> bool:
    """Queue history import task for one entry."""
    running_task = entry.runtime_data.history_import_task
    if running_task is not None and not running_task.done():
        if reject_if_running:
            _manual_validation_error("history_import_in_progress")
        return False

    task = hass.async_create_task(_async_run_import(hass, entry, trigger))
    entry.runtime_data.history_import_task = task

    def _clear_task(_task: object) -> None:
        runtime_data = getattr(entry, "runtime_data", None)
        if runtime_data is None:
            return
        if runtime_data.history_import_task is _task:
            runtime_data.history_import_task = None

    task.add_done_callback(_clear_task)
    return True


async def _async_run_import(
    hass: HomeAssistant,
    entry: ConfigEntry,
    trigger: str,
) -> None:
    tracker = await entry.runtime_data.store.async_load() or EnergyTrackerState()
    bridge_entity_id = _bridge_energy_entity_id(hass, entry)
    source_entity_id = entry.runtime_data.consumer.source_energy_entity_id

    tracker.history_import_in_progress = True
    tracker.history_import_last_started_at = dt_util.utcnow().isoformat()
    tracker.history_import_last_error = None
    await entry.runtime_data.store.async_save(tracker)

    if bridge_entity_id is None:
        tracker.history_import_in_progress = False
        tracker.history_import_last_result = "failed"
        tracker.history_import_last_error = "Bridge energy entity is unavailable"
        tracker.history_import_last_finished_at = dt_util.utcnow().isoformat()
        await entry.runtime_data.store.async_save(tracker)
        persistent_notification.async_create(
            hass,
            "Historical import failed: bridge energy entity is unavailable.",
            title="Energy Device Bridge history import failed",
            notification_id=_build_notification_id(entry.entry_id),
        )
        return

    try:
        import_start_hour = _EPOCH_UTC
        current_hour_start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
        replay_tracker = EnergyTrackerState()
        retention_limited = False
        first_import_run = not tracker.history_import_has_run

        # First run should replay full available source history, regardless of
        # any bridge rows that were already generated by normal live operation.
        if not first_import_run:
            latest_stats = await hass.async_add_executor_job(
                _get_last_statistics,
                hass,
                1,
                bridge_entity_id,
                False,
                {"sum"},
            )
            latest_rows = latest_stats.get(bridge_entity_id, [])
            if latest_rows and latest_rows[-1].get("sum") is not None:
                replay_tracker.virtual_total_kwh = float(latest_rows[-1]["sum"])

            if tracker.history_import_last_imported_hour_start:
                last_imported_hour = dt_util.parse_datetime(
                    tracker.history_import_last_imported_hour_start
                )
                if last_imported_hour is not None:
                    import_start_hour = dt_util.as_utc(last_imported_hour) + timedelta(hours=1)
            else:
                if latest_rows:
                    last_row_start = dt_util.utc_from_timestamp(latest_rows[-1]["start"])
                    import_start_hour = last_row_start + timedelta(hours=1)
        else:
            await _async_clear_statistics_and_wait(hass, bridge_entity_id)

        history_data = await hass.async_add_executor_job(
            _state_changes_during_period,
            hass,
            import_start_hour,
            current_hour_start,
            source_entity_id,
        )
        source_states = history_data.get(source_entity_id, [])
        zero_drop_policy = str(entry.options.get(CONF_ZERO_DROP_POLICY, DEFAULT_ZERO_DROP_POLICY))
        stats_rows, sample_count, period_start, period_end = _build_stats_rows(
            source_states,
            replay_tracker,
            source_entity_id,
            zero_drop_policy,
            import_start_hour,
            current_hour_start,
        )
        if stats_rows:
            metadata = _build_statistics_metadata(
                name=entry.title,
                statistic_id=bridge_entity_id,
            )
            _async_import_statistics(hass, metadata, stats_rows)

        if source_states and tracker.history_import_has_run:
            first_ts = dt_util.as_utc(
                source_states[0].last_updated
                or source_states[0].last_changed
                or dt_util.utcnow()
            )
            retention_limited = first_ts > import_start_hour

        if stats_rows:
            tracker.virtual_total_kwh = replay_tracker.virtual_total_kwh
            source_state = hass.states.get(source_entity_id)
            source_kwh: float | None = None
            if source_state is not None:
                source_numeric = _parse_numeric(source_state.state)
                if source_numeric is not None:
                    source_kwh = _convert_energy_to_kwh(
                        source_numeric,
                        source_state.attributes.get("unit_of_measurement"),
                    )
            now_iso = dt_util.utcnow().isoformat()
            tracker.last_source_entity_id = source_entity_id
            tracker.awaiting_non_zero_after_zero_drop = False
            if source_kwh is None:
                # Force next valid live update to initialize baseline cleanly.
                tracker.last_source_energy_value_kwh = None
                tracker.last_valid_source_sample_ts = None
                tracker.current_normalized_source_unit = None
            else:
                tracker.last_source_energy_value_kwh = source_kwh
                tracker.last_valid_source_sample_ts = now_iso
                tracker.current_normalized_source_unit = UnitOfEnergy.KILO_WATT_HOUR

            if bool(entry.options.get(CONF_COPY_SOURCE_HISTORY_ON_CREATE_PENDING, False)):
                hass.config_entries.async_update_entry(
                    entry,
                    options={
                        **entry.options,
                        CONF_COPY_SOURCE_HISTORY_ON_CREATE_PENDING: False,
                    },
                )

        tracker.history_import_in_progress = False
        tracker.history_import_has_run = True
        tracker.history_import_last_result = "success"
        tracker.history_import_last_error = None
        tracker.history_import_last_finished_at = dt_util.utcnow().isoformat()
        tracker.history_import_retention_limited = retention_limited
        tracker.history_import_samples_processed = sample_count
        tracker.history_import_hours_imported = len(stats_rows)
        tracker.history_import_period_start = (
            period_start.isoformat() if period_start else None
        )
        tracker.history_import_period_end = period_end.isoformat() if period_end else None
        tracker.history_import_last_imported_hour_start = (
            period_end.isoformat() if period_end else tracker.history_import_last_imported_hour_start
        )
        await entry.runtime_data.store.async_save(tracker)
        if entry.runtime_data.energy_sensor is not None:
            await entry.runtime_data.energy_sensor.async_apply_import_tracker_state(tracker)

        summary = (
            f"Trigger: {trigger}\n"
            f"Source entity: {source_entity_id}\n"
            f"Bridge entity: {bridge_entity_id}\n"
            f"Imported period start: {tracker.history_import_period_start}\n"
            f"Imported period end: {tracker.history_import_period_end}\n"
            f"Rows imported: {tracker.history_import_hours_imported}\n"
            f"Samples processed: {tracker.history_import_samples_processed}\n"
            f"Exact replay only: yes\n"
            f"Retention limited: {'yes' if retention_limited else 'no'}"
        )
        persistent_notification.async_create(
            hass,
            summary,
            title="Energy Device Bridge history import completed",
            notification_id=_build_notification_id(entry.entry_id),
        )
    except Exception as err:  # noqa: BLE001 - user-facing failure path
        tracker.history_import_in_progress = False
        tracker.history_import_last_result = "failed"
        tracker.history_import_last_error = str(err)
        tracker.history_import_last_finished_at = dt_util.utcnow().isoformat()
        await entry.runtime_data.store.async_save(tracker)
        persistent_notification.async_create(
            hass,
            (
                "Historical import failed.\n\n"
                f"Source entity: {source_entity_id}\n"
                f"Bridge entity: {bridge_entity_id}\n"
                f"Error: {err}"
            ),
            title="Energy Device Bridge history import failed",
            notification_id=_build_notification_id(entry.entry_id),
        )


