"""Smart deadline preset buttons, and a new-charge button in energy mode."""

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
    buttons = [DeadlinePreset(coordinator, key, days, hour) for key, days, hour in PRESETS]
    if coordinator.energy_mode:
        buttons.append(NewCharge(coordinator))
    async_add_entities(buttons)


class NewCharge(ChargerEntity, ButtonEntity):
    """Energy mode: count the energy to charge from zero again."""

    _attr_icon = "mdi:restart"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "new_charge")

    async def async_press(self) -> None:
        await self.coordinator.async_new_charge()


class DeadlinePreset(ChargerEntity, ButtonEntity):
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator, key: str, days: int, hour: int) -> None:
        super().__init__(coordinator, key)
        self._days = days
        self._hour = hour

    async def async_press(self) -> None:
        await self.coordinator.async_set_deadline_preset(self._days, self._hour)
