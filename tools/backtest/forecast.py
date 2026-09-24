"""Price data and forecast models for the backtest.

Everything works in all-in NextEnergy prices (EUR/kWh). Forecasts only use data
that was available at the decision time: prices published up to `known_until`
and weather forecasts made `lead` days before the target hour.
"""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

DATA = Path(__file__).parent / "data"
LOCAL = ZoneInfo("Europe/Amsterdam")
HOUR = timedelta(hours=1)

# Fitted on 48 hours of Enever NextEnergy prices against Energy-Charts market
# prices (24-25 September 2026): all-in = (market + 0.10967) x 1.21, exact.
VAT = 1.21
ALL_IN_OFFSET = 0.1327
# Next-day prices are available in Enever from about 15:00 local time.
PUBLICATION_HOUR = 15

# Public holidays behave like Sundays on the power market.
HOLIDAYS = {
    date(2024, 1, 1), date(2024, 4, 1), date(2024, 4, 27), date(2024, 5, 9),
    date(2024, 5, 20), date(2024, 12, 25), date(2024, 12, 26),
    date(2025, 1, 1), date(2025, 4, 21), date(2025, 4, 26), date(2025, 5, 29),
    date(2025, 6, 9), date(2025, 12, 25), date(2025, 12, 26),
    date(2026, 1, 1), date(2026, 4, 6), date(2026, 4, 27), date(2026, 5, 14),
    date(2026, 5, 25), date(2026, 12, 25), date(2026, 12, 26),
}  # fmt: skip

WIND_POINTS = ("nl_offshore", "nl_onshore", "de_north", "de_central")
SOLAR_POINTS = ("nl_onshore", "de_central", "de_north")
TEMPERATURE_POINTS = ("nl_onshore", "de_central")


def all_in(market: float) -> float:
    return market * VAT + ALL_IN_OFFSET


def local_date(hour: datetime) -> date:
    return hour.astimezone(LOCAL).date()


def day_start(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=LOCAL).astimezone(UTC)


def known_until(now: datetime) -> datetime:
    """First hour whose price is not yet published at `now`."""
    today = local_date(now)
    published_days = 1 if now.astimezone(LOCAL).hour < PUBLICATION_HOUR else 2
    return day_start(today + timedelta(days=published_days))


def is_day_off(day: date) -> bool:
    return day.weekday() >= 5 or day in HOLIDAYS


class History:
    """Hourly all-in prices and archived weather forecasts."""

    def __init__(self) -> None:
        self.prices: dict[datetime, float] = {}
        with open(DATA / "prices.csv") as file:
            for row in csv.DictReader(file):
                hour = datetime.fromisoformat(row["utc_hour"])
                self.prices[hour] = all_in(float(row["market_eur_kwh"]))
        self.weather: dict[datetime, dict[str, float]] = {}
        with open(DATA / "weather.csv") as file:
            for row in csv.DictReader(file):
                hour = datetime.fromisoformat(row.pop("utc_hour"))
                self.weather[hour] = {k: float(v) for k, v in row.items() if v != ""}
        by_day: dict[date, list[float]] = defaultdict(list)
        for hour, price in self.prices.items():
            by_day[local_date(hour)].append(price)
        self.day_mean = {day: sum(v) / len(v) for day, v in by_day.items() if len(v) >= 23}
        self.first_hour = min(self.prices)
        self.last_hour = max(self.prices)


class ProfileModel:
    """Last known day's level plus the typical hourly shape of recent weeks."""

    name = "profile"

    def __init__(self, history: History, weeks: int = 4) -> None:
        self.history = history
        self.weeks = weeks
        self._cache: dict[datetime, tuple[float, dict]] = {}

    def _fit(self, until: datetime) -> tuple[float, dict]:
        if until not in self._cache:
            last_day = local_date(until - HOUR)
            level = self.history.day_mean[last_day]
            deviations: dict[tuple[int, bool], list[float]] = defaultdict(list)
            for offset in range(self.weeks * 7):
                day = last_day - timedelta(days=offset)
                mean = self.history.day_mean.get(day)
                if mean is None:
                    continue
                hour = day_start(day)
                while local_date(hour) == day:
                    local = hour.astimezone(LOCAL)
                    price = self.history.prices[hour]
                    deviations[(local.hour, is_day_off(day))].append(price - mean)
                    hour += HOUR
            shape = {key: sum(v) / len(v) for key, v in deviations.items()}
            self._cache[until] = (level, shape)
        return self._cache[until]

    def predict(self, now: datetime, hours: list[datetime]) -> dict[datetime, float]:
        level, shape = self._fit(known_until(now))
        result = {}
        for hour in hours:
            day = local_date(hour)
            key = (hour.astimezone(LOCAL).hour, is_day_off(day))
            result[hour] = level + shape.get(key, 0.0)
        return result


