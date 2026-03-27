"""Historical source replay and statistics import for bridge sensors."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import datetime, timedelta
from functools import partial
import logging
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
_LOGGER = logging.getLogger(__name__)


def _get_last_statistics(
    hass: HomeAssistant,
    number_of_stats: int,
    statistic_id: str,
    convert_units: bool,
    types: set[str],
) -> dict[str, list[dict[str, Any]]]:
    from homeassistant.components.recorder.statistics import get_last_statistics

    return get_last_statistics(
        hass, number_of_stats, statistic_id, convert_units, types
    )


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


def _resolve_short_term_statistics_table() -> type | None:
    """Resolve recorder short-term statistics ORM model."""
    try:
        from homeassistant.components.recorder.db_schema import StatisticsShortTerm
    except Exception:  # noqa: BLE001 - import safety across HA versions
        return None
    return StatisticsShortTerm


def _async_import_short_term_statistics(
    hass: HomeAssistant,
    metadata: dict[str, Any],
    statistics: list[dict[str, Any]],
) -> bool:
    """Import 5-minute statistics when recorder internals support it."""
    table = _resolve_short_term_statistics_table()
    if table is None:
        return False
    try:
        from homeassistant.components.recorder import get_instance

        instance = get_instance(hass)
        import_fn = getattr(instance, "async_import_statistics", None)
        if import_fn is None:
            return False
        import_fn(metadata, statistics, table)
    except Exception:  # noqa: BLE001 - best-effort short-term import
        _LOGGER.debug(
            "Unable to import short-term statistics for %s",
            metadata.get("statistic_id"),
            exc_info=True,
        )
        return False
    return True


def _async_clear_statistics(hass: HomeAssistant, statistic_ids: list[str]) -> None:
    from homeassistant.components.recorder import get_instance

    get_instance(hass).async_clear_statistics(statistic_ids)


def _last_valid_source_kwh_before(
    hass: HomeAssistant,
    *,
    source_entity_id: str,
    before_time: datetime,
) -> float | None:
    """Return latest valid source kWh sample strictly before an import window."""
    if before_time <= _EPOCH_UTC:
        return None
    history_data = _state_changes_during_period(
        hass,
        _EPOCH_UTC,
        before_time,
        source_entity_id,
    )
    source_states = history_data.get(source_entity_id, [])
    if not source_states:
        return None

    sorted_states = sorted(
        list(source_states),
        key=lambda state: dt_util.as_utc(
            state.last_updated or state.last_changed or dt_util.utcnow()
        ),
        reverse=True,
    )
    for source_state in sorted_states:
        source_numeric = _parse_numeric(source_state.state)
        if source_numeric is None:
            continue
        source_kwh = _convert_energy_to_kwh(
            source_numeric, source_state.attributes.get("unit_of_measurement")
        )
        if source_kwh is not None:
            return source_kwh
    return None


def _seed_replay_baseline_from_persisted_tracker(
    *,
    tracker: EnergyTrackerState,
    replay_tracker: EnergyTrackerState,
    source_entity_id: str,
    import_start_hour: datetime,
) -> bool:
    """Seed replay baseline from persisted import state when compatible."""
    persisted_value = tracker.history_import_last_source_energy_value_kwh
    if persisted_value is None:
        return False
    if tracker.history_import_last_source_entity_id != source_entity_id:
        return False
    if tracker.history_import_last_source_unit != UnitOfEnergy.KILO_WATT_HOUR:
        return False
    if not tracker.history_import_last_source_sample_ts:
        return False
    sample_dt = dt_util.parse_datetime(tracker.history_import_last_source_sample_ts)
    if sample_dt is None:
        return False
    if dt_util.as_utc(sample_dt) >= import_start_hour:
        return False

    replay_tracker.last_source_entity_id = source_entity_id
    replay_tracker.last_source_energy_value_kwh = float(persisted_value)
    replay_tracker.last_valid_source_sample_ts = tracker.history_import_last_source_sample_ts
    replay_tracker.current_normalized_source_unit = UnitOfEnergy.KILO_WATT_HOUR
    return True


async def _async_seed_replay_baseline_with_legacy_backfill(
    hass: HomeAssistant,
    *,
    replay_tracker: EnergyTrackerState,
    source_entity_id: str,
    import_start_hour: datetime,
) -> None:
    """Legacy/recovery baseline backfill for pre-optimization tracker state."""
    last_source_before_window = await hass.async_add_executor_job(
        partial(
            _last_valid_source_kwh_before,
            hass,
            source_entity_id=source_entity_id,
            before_time=import_start_hour,
        )
    )
    if last_source_before_window is None:
        return
    replay_tracker.last_source_entity_id = source_entity_id
    replay_tracker.last_source_energy_value_kwh = last_source_before_window
    replay_tracker.current_normalized_source_unit = UnitOfEnergy.KILO_WATT_HOUR


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
            from homeassistant.components.recorder.models.statistics import (
                StatisticMeanType,
            )

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
) -> None:
    """Clear statistics and synchronize with recorder task queue.

    Recorder clear operations are asynchronous queue tasks. We wait for recorder
    queue synchronization rather than enforcing a fixed timeout, then verify
    best-effort that rows are gone. Any residual rows are logged and handled by
    subsequent import overwrite logic.
    """
    _async_clear_statistics(hass, [statistic_id])
    try:
        from homeassistant.components.recorder import get_instance

        await get_instance(hass).async_block_till_done()
    except Exception:  # noqa: BLE001 - recorder may be unavailable during startup
        _LOGGER.debug(
            "Unable to synchronize recorder queue after statistics clear for %s",
            statistic_id,
            exc_info=True,
        )
        return

    latest_stats = await hass.async_add_executor_job(
        _get_last_statistics,
        hass,
        1,
        statistic_id,
        False,
        {"sum"},
    )
    if latest_stats.get(statistic_id):
        _LOGGER.debug(
            "Statistics clear verification still returned rows for %s",
            statistic_id,
        )


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


def _bridge_energy_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    if entry.runtime_data.energy_sensor and entry.runtime_data.energy_sensor.entity_id:
        return entry.runtime_data.energy_sensor.entity_id
    entity_registry = er.async_get(hass)
    return entity_registry.async_get_entity_id(
        "sensor",
        DOMAIN,
        f"{entry.runtime_data.consumer.consumer_uuid}_energy",
    )


def _manual_validation_error(
    key: str, placeholders: dict[str, str] | None = None
) -> None:
    raise ServiceValidationError(
        "Energy Device Bridge history import validation error",
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders,
    )


def _build_notification_id(entry_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_history_import"


def _format_notification_datetime(iso_timestamp: str | None) -> str:
    """Format tracker timestamps for human-readable notifications."""
    if not iso_timestamp:
        return "n/a"
    parsed = dt_util.parse_datetime(iso_timestamp)
    if parsed is None:
        return iso_timestamp
    localized = dt_util.as_local(dt_util.as_utc(parsed))
    return localized.strftime("%Y-%m-%d %H:%M:%S %Z")


def _build_success_notification_message(
    *,
    trigger: str,
    source_entity_id: str,
    bridge_entity_id: str,
    period_start_iso: str | None,
    period_end_iso: str | None,
    sample_count: int,
    hourly_rows_imported: int,
    short_term_rows_imported: int,
    short_term_rows_skipped: int,
    retention_limited: bool,
) -> str:
    """Build a professional, scannable success summary for persistent notifications."""
    short_term_line = f"**5-minute rows imported:** {short_term_rows_imported}"
    if short_term_rows_skipped > 0:
        short_term_line = (
            f"{short_term_line} ({short_term_rows_skipped} skipped: unsupported recorder "
            "short-term import)"
        )

    total_rows_imported = hourly_rows_imported + short_term_rows_imported
    return (
        "## Energy history import complete\n"
        f"- **Trigger:** `{trigger}`\n"
        f"- **Source entity:** `{source_entity_id}`\n"
        f"- **Bridge entity:** `{bridge_entity_id}`\n\n"
        "### Imported window\n"
        f"- **Start:** {_format_notification_datetime(period_start_iso)}\n"
        f"- **End:** {_format_notification_datetime(period_end_iso)}\n\n"
        "### Processing summary\n"
        f"- **Samples replayed:** {sample_count}\n"
        f"- **Hourly rows imported:** {hourly_rows_imported}\n"
        f"- {short_term_line}\n"
        f"- **Total rows imported:** {total_rows_imported}\n"
        "- **Replay mode:** Exact historical replay\n"
        f"- **Retention limited:** {'yes' if retention_limited else 'no'}"
    )


def _build_failure_notification_message(
    *,
    source_entity_id: str,
    bridge_entity_id: str | None,
    error: Exception,
) -> str:
    """Build a clear and actionable failure summary for persistent notifications."""
    bridge_value = bridge_entity_id or "unavailable"
    return (
        "## Energy history import failed\n"
        f"- **Source entity:** `{source_entity_id}`\n"
        f"- **Bridge entity:** `{bridge_value}`\n"
        f"- **Error:** `{error}`\n\n"
        "Review Home Assistant logs for full diagnostics."
    )


def _clear_create_pending_option(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clear create-time import pending flag once import reached a terminal state."""
    if not bool(entry.options.get(CONF_COPY_SOURCE_HISTORY_ON_CREATE_PENDING, False)):
        return
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_COPY_SOURCE_HISTORY_ON_CREATE_PENDING: False,
        },
    )


