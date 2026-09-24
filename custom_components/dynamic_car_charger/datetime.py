"""Full date and time deadline control."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.datetime import DateTimeEntity

from .entity import ChargerEntity
from .planner import timestamp


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([Deadline(entry.runtime_data)])


class Deadline(ChargerEntity, DateTimeEntity):
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator):
        super().__init__(coordinator, "deadline")

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.deadline

    async def async_set_value(self, value: datetime) -> None:
        await self.coordinator.async_change(deadline=timestamp(value))
