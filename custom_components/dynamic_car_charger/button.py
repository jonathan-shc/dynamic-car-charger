"""Smart deadline preset buttons."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity

from .entity import ChargerEntity

PRESETS = (
    ("tomorrow_0700", 1, 7),
    ("tomorrow_0900", 1, 9),
    ("day_after_tomorrow_0900", 2, 9),
)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities(DeadlinePreset(coordinator, key, days, hour) for key, days, hour in PRESETS)


class DeadlinePreset(ChargerEntity, ButtonEntity):
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator, key: str, days: int, hour: int) -> None:
        super().__init__(coordinator, key)
        self._days = days
        self._hour = hour

    async def async_press(self) -> None:
        await self.coordinator.async_set_deadline_preset(self._days, self._hour)
