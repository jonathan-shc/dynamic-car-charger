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
            PriceForecast(entry.runtime_data),
            CheapOnly(entry.runtime_data),
            CheapAfterDeadline(entry.runtime_data),
            WeeklySchedule(entry.runtime_data),
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


class CheapOnly(ChargerEntity, SwitchEntity):
    """No deadline: charge to the target only in hours at or below the cheap price.
    Turning it on turns automatic charging on too."""

    _attr_icon = "mdi:piggy-bank-outline"

    def __init__(self, coordinator):
        super().__init__(coordinator, "cheap_only")

    @property
    def is_on(self) -> bool:
        return self.coordinator.cheap_only

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_change(cheap_only=True, enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_change(cheap_only=False)


class CheapAfterDeadline(ChargerEntity, SwitchEntity):
    """Without a deadline, or once it passed, charge as in the cheap-only mode until a
    new deadline is set."""

    _attr_icon = "mdi:piggy-bank-outline"

    def __init__(self, coordinator):
        super().__init__(coordinator, "cheap_after_deadline")

    @property
    def is_on(self) -> bool:
        return self.coordinator.cheap_after_deadline

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_change(cheap_after_deadline=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_change(cheap_after_deadline=False)


class WeeklySchedule(ChargerEntity, SwitchEntity):
    """Follow the weekly schedule: each ready-by time that passes makes way for the next
    scheduled one. The times are set with the set_schedule action."""

    _attr_icon = "mdi:calendar-week"

    def __init__(self, coordinator):
        super().__init__(coordinator, "weekly_schedule")

    @property
    def is_on(self) -> bool:
        return self.coordinator.schedule_enabled

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"days": dict(self.coordinator.schedule)}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_change(schedule_enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_change(schedule_enabled=False)
