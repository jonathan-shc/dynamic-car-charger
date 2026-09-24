"""Target percentage and price threshold controls."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory

from .const import DEFAULTS
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
        super().__init__(coordinator, "target")

    @property
    def native_value(self) -> float:
        return self.coordinator.target

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_change(target=number(value, 0, 100))


class PriceThreshold(ChargerEntity, NumberEntity):
    _attr_native_min_value = 0
    _attr_native_max_value = 5
    _attr_native_step = 0.01
    _attr_native_unit_of_measurement = "EUR/kWh"
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:cash-clock"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator):
        super().__init__(coordinator, "price_threshold")

    @property
    def native_value(self) -> float:
        return self.coordinator.settings.get("max_price_eur_kwh", DEFAULTS["max_price_eur_kwh"])

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_change_price_threshold(number(value, 0, 5))
