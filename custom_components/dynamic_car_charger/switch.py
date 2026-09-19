"""Explicit control ownership; off requests a charger pause."""

from homeassistant.components.switch import SwitchEntity

from .entity import ChargerEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([
        AutomaticCharging(entry.runtime_data),
        ImmediateCharging(entry.runtime_data),
    ])


class AutomaticCharging(ChargerEntity, SwitchEntity):
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator):
        super().__init__(coordinator, "automatic", "Automatic charging")

    @property
    def is_on(self):
        return self.coordinator.enabled

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_change(enabled=True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_change(enabled=False)


class ImmediateCharging(ChargerEntity, SwitchEntity):
    """Charge immediately until the configured target percentage is reached."""

    _attr_icon = "mdi:flash"

    def __init__(self, coordinator):
        super().__init__(coordinator, "immediate_charging", "Charge now to target")

    @property
    def is_on(self):
        return self.coordinator.immediate_charging

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_change(immediate_charging=True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_change(immediate_charging=False)
