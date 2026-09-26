"""Estimate unpublished electricity prices from recent prices and weather forecasts.

No Home Assistant dependencies. The model is a ridge regression on the hour,
weekday or holiday, the last published day's prices, and forecast wind, solar
radiation and temperature at points that drive the bidding zone's market
(see zones.py). It was chosen with the backtest in tools/backtest on Dutch prices.

Prices here are market prices in EUR/kWh. `fit_calibration` maps them to the
all-in prices of the configured price sensor, in whatever currency it uses.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo

import numpy as np

from .zones import WEATHER_POINTS as WEATHER_POINTS
from .zones import Zone
from .zones import dutch_holidays as dutch_holidays
from .zones import easter as easter
from .zones import zone as zone_for

HOUR = timedelta(hours=1)
DAY = timedelta(days=1)

WEATHER_VARIABLES = ("wind_speed_100m", "shortwave_radiation", "temperature_2m")
# Archived forecasts exist for 1 to 6 days before each hour.
MAX_LEAD_DAYS = 6
TRAIN_DAYS = 365
MIN_TRAIN_HOURS = 30 * 24
RIDGE_ALPHA = 3.0


def is_day_off(day: date, code: str | None = None) -> bool:
    """Weekends and public holidays behave alike on the power market."""
    return zone_for(code).is_day_off(day)


@dataclass(frozen=True)
class Calibration:
    """all-in price = slope x market price + offset."""

    slope: float
    offset: float
    points: int

    def apply(self, market: float) -> float:
        return self.slope * market + self.offset


def fit_calibration(pairs: list[tuple[float, float]], euro: bool = True) -> Calibration | None:
    """Fit the all-in price from market prices for hours where both are known.

    Returns None when there are too few hours or the relation is not the
    linear one expected from VAT, taxes and supplier fees. Prices in another
    currency than the euro market prices also carry the exchange rate, so the
    slope can be anything positive and the allowed error scales with it.
    """
    if len(pairs) < 12:
        return None
    market = np.array([p[0] for p in pairs])
    all_in = np.array([p[1] for p in pairs])
    if market.std() < 1e-4:
        return None
    slope, offset = np.polyfit(market, all_in, 1)
    residual = all_in - (slope * market + offset)
    error = float(np.sqrt(np.mean(residual**2)))
    if euro and (not 0.5 <= slope <= 2.0 or error > 0.01):
        return None
    if not euro and (slope <= 0 or error > 0.01 * slope):
        return None
    return Calibration(float(slope), float(offset), len(pairs))


class PriceModel:
    """Train on market price history and archived weather forecasts."""

    def __init__(
        self,
        tz: tzinfo,
        market: dict[datetime, float],
        archive: dict[datetime, dict[str, float]],
        zone: Zone | None = None,
    ) -> None:
        self.tz = tz
        self.zone = zone or zone_for(None)
        self.market = market
        self.archive = archive
        by_day: dict[date, list[float]] = {}
        for hour, price in market.items():
            by_day.setdefault(self.local_date(hour), []).append(price)
        # A complete local day has 23 to 25 hours around daylight-saving changes.
        self.day_mean = {day: sum(v) / len(v) for day, v in by_day.items() if len(v) >= 23}
        self.last_known_day = max(self.day_mean) if self.day_mean else None
        self._models: dict[tuple[int, int], tuple[np.ndarray, ...] | None] = {}

    def local_date(self, hour: datetime) -> date:
        return hour.astimezone(self.tz).date()

    def day_start(self, day: date) -> datetime:
        return datetime(day.year, day.month, day.day, tzinfo=self.tz).astimezone(UTC)

    def _features(
        self, hour: datetime, lag: int, weather: dict[str, float] | None, suffix: str
    ) -> list[float] | None:
        day = self.local_date(hour)
        lag_mean = self.day_mean.get(day - timedelta(days=lag))
        lag_price = self.market.get(hour - lag * 24 * HOUR)
        if lag_mean is None or lag_price is None or weather is None:
            return None
        local_hour = hour.astimezone(self.tz).hour
        row = [1.0 if local_hour == h else 0.0 for h in range(24)]
        row += [1.0 if self.zone.is_day_off(day) else 0.0, lag_mean, lag_price]
        try:
            for point in self.zone.wind:
                wind = weather[f"{point}.wind_speed_100m{suffix}"] / 10
                row += [wind, wind * wind]
            for point in self.zone.solar:
                row.append(weather[f"{point}.shortwave_radiation{suffix}"] / 100)
            for point in self.zone.temperature:
                row.append(weather[f"{point}.temperature_2m{suffix}"] / 10)
        except (KeyError, TypeError):
            return None
        return row

    def _fit(self, lag: int, lead: int):
        """Ridge regression for one (lag, lead) pair, on the last year."""
        key = (lag, lead)
        if key in self._models:
            return self._models[key]
        end = self.day_start(self.last_known_day + DAY)
        start = end - timedelta(days=TRAIN_DAYS)
        rows, targets = [], []
        suffix = f"_previous_day{lead}"
        for hour, price in self.market.items():
            if not start <= hour < end:
                continue
            row = self._features(hour, lag, self.archive.get(hour), suffix)
            if row is not None:
                rows.append(row)
                targets.append(price)
        model = None
        if len(rows) >= MIN_TRAIN_HOURS:
            x, y = np.array(rows), np.array(targets)
            mean, scale = x.mean(axis=0), x.std(axis=0)
            scale[scale == 0] = 1.0
            z = (x - mean) / scale
            y_mean = y.mean()
            beta = np.linalg.solve(z.T @ z + RIDGE_ALPHA * np.eye(z.shape[1]), z.T @ (y - y_mean))
            model = (mean, scale, beta, float(y_mean))
        self._models[key] = model
        return model

    def predict(
        self, now: datetime, hours: list[datetime], live: dict[datetime, dict[str, float]]
    ) -> dict[datetime, float]:
        """Market price per hour: published prices where known, else estimates.

        `live` holds the current weather forecast per hour. An hour is left out
        when it is too far ahead or its inputs are missing.
        """
        if self.last_known_day is None:
            return {}
        result = {}
        for hour in hours:
            day = self.local_date(hour)
            if day <= self.last_known_day:
                if hour in self.market:
                    result[hour] = self.market[hour]
                continue
            lead = max(1, math.ceil((hour - now) / DAY))
            if lead > MAX_LEAD_DAYS:
                continue
            lag = (day - self.last_known_day).days
            row = self._features(hour, lag, live.get(hour), "")
            model = self._fit(lag, lead) if row is not None else None
            if model is None:
                continue
            mean, scale, beta, y_mean = model
            result[hour] = float(((np.array(row) - mean) / scale) @ beta + y_mean)
        return result
