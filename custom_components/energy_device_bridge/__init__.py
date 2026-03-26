"""The Energy Device Bridge integration."""

from __future__ import annotations

import asyncio
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
        if runtime_data.energy_sensor is None or runtime_data.energy_sensor.entity_id is None:
            _raise_service_validation_error("service_energy_sensor_unavailable")
        if runtime_data.energy_sensor.entity_id != entity_id:
            _raise_service_validation_error(
                "service_requires_energy_entity", {"entity_id": entity_id}
            )
        sensors_by_entry_id[entry.entry_id] = runtime_data.energy_sensor
    return list(sensors_by_entry_id.values())


async def async_setup_entry(hass: HomeAssistant, entry: EnergyDeviceBridgeConfigEntry) -> bool:
    """Set up Energy Device Bridge from a config entry."""
    consumer = resolve_consumer_config(entry.data)
    if entry.title != consumer.consumer_name:
        hass.config_entries.async_update_entry(entry, title=consumer.consumer_name)
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
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EnergyDeviceBridgeConfigEntry) -> bool:
    """Unload a config entry."""
    runtime_data = entry.runtime_data
    if runtime_data.history_import_task is not None and not runtime_data.history_import_task.done():
        runtime_data.history_import_task.cancel()
        runtime_data.history_import_task = None
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        runtime_data.dismiss_all_issues(hass)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: EnergyDeviceBridgeConfigEntry) -> None:
    """Remove config entry and persisted metadata."""
    _async_clear_bridge_statistics_for_entry(hass, entry)
    await EnergyDeviceBridgeStore(hass, entry.entry_id).async_remove()


def _async_clear_bridge_statistics_for_entry(
    hass: HomeAssistant,
    entry: EnergyDeviceBridgeConfigEntry,
) -> None:
    """Best-effort clear of bridge long-term statistics for removed entry."""
    try:
        entity_id: str | None = None
        runtime_data = getattr(entry, "runtime_data", None)
        if runtime_data is not None and runtime_data.energy_sensor is not None:
            entity_id = runtime_data.energy_sensor.entity_id
        if entity_id is None:
            consumer = resolve_consumer_config(entry.data)
            entity_id = er.async_get(hass).async_get_entity_id(
                "sensor",
                DOMAIN,
                f"{consumer.consumer_uuid}_energy",
            )
        if entity_id is None:
            return
        from homeassistant.components.recorder import get_instance

        get_instance(hass).async_clear_statistics([entity_id])
    except Exception:  # noqa: BLE001 - do not block entry removal
        _LOGGER.debug(
            "Unable to clear recorder statistics while removing entry %s",
            entry.entry_id,
            exc_info=True,
        )


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: EnergyDeviceBridgeConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Delete device by removing the owning config entry and its entities."""
    if not any(identifier[0] == DOMAIN for identifier in device_entry.identifiers):
        return False
    return await hass.config_entries.async_remove(config_entry.entry_id)
