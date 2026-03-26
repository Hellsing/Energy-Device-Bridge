"""The Energy Device Bridge integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN, PLATFORMS
from .models import ConsumerConfig, resolve_consumer_config
from .store import EnergyDeviceBridgeStore


@dataclass(slots=True)
class EnergyDeviceBridgeRuntimeData:
    """Runtime objects for a config entry."""

    consumer: ConsumerConfig
    store: EnergyDeviceBridgeStore
    device_info: DeviceInfo


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
        store=EnergyDeviceBridgeStore(hass, entry.entry_id),
        device_info=device_info,
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EnergyDeviceBridgeConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


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
