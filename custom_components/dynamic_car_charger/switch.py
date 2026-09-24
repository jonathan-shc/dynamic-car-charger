"""Explicit control ownership; off requests a charger pause."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity

from .entity import ChargerEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        [
            AutomaticCharging(entry.runtime_data),
            ImmediateCharging(entry.runtime_data),
        ]
    )


class AutomaticCharging(ChargerEntity, SwitchEntity):
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator):
        super().__init__(coordinator, "automatic")

    @property
    def is_on(self) -> bool:
        return self.coordinator.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_change(enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_change(enabled=False)


class ImmediateCharging(ChargerEntity, SwitchEntity):
    """Charge immediately until the configured target percentage is reached."""

    _attr_icon = "mdi:flash"

    def __init__(self, coordinator):
        super().__init__(coordinator, "immediate_charging")

    @property
    def is_on(self) -> bool:
        return self.coordinator.immediate_charging

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_change(immediate_charging=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_change(immediate_charging=False)


class PriceForecast(ChargerEntity, SwitchEntity):
    """Choose between the price forecast and the price threshold."""

    _attr_icon = "mdi:weather-windy"

    def __init__(self, coordinator):
        super().__init__(coordinator, "price_forecast")

    @property
    def is_on(self) -> bool:
        return self.coordinator.use_forecast

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_change(use_forecast=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_change(use_forecast=False)
