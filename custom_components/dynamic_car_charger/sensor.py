"""Plan and cost entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory

from .entity import ChargerEntity


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities(
        [
            PlanSensor(coordinator),
            CostSensor(coordinator),
            SessionCostSensor(coordinator),
            PriceForecastSensor(coordinator),
        ]
    )


class PlanSensor(ChargerEntity, SensorEntity):
    _attr_icon = "mdi:calendar-clock"
    # These change on nearly every 15-second update while charging. They stay
    # available on the live state, but are not written to the history database.
    _unrecorded_attributes = frozenset(
        {"slots", "estimated_soc", "active_charge_until", "prices", "setup"}
    )

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


class PriceForecastSensor(ChargerEntity, SensorEntity):
    """State of the price forecast, with the estimated all-in prices."""

    _attr_icon = "mdi:weather-windy"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({"estimates"})

    def __init__(self, coordinator):
        super().__init__(coordinator, "price_forecast_status")

    @property
    def native_value(self) -> str:
        if not self.coordinator.use_forecast:
            return "off"
        return self.coordinator.forecaster.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        forecaster = self.coordinator.forecaster
        calibration = self.coordinator.forecast_calibration
        model = forecaster.model
        attributes: dict[str, Any] = {
            "error": forecaster.error,
            "trained_at": forecaster.trained_at.isoformat() if forecaster.trained_at else None,
            "estimated_at": (
                forecaster.estimated_at.isoformat() if forecaster.estimated_at else None
            ),
            "last_market_day": (
                model.last_known_day.isoformat() if model and model.last_known_day else None
            ),
            "calibration_slope": round(calibration.slope, 4) if calibration else None,
            "calibration_offset": round(calibration.offset, 4) if calibration else None,
            "estimates": [],
        }
        if calibration and model and model.last_known_day:
            # Only hours after the last published market day are estimates.
            attributes["estimates"] = [
                {"start": hour.isoformat(), "price_eur_kwh": round(calibration.apply(price), 4)}
                for hour, price in sorted(forecaster.estimates.items())
                if model.local_date(hour) > model.last_known_day
            ]
        return attributes
