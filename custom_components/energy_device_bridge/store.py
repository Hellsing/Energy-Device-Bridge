"""Storage helpers for Energy Device Bridge."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY_PREFIX, STORAGE_VERSION
from .models import EnergyTrackerState


class EnergyDeviceBridgeStore:
    """Persist virtual energy metadata per config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, float | str | None]] = Store(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}{entry_id}",
        )

    async def async_load(self) -> EnergyTrackerState | None:
        """Load stored tracker state."""
        data = await self._store.async_load()
        if data is None:
            return None
        return EnergyTrackerState.from_dict(data)

    async def async_save(self, state: EnergyTrackerState) -> None:
        """Save tracker state."""
        await self._store.async_save(state.as_dict())

    async def async_remove(self) -> None:
        """Remove persisted tracker state."""
        await self._store.async_remove()