def _build_stats_rows(
    states: Iterable[State],
    tracker: EnergyTrackerState,
    source_entity_id: str,
    zero_drop_policy: str,
    import_start_hour: datetime,
    import_end_exclusive_hour: datetime,
    import_end_exclusive_short_term: datetime,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    int,
    datetime | None,
    datetime | None,
]:
    hours: dict[datetime, float] = {}
    short_term: dict[datetime, float] = {}
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

        sample_ts = (
            source_state.last_updated or source_state.last_changed or dt_util.utcnow()
        )
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
        virtual_total = round(tracker.virtual_total_kwh, 6)
        short_term_start = sample_ts.replace(
            minute=sample_ts.minute - (sample_ts.minute % 5),
            second=0,
            microsecond=0,
        )
        if import_start_hour <= short_term_start < import_end_exclusive_short_term:
            short_term[short_term_start] = virtual_total

        if hour_start < import_start_hour:
            continue
        if hour_start >= import_end_exclusive_hour:
            continue
        if period_start is None or hour_start < period_start:
            period_start = hour_start
        if period_end is None or hour_start > period_end:
            period_end = hour_start
        hours[hour_start] = virtual_total

        # Keep variable referenced to make intent explicit.
        _ = result

    rows = [
        {"start": hour_start, "state": total, "sum": total}
        for hour_start, total in sorted(hours.items())
    ]
    short_term_rows = [
        {"start": bucket_start, "state": total, "sum": total}
        for bucket_start, total in sorted(short_term.items())
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
    return rows, short_term_rows, sample_count, period_start, period_end


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
    reinitialize_before_import: bool = False,
) -> bool:
    """Queue history import task for one entry."""
    running_task = entry.runtime_data.history_import_task
    if running_task is not None and not running_task.done():
        if reject_if_running:
            _manual_validation_error("history_import_in_progress")
        return False

    task = hass.async_create_task(
        _async_run_import(
            hass,
            entry,
            trigger,
            reinitialize_before_import=reinitialize_before_import,
        )
    )
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
    *,
    reinitialize_before_import: bool = False,
) -> None:
    source_entity_id = entry.runtime_data.consumer.source_energy_entity_id
    bridge_entity_id: str | None = None
    tracker = EnergyTrackerState()

    async def _async_purge_bridge_entity_history(entity_id: str) -> None:
        try:
            await hass.services.async_call(
                "recorder",
                "purge_entities",
                {"entity_id": [entity_id], "keep_days": 0},
                blocking=True,
            )
        except Exception:  # noqa: BLE001 - recorder service may be unavailable
            _LOGGER.debug(
                "Recorder purge_entities unavailable for %s",
                entity_id,
                exc_info=True,
            )

    try:
        tracker = await entry.runtime_data.store.async_load() or EnergyTrackerState()
        bridge_entity_id = _bridge_energy_entity_id(hass, entry)

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
            _clear_create_pending_option(hass, entry)
            persistent_notification.async_create(
                hass,
                "Historical import failed: bridge energy entity is unavailable.",
                title="Energy Device Bridge history import failed",
                notification_id=_build_notification_id(entry.entry_id),
            )
            return

        if reinitialize_before_import:
            energy_sensor = entry.runtime_data.energy_sensor
            if energy_sensor is not None:
                await energy_sensor.async_prepare_for_manual_history_import()
            tracker = EnergyTrackerState()
            tracker.history_import_last_started_at = dt_util.utcnow().isoformat()
            tracker.history_import_in_progress = True
            await entry.runtime_data.store.async_save(tracker)
            await _async_purge_bridge_entity_history(bridge_entity_id)
            await _async_clear_statistics_and_wait(hass, bridge_entity_id)

        import_start_hour = _EPOCH_UTC
        now_utc = dt_util.utcnow()
        current_hour_start = now_utc.replace(minute=0, second=0, microsecond=0)
        current_short_term_start = now_utc.replace(
            minute=now_utc.minute - (now_utc.minute % 5),
            second=0,
            microsecond=0,
        )
        replay_tracker = EnergyTrackerState()
        retention_limited = False
        first_import_run = reinitialize_before_import or not tracker.history_import_has_run

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
                    import_start_hour = dt_util.as_utc(last_imported_hour) + timedelta(
                        hours=1
                    )
            else:
                if latest_rows:
                    last_row_start = dt_util.utc_from_timestamp(
                        latest_rows[-1]["start"]
                    )
                    import_start_hour = last_row_start + timedelta(hours=1)

            seeded_from_persisted_baseline = False
            if import_start_hour > _EPOCH_UTC:
                seeded_from_persisted_baseline = (
                    _seed_replay_baseline_from_persisted_tracker(
                        tracker=tracker,
                        replay_tracker=replay_tracker,
                        source_entity_id=source_entity_id,
                        import_start_hour=import_start_hour,
                    )
                )
            if import_start_hour > _EPOCH_UTC and not seeded_from_persisted_baseline:
                await _async_seed_replay_baseline_with_legacy_backfill(
                    hass,
                    replay_tracker=replay_tracker,
                    source_entity_id=source_entity_id,
                    import_start_hour=import_start_hour,
                )
        elif not reinitialize_before_import:
            await _async_clear_statistics_and_wait(hass, bridge_entity_id)

        history_data = await hass.async_add_executor_job(
            _state_changes_during_period,
            hass,
            import_start_hour,
            current_short_term_start,
            source_entity_id,
        )
        source_states = history_data.get(source_entity_id, [])
        zero_drop_policy = str(
            entry.options.get(CONF_ZERO_DROP_POLICY, DEFAULT_ZERO_DROP_POLICY)
        )
        (
            stats_rows,
            short_term_rows,
            sample_count,
            period_start,
            period_end,
        ) = _build_stats_rows(
            source_states,
            replay_tracker,
            source_entity_id,
            zero_drop_policy,
            import_start_hour,
            current_hour_start,
            current_short_term_start,
        )
        metadata = _build_statistics_metadata(
            name=entry.title,
            statistic_id=bridge_entity_id,
        )
        short_term_rows_imported = 0
        short_term_rows_skipped = 0
        if stats_rows:
            _async_import_statistics(hass, metadata, stats_rows)
        if short_term_rows:
            imported_short_term = _async_import_short_term_statistics(
                hass, metadata, short_term_rows
            )
            if imported_short_term:
                short_term_rows_imported = len(short_term_rows)
            else:
                short_term_rows_skipped = len(short_term_rows)
            if not imported_short_term:
                _LOGGER.debug(
                    "Recorder short-term import unsupported for %s; "
                    "5-minute continuity cannot be backfilled on this HA version",
                    bridge_entity_id,
                )

        if source_states and tracker.history_import_has_run:
            first_ts = dt_util.as_utc(
                source_states[0].last_updated
                or source_states[0].last_changed
                or dt_util.utcnow()
            )
            retention_limited = first_ts > import_start_hour

        if stats_rows or short_term_rows or sample_count:
            tracker.virtual_total_kwh = replay_tracker.virtual_total_kwh
            if (
                sample_count
                and replay_tracker.last_source_entity_id == source_entity_id
                and replay_tracker.last_source_energy_value_kwh is not None
            ):
                tracker.history_import_last_source_entity_id = source_entity_id
                tracker.history_import_last_source_energy_value_kwh = (
                    replay_tracker.last_source_energy_value_kwh
                )
                tracker.history_import_last_source_sample_ts = (
                    replay_tracker.last_valid_source_sample_ts
                )
                tracker.history_import_last_source_unit = UnitOfEnergy.KILO_WATT_HOUR
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
        tracker.history_import_period_end = (
            period_end.isoformat() if period_end else None
        )
        tracker.history_import_last_imported_hour_start = (
            period_end.isoformat()
            if period_end
            else tracker.history_import_last_imported_hour_start
        )
        await entry.runtime_data.store.async_save(tracker)
        _clear_create_pending_option(hass, entry)
        if entry.runtime_data.energy_sensor is not None:
            await entry.runtime_data.energy_sensor.async_apply_import_tracker_state(
                tracker
            )

        summary = _build_success_notification_message(
            trigger=trigger,
            source_entity_id=source_entity_id,
            bridge_entity_id=bridge_entity_id,
            period_start_iso=tracker.history_import_period_start,
            period_end_iso=tracker.history_import_period_end,
            sample_count=tracker.history_import_samples_processed,
            hourly_rows_imported=tracker.history_import_hours_imported,
            short_term_rows_imported=short_term_rows_imported,
            short_term_rows_skipped=short_term_rows_skipped,
            retention_limited=retention_limited,
        )
        persistent_notification.async_create(
            hass,
            summary,
            title="Energy Device Bridge history import completed",
            notification_id=_build_notification_id(entry.entry_id),
        )
    except asyncio.CancelledError:
        tracker.history_import_in_progress = False
        tracker.history_import_last_result = "cancelled"
        tracker.history_import_last_error = "History import cancelled"
        tracker.history_import_last_finished_at = dt_util.utcnow().isoformat()
        await entry.runtime_data.store.async_save(tracker)
        if entry.runtime_data.energy_sensor is not None:
            await entry.runtime_data.energy_sensor.async_apply_import_tracker_state(
                tracker
            )
        _clear_create_pending_option(hass, entry)
        raise
    except Exception as err:  # noqa: BLE001 - user-facing failure path
        tracker.history_import_in_progress = False
        tracker.history_import_last_result = "failed"
        tracker.history_import_last_error = str(err)
        tracker.history_import_last_finished_at = dt_util.utcnow().isoformat()
        await entry.runtime_data.store.async_save(tracker)
        if entry.runtime_data.energy_sensor is not None:
            await entry.runtime_data.energy_sensor.async_apply_import_tracker_state(
                tracker
            )
        _clear_create_pending_option(hass, entry)
        persistent_notification.async_create(
            hass,
            _build_failure_notification_message(
                source_entity_id=source_entity_id,
                bridge_entity_id=bridge_entity_id,
                error=err,
            ),
            title="Energy Device Bridge history import failed",
            notification_id=_build_notification_id(entry.entry_id),
        )