class WeatherModel:
    """Ridge regression on hour, day type, recent prices and weather forecasts.

    One model per (lag, lead): lag = days between the target and the last day
    with known prices; lead = how many days before the target hour the weather
    forecast was made, rounded up so it was available at the decision time.
    """

    name = "weather"

    def __init__(
        self, history: History, train_days: int = 365, retrain_days: int = 7, alpha: float = 3.0
    ) -> None:
        self.history = history
        self.train_days = train_days
        self.retrain_days = retrain_days
        self.alpha = alpha
        self._tables: dict[tuple[int, int], tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        self._models: dict[tuple[int, int, date], tuple[np.ndarray, ...]] = {}

    def _features(self, hour: datetime, lag: int, lead: int) -> list[float] | None:
        history = self.history
        day = local_date(hour)
        lag_mean = history.day_mean.get(day - timedelta(days=lag))
        lag_price = history.prices.get(hour - lag * 24 * HOUR)
        weather = history.weather.get(hour)
        if lag_mean is None or lag_price is None or weather is None:
            return None
        local_hour = hour.astimezone(LOCAL).hour
        row = [1.0 if local_hour == h else 0.0 for h in range(24)]
        row += [1.0 if is_day_off(day) else 0.0, lag_mean, lag_price]
        suffix = f"_previous_day{lead}"
        try:
            for point in WIND_POINTS:
                wind = weather[f"{point}.wind_speed_100m{suffix}"] / 10
                row += [wind, wind * wind]
            for point in SOLAR_POINTS:
                row.append(weather[f"{point}.shortwave_radiation{suffix}"] / 100)
            for point in TEMPERATURE_POINTS:
                row.append(weather[f"{point}.temperature_2m{suffix}"] / 10)
        except KeyError:
            return None
        return row

    def _table(self, lag: int, lead: int):
        """Features and targets for every historical hour, built once per pair."""
        key = (lag, lead)
        if key not in self._tables:
            hours, rows, targets = [], [], []
            for hour, price in sorted(self.history.prices.items()):
                row = self._features(hour, lag, lead)
                if row is not None:
                    hours.append(hour)
                    rows.append(row)
                    targets.append(price)
            stamps = np.array([h.timestamp() for h in hours])
            self._tables[key] = (stamps, np.array(rows), np.array(targets))
        return self._tables[key]

    def _model(self, until: datetime, lag: int, lead: int):
        # Retrain weekly: the model for a decision uses a fit from at most a
        # week earlier, only on hours that were known at that time.
        epoch = local_date(until)
        epoch -= timedelta(days=epoch.toordinal() % self.retrain_days)
        key = (lag, lead, epoch)
        if key not in self._models:
            stamps, x, y = self._table(lag, lead)
            end = min(day_start(epoch), until)
            start = end - timedelta(days=self.train_days)
            lo = np.searchsorted(stamps, start.timestamp())
            hi = np.searchsorted(stamps, end.timestamp())
            x_train, y_train = x[lo:hi], y[lo:hi]
            mean, scale = x_train.mean(axis=0), x_train.std(axis=0)
            scale[scale == 0] = 1.0
            z = (x_train - mean) / scale
            y_mean = y_train.mean()
            beta = np.linalg.solve(
                z.T @ z + self.alpha * np.eye(z.shape[1]), z.T @ (y_train - y_mean)
            )
            self._models[key] = (mean, scale, beta, y_mean)
        return self._models[key]

    def predict(self, now: datetime, hours: list[datetime]) -> dict[datetime, float]:
        until = known_until(now)
        last_known_day = local_date(until - HOUR)
        result = {}
        for hour in hours:
            day = local_date(hour)
            lag = (day - last_known_day).days
            # Archived "previous_dayN" forecasts were made about N x 24 hours
            # before the hour. Only use one that already existed at `now`.
            lead = min(3, max(1, math.ceil((hour - now) / timedelta(days=1))))
            row = self._features(hour, lag, lead)
            if row is None:
                continue
            mean, scale, beta, y_mean = self._model(until, lag, lead)
            result[hour] = float(((np.array(row) - mean) / scale) @ beta + y_mean)
        return result
