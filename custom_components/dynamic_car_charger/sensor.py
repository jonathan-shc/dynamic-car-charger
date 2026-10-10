"""Plan and cost entities."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.util import dt as dt_util

from .entity import ChargerEntity
from .forecaster import forecast_error_code


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities(
        [
            PlanSensor(coordinator),
            CostSensor(coordinator),
            SessionCostSensor(coordinator),
            PriceForecastSensor(coordinator),
            *(
                [
                    BikeStatusSensor(coordinator, "bike_state"),
                    BikeStatusSensor(coordinator, "bike_cable"),
                ]
                if coordinator.bike
                else []
            ),
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
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator):
        super().__init__(coordinator, "cost")

    @property
    def native_unit_of_measurement(self) -> str:
        """The currency of the price sensor."""
        return self.coordinator.currency

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get("estimated_cost_eur")


class SessionCostSensor(ChargerEntity, SensorEntity):
    """Measured cost of the current or last finished charging session."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator):
        super().__init__(coordinator, "session_cost")

    @property
    def native_unit_of_measurement(self) -> str:
        """The currency of the price sensor."""
        return self.coordinator.currency

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
            "error_code": forecast_error_code(forecaster.error),
            "trained_at": forecaster.trained_at.isoformat() if forecaster.trained_at else None,
            "estimated_at": (
                forecaster.estimated_at.isoformat() if forecaster.estimated_at else None
            ),
            "market_source": forecaster.market_source,
            # When each source first had the next day's prices, the last week.
            "market_arrivals": forecaster.arrivals[-7:],
            "last_market_day": (
                model.last_known_day.isoformat() if model and model.last_known_day else None
            ),
            "calibration_slope": round(calibration.slope, 4) if calibration else None,
            "calibration_offset": round(calibration.offset, 4) if calibration else None,
            "warning": self.coordinator.forecast_warning,
            "calibration_reused": self.coordinator.forecast_warning is not None,
            "calibration_validated_at": (
                (self.coordinator._saved_forecast_calibration or {}).get("validated_at")
            ),
            "estimates": [],
        }
        if calibration and model and model.last_known_day:
            # Every hour after the last published all-in price. Between the market
            # publishing the next day (about 13:00) and the price sensor having it, those
            # are the market's own prices, calibrated: without them the next day was empty.
            published_until = self._published_until()
            attributes["estimates"] = [
                {"start": hour.isoformat(), "price_eur_kwh": round(calibration.apply(price), 4)}
                for hour, price in sorted(forecaster.estimates.items())
                if (
                    (hour >= published_until or self._has_price_gap(hour))
                    if published_until
                    else model.local_date(hour) > model.last_known_day
                )
            ]
        return attributes

    def _has_price_gap(self, hour: datetime) -> bool:
        """Include an estimate whenever any interval inside this hour is unpublished."""
        end = hour + timedelta(hours=1)
        cursor = hour
        intervals = []
        for row in (self.coordinator.data or {}).get("prices") or []:
            start, stop = (
                dt_util.parse_datetime(row.get("start", "")),
                dt_util.parse_datetime(row.get("end", "")),
            )
            if start is not None and stop is not None and start < end and stop > hour:
                intervals.append((start, stop))
        for start, stop in sorted(intervals):
            if start > cursor:
                return True
            cursor = max(cursor, stop)
            if cursor >= end:
                return False
        return cursor < end

    def _published_until(self) -> datetime | None:
        """The end of the last all-in price the price sensor has, from the plan."""
        ends = [
            dt_util.parse_datetime(row["end"])
            for row in (self.coordinator.data or {}).get("prices") or []
            if row.get("end")
        ]
        return max((end for end in ends if end is not None), default=None)


class BikeStatusSensor(ChargerEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_icon = "mdi:bicycle"

    def __init__(self, coordinator, key):
        super().__init__(coordinator, key)
        self.key = "state" if key == "bike_state" else "cable"
        self._attr_options = (
            ["unknown", "home_on", "home_off", "home_unreachable", "departing", "away", "arriving"]
            if self.key == "state"
            else ["unknown", "connected", "disconnected"]
        )

    @property
    def native_value(self):
        return self.coordinator.data.get("bike", {}).get(self.key, "unknown")

    @property
    def extra_state_attributes(self):
        return self.coordinator.data.get("bike", {})
