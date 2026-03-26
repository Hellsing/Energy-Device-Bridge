"""The Energy Device Bridge integration."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
import logging
from typing import TYPE_CHECKING

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .const import (
    ATTR_VALUE_KWH,
    DOMAIN,
    PLATFORMS,
    SERVICE_ADOPT_CURRENT_SOURCE_AS_BASELINE,
    SERVICE_CLEANUP_RECORDER_DATA,
    SERVICE_IMPORT_SOURCE_HISTORY,
    SERVICE_RESET_TRACKER,
    SERVICE_SET_VIRTUAL_TOTAL,
)
from .history_import import async_request_history_import, async_schedule_copy_on_create
from .models import ConsumerConfig, resolve_consumer_config
from .store import EnergyDeviceBridgeStore

if TYPE_CHECKING:
    from .sensor import EnergyDeviceBridgeEnergySensor

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass(slots=True)
class EnergyDeviceBridgeRuntimeData:
    """Runtime objects for a config entry."""

    consumer: ConsumerConfig
    entry_id: str
    store: EnergyDeviceBridgeStore
    device_info: DeviceInfo
    energy_sensor: EnergyDeviceBridgeEnergySensor | None = None
    active_issue_ids: set[str] = field(default_factory=set)
    history_import_task: asyncio.Task[None] | None = None

    def _issue_id(self, issue_key: str) -> str:
        return f"{self.consumer.consumer_uuid}_{issue_key}"

    def set_issue(
        self,
        hass: HomeAssistant,
        issue_key: str,
        *,
        is_active: bool,
        translation_placeholders: dict[str, str] | None = None,
    ) -> None:
        """Create or dismiss a runtime repair issue."""
        issue_id = self._issue_id(issue_key)

        if is_active:
            if issue_id in self.active_issue_ids:
                return
            ir.async_create_issue(
                hass,
                DOMAIN,
                issue_id,
                is_fixable=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key=issue_key,
                translation_placeholders=translation_placeholders,
                data={"entry_id": self.entry_id},
            )
            self.active_issue_ids.add(issue_id)
            return

        if issue_id not in self.active_issue_ids:
            return
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        self.active_issue_ids.remove(issue_id)

    def dismiss_all_issues(self, hass: HomeAssistant) -> None:
        """Dismiss any active runtime repair issues."""
        if not self.active_issue_ids:
            return
        for issue_id in tuple(self.active_issue_ids):
            ir.async_delete_issue(hass, DOMAIN, issue_id)
        self.active_issue_ids.clear()


if TYPE_CHECKING:
    EnergyDeviceBridgeConfigEntry = ConfigEntry[EnergyDeviceBridgeRuntimeData]
else:
    EnergyDeviceBridgeConfigEntry = ConfigEntry


def _raise_service_validation_error(
    translation_key: str,
    placeholders: dict[str, str] | None = None,
) -> None:
    raise ServiceValidationError(
        "Energy Device Bridge service validation error",
        translation_domain=DOMAIN,
        translation_key=translation_key,
        translation_placeholders=placeholders,
    )


def _resolve_entry_by_id(
    hass: HomeAssistant,
    config_entry_id: str,
) -> EnergyDeviceBridgeConfigEntry:
    entry = hass.config_entries.async_get_entry(config_entry_id)
    if entry is None or entry.domain != DOMAIN:
        _raise_service_validation_error(
            "service_entry_not_found", {"entry_id": config_entry_id}
        )
    assert entry is not None
    if entry.state is not ConfigEntryState.LOADED:
        _raise_service_validation_error("service_entry_not_loaded")
    return entry


def _resolve_energy_sensors_from_entity_ids(
    hass: HomeAssistant,
    entity_ids: list[str],
) -> list[EnergyDeviceBridgeEnergySensor]:
    entity_registry = er.async_get(hass)
    sensors_by_entry_id: dict[str, EnergyDeviceBridgeEnergySensor] = {}
    for entity_id in entity_ids:
        entity_entry = entity_registry.async_get(entity_id)
        if entity_entry is None:
            _raise_service_validation_error(
                "service_entity_not_found", {"entity_id": entity_id}
            )

        entry = hass.config_entries.async_get_entry(entity_entry.config_entry_id)
        if entry is None or entry.domain != DOMAIN:
            _raise_service_validation_error(
                "service_entity_wrong_domain", {"entity_id": entity_id}
            )
        if entry.state is not ConfigEntryState.LOADED:
            _raise_service_validation_error("service_entry_not_loaded")

        runtime_data = entry.runtime_data
        if (
            runtime_data.energy_sensor is None
            or runtime_data.energy_sensor.entity_id is None
        ):
            _raise_service_validation_error("service_energy_sensor_unavailable")
        if runtime_data.energy_sensor.entity_id != entity_id:
            _raise_service_validation_error(
                "service_requires_energy_entity", {"entity_id": entity_id}
            )
        sensors_by_entry_id[entry.entry_id] = runtime_data.energy_sensor
    return list(sensors_by_entry_id.values())


async def async_setup_entry(
    hass: HomeAssistant, entry: EnergyDeviceBridgeConfigEntry
) -> bool:
    """Set up Energy Device Bridge from a config entry."""
    consumer = resolve_consumer_config(entry.data)
    entry_updates: dict[str, str] = {}
    if entry.title != consumer.consumer_name:
        entry_updates["title"] = consumer.consumer_name
    if entry.unique_id != consumer.consumer_uuid:
        entry_updates["unique_id"] = consumer.consumer_uuid
    if entry_updates:
        hass.config_entries.async_update_entry(entry, **entry_updates)
    device_info = DeviceInfo(
        identifiers={(DOMAIN, consumer.consumer_uuid)},
        manufacturer="Energy Device Bridge",
        model="Persistent Energy Bridge",
        name=consumer.consumer_name,
    )
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, consumer.consumer_uuid)},
        manufacturer="Energy Device Bridge",
        model="Persistent Energy Bridge",
        name=consumer.consumer_name,
    )
    entry.runtime_data = EnergyDeviceBridgeRuntimeData(
        consumer=consumer,
        entry_id=entry.entry_id,
        store=EnergyDeviceBridgeStore(hass, entry.entry_id),
        device_info=device_info,
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    hass.async_create_task(async_schedule_copy_on_create(hass, entry))
    return True


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
    """Set up integration-level services."""

    async def _handle_adopt_baseline_service(call: ServiceCall) -> None:
        entity_ids = list(call.data[ATTR_ENTITY_ID])
        for sensor in _resolve_energy_sensors_from_entity_ids(hass, entity_ids):
            await sensor.async_adopt_current_source_as_baseline()

    async def _handle_reset_tracker_service(call: ServiceCall) -> None:
        entity_ids = list(call.data[ATTR_ENTITY_ID])
        for sensor in _resolve_energy_sensors_from_entity_ids(hass, entity_ids):
            await sensor.async_reset_tracker()

    async def _handle_set_virtual_total_service(call: ServiceCall) -> None:
        entity_ids = list(call.data[ATTR_ENTITY_ID])
        value_kwh = float(call.data[ATTR_VALUE_KWH])
        if value_kwh < 0:
            raise ServiceValidationError(
                "Energy Device Bridge service validation error",
                translation_domain=DOMAIN,
                translation_key="virtual_total_negative",
            )
        for sensor in _resolve_energy_sensors_from_entity_ids(hass, entity_ids):
            await sensor.async_set_virtual_total(value_kwh)

    async def _handle_import_service(call: ServiceCall) -> None:
        entry = _resolve_entry_by_id(hass, call.data["config_entry_id"])
        accepted = await async_request_history_import(
            hass,
            entry=entry,
            trigger="service",
            reject_if_running=True,
        )
        if not accepted:
            _raise_service_validation_error("history_import_in_progress")

    async def _handle_cleanup_recorder_data_service(call: ServiceCall) -> None:
        entity_ids = list(call.data[ATTR_ENTITY_ID])
        await _async_purge_entity_history(hass, entity_ids, wait_for_completion=True)
        _async_clear_statistics_for_entity_ids(hass, entity_ids)

    if not hass.services.has_service(DOMAIN, SERVICE_ADOPT_CURRENT_SOURCE_AS_BASELINE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_ADOPT_CURRENT_SOURCE_AS_BASELINE,
            _handle_adopt_baseline_service,
            schema=vol.Schema({vol.Required(ATTR_ENTITY_ID): cv.comp_entity_ids}),
        )
    if not hass.services.has_service(DOMAIN, SERVICE_RESET_TRACKER):
        hass.services.async_register(
            DOMAIN,
            SERVICE_RESET_TRACKER,
            _handle_reset_tracker_service,
            schema=vol.Schema({vol.Required(ATTR_ENTITY_ID): cv.comp_entity_ids}),
        )
    if not hass.services.has_service(DOMAIN, SERVICE_SET_VIRTUAL_TOTAL):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_VIRTUAL_TOTAL,
            _handle_set_virtual_total_service,
            schema=vol.Schema(
                {
                    vol.Required(ATTR_ENTITY_ID): cv.comp_entity_ids,
                    vol.Required(ATTR_VALUE_KWH): vol.Coerce(float),
                }
            ),
        )
    if not hass.services.has_service(DOMAIN, SERVICE_IMPORT_SOURCE_HISTORY):
        hass.services.async_register(
            DOMAIN,
            SERVICE_IMPORT_SOURCE_HISTORY,
            _handle_import_service,
            schema=vol.Schema({vol.Required("config_entry_id"): cv.string}),
        )
    if not hass.services.has_service(DOMAIN, SERVICE_CLEANUP_RECORDER_DATA):
        hass.services.async_register(
            DOMAIN,
            SERVICE_CLEANUP_RECORDER_DATA,
            _handle_cleanup_recorder_data_service,
            schema=vol.Schema({vol.Required(ATTR_ENTITY_ID): cv.comp_entity_ids}),
        )
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: EnergyDeviceBridgeConfigEntry
) -> bool:
    """Unload a config entry."""
    runtime_data = entry.runtime_data
    if (
        runtime_data.history_import_task is not None
        and not runtime_data.history_import_task.done()
    ):
        runtime_data.history_import_task.cancel()
        with suppress(asyncio.CancelledError):
            await runtime_data.history_import_task
        runtime_data.history_import_task = None
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        runtime_data.dismiss_all_issues(hass)
    return unloaded


async def async_remove_entry(
    hass: HomeAssistant, entry: EnergyDeviceBridgeConfigEntry
) -> None:
    """Remove config entry and persisted metadata/history for owned entities."""
    await _async_cleanup_recorder_for_entry(hass, entry)
    runtime_data = getattr(entry, "runtime_data", None)
    if runtime_data is not None:
        runtime_data.dismiss_all_issues(hass)
    await EnergyDeviceBridgeStore(hass, entry.entry_id).async_remove()


def _async_entry_entity_ids(
    hass: HomeAssistant,
    entry: EnergyDeviceBridgeConfigEntry,
) -> list[str]:
    """Return current entity_ids owned by one config entry."""
    entity_registry = er.async_get(hass)
    owned_entity_ids = [
        entity.entity_id
        for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    ]

    # Fallback for edge cases where entity registry entries were already removed.
    runtime_data = getattr(entry, "runtime_data", None)
    if runtime_data is not None and runtime_data.energy_sensor is not None:
        entity_id = runtime_data.energy_sensor.entity_id
        if entity_id is not None and entity_id not in owned_entity_ids:
            owned_entity_ids.append(entity_id)

    if not owned_entity_ids:
        consumer = resolve_consumer_config(entry.data)
        fallback_entity_id = entity_registry.async_get_entity_id(
            "sensor",
            DOMAIN,
            f"{consumer.consumer_uuid}_energy",
        )
        if fallback_entity_id is not None:
            owned_entity_ids.append(fallback_entity_id)
    return owned_entity_ids


async def _async_purge_entity_history(
    hass: HomeAssistant,
    entity_ids: list[str],
    *,
    wait_for_completion: bool,
) -> None:
    """Best-effort purge of recorder states/events for entities."""
    if not entity_ids:
        return
    if not hass.services.has_service("recorder", "purge_entities"):
        _LOGGER.debug("Recorder purge_entities service unavailable")
        return
    await hass.services.async_call(
        "recorder",
        "purge_entities",
        {"entity_id": entity_ids, "keep_days": 0},
        blocking=wait_for_completion,
    )


def _async_clear_statistics_for_entity_ids(
    hass: HomeAssistant,
    entity_ids: list[str],
) -> None:
    """Best-effort statistics clear for sensor entities."""
    statistic_ids = [
        entity_id for entity_id in entity_ids if entity_id.startswith("sensor.")
    ]
    if not statistic_ids:
        return
    try:
        _get_recorder_instance(hass).async_clear_statistics(statistic_ids)
    except Exception:  # noqa: BLE001 - recorder may be unavailable
        _LOGGER.debug("Unable to clear statistics for %s", statistic_ids, exc_info=True)


async def _async_cleanup_recorder_for_entry(
    hass: HomeAssistant,
    entry: EnergyDeviceBridgeConfigEntry,
) -> None:
    """Best-effort cleanup of recorder history/statistics for removed entry."""
    entity_ids = _async_entry_entity_ids(hass, entry)
    if not entity_ids:
        return
    try:
        await _async_purge_entity_history(
            hass,
            entity_ids,
            wait_for_completion=False,
        )
        _async_clear_statistics_for_entity_ids(hass, entity_ids)
    except Exception:  # noqa: BLE001 - do not block entry removal
        _LOGGER.debug(
            "Unable to clear recorder data while removing entry %s",
            entry.entry_id,
            exc_info=True,
        )


def _get_recorder_instance(hass: HomeAssistant):
    """Return active recorder instance for API calls."""
    from homeassistant.components.recorder import get_instance

    return get_instance(hass)


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: EnergyDeviceBridgeConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Delete one bridge device by removing the owning config entry."""
    if config_entry.entry_id not in device_entry.config_entries:
        return False
    if not any(identifier[0] == DOMAIN for identifier in device_entry.identifiers):
        return False
    return await hass.config_entries.async_remove(config_entry.entry_id)
