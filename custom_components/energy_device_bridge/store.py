"""Storage helpers for Energy Device Bridge."""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY_PREFIX, STORAGE_VERSION
from .models import EnergyTrackerState


class EnergyDeviceBridgeStore:
    """Persist virtual energy metadata per config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, float | int | str | None]] = Store(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}{entry_id}",
        )
        self._pending_data: dict[str, float | int | str | None] | None = None

    async def async_load(self) -> EnergyTrackerState | None:
        """Load stored tracker state."""
        data = await self._store.async_load()
        if data is None:
            return None
        return EnergyTrackerState.from_dict(data)

    async def async_save(self, state: EnergyTrackerState) -> None:
        """Save tracker state."""
        await self._store.async_save(state.as_dict())

    def async_schedule_save(
        self,
        state: EnergyTrackerState,
        *,
        delay: float = 1.0,
    ) -> None:
        """Coalesce frequent updates into a delayed storage write."""
        self._pending_data = state.as_dict()
        self._store.async_delay_save(self._async_get_pending_data, delay)

    @callback
    def _async_get_pending_data(self) -> dict[str, float | int | str | None]:
        """Return latest pending state for delayed save."""
        return self._pending_data or {}

    async def async_flush_pending(self, state: EnergyTrackerState | None = None) -> None:
        """Flush latest known state immediately."""
        if state is not None:
            self._pending_data = state.as_dict()
        if self._pending_data is None:
            return
        await self._store.async_save(self._pending_data)

    async def async_remove(self) -> None:
        """Remove persisted tracker state."""
        await self._store.async_remove()
