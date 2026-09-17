"""Smart deadline preset buttons."""

from homeassistant.components.button import ButtonEntity

from .entity import ChargerEntity


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities([
        DeadlinePreset(coordinator, "tomorrow_0700", "Tomorrow 07:00", 1, 7),
        DeadlinePreset(coordinator, "tomorrow_0900", "Tomorrow 09:00", 1, 9),
        DeadlinePreset(coordinator, "day_after_tomorrow_0900", "Day after tomorrow 09:00", 2, 9),
    ])


class DeadlinePreset(ChargerEntity, ButtonEntity):
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator, key, name, days, hour):
        super().__init__(coordinator, key, name)
        self._days = days
        self._hour = hour

    async def async_press(self):
        await self.coordinator.async_set_deadline_preset(self._days, self._hour)
