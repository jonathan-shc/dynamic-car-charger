"""Target (battery percentage or energy to charge) and price threshold controls."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory

from .const import DEFAULTS
from .entity import ChargerEntity
from .planner import number


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    # Without a battery sensor the target is an amount of energy, not a percentage.
    target = EnergyGoal(coordinator) if coordinator.energy_mode else Target(coordinator)
    async_add_entities([target, PriceThreshold(coordinator)])


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


class EnergyGoal(ChargerEntity, NumberEntity):
    """Energy mode: how much energy to charge before the deadline."""

    _attr_native_min_value = 0
    _attr_native_max_value = 200
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = "kWh"
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:lightning-bolt"

    def __init__(self, coordinator):
        super().__init__(coordinator, "energy_goal")

    @property
    def native_value(self) -> float:
        return self.coordinator.energy_goal

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_change(energy_goal=number(value, 0, 200))


class PriceThreshold(ChargerEntity, NumberEntity):
    _attr_native_min_value = 0
    _attr_native_max_value = 5
    _attr_native_step = 0.01
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:cash-clock"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator):
        super().__init__(coordinator, "price_threshold")

    @property
    def native_unit_of_measurement(self) -> str:
        return f"{self.coordinator.currency}/kWh"

    @property
    def native_value(self) -> float:
        return self.coordinator.settings.get("max_price_eur_kwh", DEFAULTS["max_price_eur_kwh"])

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_change_price_threshold(number(value, 0, 5))
