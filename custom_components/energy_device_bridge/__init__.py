"""The Energy Device Bridge integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, PLATFORMS, SERVICE_IMPORT_SOURCE_HISTORY
from .history_import import (
    async_request_history_import,
    async_schedule_copy_on_create,
    resolve_entry_for_service,
)
from .models import ConsumerConfig, resolve_consumer_config
from .store import EnergyDeviceBridgeStore

if TYPE_CHECKING:
    from .sensor import EnergyDeviceBridgeEnergySensor


@dataclass(slots=True)
class EnergyDeviceBridgeRuntimeData:
    """Runtime objects for a config entry."""

    consumer: ConsumerConfig
    entry_id: str
    store: EnergyDeviceBridgeStore
    device_info: DeviceInfo
    energy_sensor: EnergyDeviceBridgeEnergySensor | None = None
    active_issue_ids: set[str] | None = None

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
        if self.active_issue_ids is None:
            self.active_issue_ids = set()
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
    # Ensure the bridge device exists as soon as the entry is set up.
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
        active_issue_ids=set(),
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    hass.async_create_task(async_schedule_copy_on_create(hass, entry))
    return True


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
    """Set up integration-level services."""
    hass.data.setdefault(DOMAIN, {})
    if hass.data[DOMAIN].get("services_registered"):
        return True

    async def _handle_import_service(call) -> None:
        entry = resolve_entry_for_service(
            hass,
            config_entry_id=call.data.get("config_entry_id"),
            entity_id=call.data.get("entity_id"),
        )
        accepted = await async_request_history_import(
            hass,
            entry=entry,
            trigger="service",
            reject_if_running=True,
        )
        if not accepted:
            raise ServiceValidationError(
                "History import request was not accepted",
                translation_domain=DOMAIN,
                translation_key="history_import_in_progress",
            )

    hass.services.async_register(
        DOMAIN,
        SERVICE_IMPORT_SOURCE_HISTORY,
        _handle_import_service,
        schema=vol.Schema(
            {
                vol.Optional("config_entry_id"): cv.string,
                vol.Optional("entity_id"): cv.entity_id,
            }
        ),
    )
    hass.data[DOMAIN]["services_registered"] = True
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EnergyDeviceBridgeConfigEntry) -> bool:
    """Unload a config entry."""
    runtime_data = entry.runtime_data
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        runtime_data.dismiss_all_issues(hass)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: EnergyDeviceBridgeConfigEntry) -> None:
    """Remove config entry and persisted metadata."""
    await EnergyDeviceBridgeStore(hass, entry.entry_id).async_remove()


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: EnergyDeviceBridgeConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Delete device by removing the owning config entry and its entities."""
    if not any(identifier[0] == DOMAIN for identifier in device_entry.identifiers):
        return False
    return await hass.config_entries.async_remove(config_entry.entry_id)
