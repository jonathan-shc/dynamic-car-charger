"""Target percentage control."""

from homeassistant.components.number import NumberEntity, NumberMode

from .entity import ChargerEntity
from .planner import number


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([Target(entry.runtime_data), PriceThreshold(entry.runtime_data)])


class Target(ChargerEntity, NumberEntity):
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:battery-charging-80"

    def __init__(self, coordinator):
        super().__init__(coordinator, "target", "Target charge")

    @property
    def native_value(self):
        return self.coordinator.target

    async def async_set_native_value(self, value):
        await self.coordinator.async_change(target=number(value, 0, 100))


class PriceThreshold(ChargerEntity, NumberEntity):
    _attr_native_min_value = 0
    _attr_native_max_value = 5
    _attr_native_step = 0.001
    _attr_native_unit_of_measurement = "EUR/kWh"
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:cash-clock"

    def __init__(self, coordinator):
        super().__init__(coordinator, "price_threshold", "Charging price threshold")

    @property
    def native_value(self):
        return self.coordinator.settings.get("max_price_eur_kwh", 0.20)

    async def async_set_native_value(self, value):
        await self.coordinator.async_change_price_threshold(number(value, 0, 5))
