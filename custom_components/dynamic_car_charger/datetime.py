"""Full date and time deadline control."""

from homeassistant.components.datetime import DateTimeEntity

from .entity import ChargerEntity
from .planner import timestamp


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([Deadline(entry.runtime_data)])


class Deadline(ChargerEntity, DateTimeEntity):
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator):
        super().__init__(coordinator, "deadline", "Ready by")

    @property
    def native_value(self):
        return self.coordinator.deadline

    async def async_set_value(self, value):
        await self.coordinator.async_change(deadline=timestamp(value))
