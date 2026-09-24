"""Estimate unpublished electricity prices from recent prices and weather forecasts.

No Home Assistant dependencies. The model is a ridge regression on the hour,
weekday or holiday, the last published day's prices, and forecast wind, solar
radiation and temperature at points that drive the Dutch and German market.
It was chosen with the backtest in tools/backtest.

Prices here are market prices in EUR/kWh. `fit_calibration` maps them to the
all-in prices of the configured price sensor.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo

import numpy as np

HOUR = timedelta(hours=1)
DAY = timedelta(days=1)

# Fixed points in the coupled NL/DE market. These are not the user's location.
WEATHER_POINTS = {
    "nl_offshore": (53.5, 4.5),
    "nl_onshore": (52.9, 5.9),
    "de_north": (54.0, 9.0),
    "de_central": (51.0, 10.0),
}
WEATHER_VARIABLES = ("wind_speed_100m", "shortwave_radiation", "temperature_2m")
WIND_POINTS = ("nl_offshore", "nl_onshore", "de_north", "de_central")
SOLAR_POINTS = ("nl_onshore", "de_central", "de_north")
TEMPERATURE_POINTS = ("nl_onshore", "de_central")
# Archived forecasts exist for 1 to 6 days before each hour.
MAX_LEAD_DAYS = 6
TRAIN_DAYS = 365
MIN_TRAIN_HOURS = 30 * 24
RIDGE_ALPHA = 3.0


def easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def dutch_holidays(year: int) -> set[date]:
    easter_sunday = easter(year)
    kings_day = date(year, 4, 27)
    if kings_day.weekday() == 6:
        kings_day = date(year, 4, 26)
    return {
        date(year, 1, 1),
        easter_sunday + timedelta(days=1),
        kings_day,
        easter_sunday + timedelta(days=39),
        easter_sunday + timedelta(days=50),
        date(year, 12, 25),
        date(year, 12, 26),
    }


def is_day_off(day: date) -> bool:
    """Weekends and public holidays behave alike on the power market."""
    return day.weekday() >= 5 or day in dutch_holidays(day.year)


@dataclass(frozen=True)
class Calibration:
    """all-in price = slope x market price + offset."""

    slope: float
    offset: float
    points: int

    def apply(self, market: float) -> float:
        return self.slope * market + self.offset


def fit_calibration(pairs: list[tuple[float, float]]) -> Calibration | None:
    """Fit the all-in price from market prices for hours where both are known.

    Returns None when there are too few hours or the relation is not the
    linear one expected from VAT, taxes and supplier fees.
    """
    if len(pairs) < 12:
        return None
    market = np.array([p[0] for p in pairs])
    all_in = np.array([p[1] for p in pairs])
    if market.std() < 1e-4:
        return None
    slope, offset = np.polyfit(market, all_in, 1)
    residual = all_in - (slope * market + offset)
    if not 0.5 <= slope <= 2.0 or float(np.sqrt(np.mean(residual**2))) > 0.01:
        return None
    return Calibration(float(slope), float(offset), len(pairs))


class PriceModel:
    """Train on market price history and archived weather forecasts."""

    def __init__(
        self,
        tz: tzinfo,
        market: dict[datetime, float],
        archive: dict[datetime, dict[str, float]],
    ) -> None:
        self.tz = tz
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
        row += [1.0 if is_day_off(day) else 0.0, lag_mean, lag_price]
        try:
            for point in WIND_POINTS:
                wind = weather[f"{point}.wind_speed_100m{suffix}"] / 10
                row += [wind, wind * wind]
            for point in SOLAR_POINTS:
                row.append(weather[f"{point}.shortwave_radiation{suffix}"] / 100)
            for point in TEMPERATURE_POINTS:
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
