"""Plan and cost entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity

from .entity import ChargerEntity


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities(
        [
            PlanSensor(coordinator),
            CostSensor(coordinator),
            SessionCostSensor(coordinator),
        ]
    )


class PlanSensor(ChargerEntity, SensorEntity):
    _attr_icon = "mdi:calendar-clock"
    # These change on nearly every 15-second update while charging. They stay
    # available on the live state, but are not written to the history database.
    _unrecorded_attributes = frozenset({"slots", "estimated_soc", "active_charge_until"})

    def __init__(self, coordinator):
        super().__init__(coordinator, "plan")

    @property
    def native_value(self) -> str:
        return self.coordinator.data["status"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.coordinator.data


class CostSensor(ChargerEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "EUR"
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator):
        super().__init__(coordinator, "cost")

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get("estimated_cost_eur")


class SessionCostSensor(ChargerEntity, SensorEntity):
    """Measured cost of the current or last finished charging session."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "EUR"
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator):
        super().__init__(coordinator, "session_cost")

    @property
    def native_value(self) -> float | None:
        session = self.coordinator.session
        return round(session["cost_eur"], 2) if session else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        session = self.coordinator.session
        if not session:
            return {}
        energy = session["energy_kwh"]
        return {
            "active": session["active"],
            "started": session["started"],
            "ended": session["ended"],
            "energy_kwh": round(energy, 2),
            "average_price_eur_kwh": (
                round(session["cost_eur"] / energy, 4) if energy > 0 else None
            ),
            "cost_complete": session["cost_complete"],
        }
