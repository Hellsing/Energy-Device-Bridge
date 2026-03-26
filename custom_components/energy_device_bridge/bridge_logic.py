"""Shared bridge sample-application logic for live and replay paths."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.const import UnitOfEnergy

from .const import (
    ZERO_DROP_POLICY_IGNORE_ZERO_UNTIL_NON_ZERO,
)
from .models import EnergyTrackerState

LOWER_VALUE_EPSILON = 1e-9


@dataclass(slots=True)
class ApplySampleResult:
    """Result details from applying one normalized source sample."""

    delta_added_kwh: float = 0.0
    event_kind: str = "none"
    previous_kwh: float | None = None
    new_kwh: float | None = None


def _update_baseline(
    tracker: EnergyTrackerState,
    source_entity_id: str,
    source_kwh: float,
    sample_ts_iso: str,
) -> None:
    tracker.last_source_entity_id = source_entity_id
    tracker.last_source_energy_value_kwh = source_kwh
    tracker.awaiting_non_zero_after_zero_drop = False
    tracker.last_valid_source_sample_ts = sample_ts_iso
    tracker.current_normalized_source_unit = UnitOfEnergy.KILO_WATT_HOUR


def apply_source_sample(
    tracker: EnergyTrackerState,
    *,
    source_entity_id: str,
    source_kwh: float,
    sample_ts_iso: str,
    zero_drop_policy: str,
) -> ApplySampleResult:
    """Apply one source sample while preserving monotonic virtual total."""
    result = ApplySampleResult()

    if tracker.last_source_entity_id != source_entity_id:
        tracker.last_source_entity_id = source_entity_id
        tracker.last_source_energy_value_kwh = None
        tracker.awaiting_non_zero_after_zero_drop = False

    if tracker.last_source_energy_value_kwh is None:
        _update_baseline(tracker, source_entity_id, source_kwh, sample_ts_iso)
        result.event_kind = "baseline_init"
        return result

    if tracker.awaiting_non_zero_after_zero_drop:
        if source_kwh <= LOWER_VALUE_EPSILON:
            result.event_kind = "zero_ignored_awaiting_non_zero"
            return result
        _update_baseline(tracker, source_entity_id, source_kwh, sample_ts_iso)
        result.event_kind = "baseline_after_zero_wait"
        return result

    previous_kwh = tracker.last_source_energy_value_kwh
    delta = source_kwh - previous_kwh
    if delta > LOWER_VALUE_EPSILON:
        tracker.virtual_total_kwh += delta
        _update_baseline(tracker, source_entity_id, source_kwh, sample_ts_iso)
        result.delta_added_kwh = delta
        result.event_kind = "positive_delta"
        return result

    if delta < -LOWER_VALUE_EPSILON:
        tracker.ignored_negative_delta_count += 1
        tracker.reset_detected_count += 1

        if source_kwh <= LOWER_VALUE_EPSILON:
            tracker.zero_drop_count += 1
            tracker.last_zero_drop_at = sample_ts_iso
            tracker.last_lower_value_event = {
                "kind": "zero_drop",
                "source_entity_id": source_entity_id,
                "timestamp": sample_ts_iso,
            }
            if zero_drop_policy == ZERO_DROP_POLICY_IGNORE_ZERO_UNTIL_NON_ZERO:
                tracker.awaiting_non_zero_after_zero_drop = True
                result.event_kind = "zero_drop_ignored_until_non_zero"
                return result
            _update_baseline(tracker, source_entity_id, 0.0, sample_ts_iso)
            result.event_kind = "zero_drop_accept_as_new_cycle"
            return result

        tracker.lower_value_count += 1
        tracker.awaiting_non_zero_after_zero_drop = False
        tracker.last_lower_value_event = {
            "kind": "lower_non_zero",
            "source_entity_id": source_entity_id,
            "previous_kwh": previous_kwh,
            "new_kwh": source_kwh,
            "timestamp": sample_ts_iso,
        }
        _update_baseline(tracker, source_entity_id, source_kwh, sample_ts_iso)
        result.event_kind = "lower_non_zero"
        result.previous_kwh = previous_kwh
        result.new_kwh = source_kwh
        return result

    _update_baseline(tracker, source_entity_id, source_kwh, sample_ts_iso)
    result.event_kind = "no_change"
    return result
