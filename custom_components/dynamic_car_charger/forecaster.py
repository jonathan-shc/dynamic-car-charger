"""Fetch price history and weather forecasts, and keep price estimates current.

Sources, no API keys needed:
- Energy-Charts (Fraunhofer ISE): Dutch day-ahead market prices, CC BY 4.0,
  source Bundesnetzagentur | SMARD.de.
- Open-Meteo: live weather forecasts, and archived forecasts to train on.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .planner import Slot
from .price_forecast import (
    HOUR,
    MAX_LEAD_DAYS,
    TRAIN_DAYS,
    WEATHER_POINTS,
    WEATHER_VARIABLES,
    Calibration,
    PriceModel,
    fit_calibration,
)

_LOGGER = logging.getLogger(__name__)

PRICE_URL = "https://api.energy-charts.info/price"
BIDDING_ZONE = "NL"
ARCHIVE_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

HISTORY_REFRESH = timedelta(hours=24)
# Next-day market prices are usually published shortly after 13:00.
MARKET_PUBLICATION_HOUR = 13
LIVE_REFRESH = timedelta(hours=1)
RETRY_AFTER = timedelta(minutes=15)

FetchJson = Callable[[str, dict[str, Any]], Awaitable[Any]]


class ForecastUnavailable(Exception):
    """Estimates cannot be made right now; plan with the threshold instead."""


def parse_market(data: dict) -> dict[datetime, float]:
    """Energy-Charts EUR/MWh (hourly or quarter-hourly) to hourly EUR/kWh."""
    hourly: dict[datetime, list[float]] = {}
    for stamp, price in zip(data["unix_seconds"], data["price"], strict=True):
        if price is not None:
            hour = datetime.fromtimestamp(stamp, UTC).replace(minute=0)
            hourly.setdefault(hour, []).append(price / 1000)
    return {hour: sum(v) / len(v) for hour, v in hourly.items()}


def parse_weather(data: dict, point: str, suffixes: list[str]) -> dict[datetime, dict[str, float]]:
    """Open-Meteo hourly data to {hour: {"point.variable<suffix>": value}}."""
    hourly = data["hourly"]
    result: dict[datetime, dict[str, float]] = {}
    for index, stamp in enumerate(hourly["time"]):
        hour = datetime.fromisoformat(stamp).replace(tzinfo=UTC)
        row = result.setdefault(hour, {})
        for variable in WEATHER_VARIABLES:
            for suffix in suffixes:
                value = hourly[f"{variable}{suffix}"][index]
                if value is not None:
                    row[f"{point}.{variable}{suffix}"] = value
    return result


class PriceForecaster:
    """Keeps market history, weather forecasts and a trained model in memory."""

    def __init__(self, hass: HomeAssistant, fetch_json: FetchJson | None = None) -> None:
        self.hass = hass
        self._fetch_json = fetch_json or self._default_fetch
        self.market: dict[datetime, float] = {}
        self.archive: dict[datetime, dict[str, float]] = {}
        self.live: dict[datetime, dict[str, float]] = {}
        self.model: PriceModel | None = None
        self.status = "off"
        self.error: str | None = None
        self.trained_at: datetime | None = None
        self.estimates: dict[datetime, float] = {}
        self.estimated_at: datetime | None = None
        self._market_at: datetime | None = None
        self._archive_at: datetime | None = None
        self._live_at: datetime | None = None
        self._failed_at: datetime | None = None
        self._lock = asyncio.Lock()

    async def _default_fetch(self, url: str, params: dict[str, Any]) -> Any:
        session = async_get_clientsession(self.hass)
        async with asyncio.timeout(60):
            response = await session.get(url, params=params)
            response.raise_for_status()
            return await response.json()

    def _market_outdated(self, now: datetime) -> bool:
        if self._market_at is None or now - self._market_at >= HISTORY_REFRESH:
            return True
        # After publication, fetch again until tomorrow's prices are in.
        local = dt_util.as_local(now)
        tomorrow = local.date() + timedelta(days=1)
        model_day = self.model.last_known_day if self.model else None
        return (
            local.hour >= MARKET_PUBLICATION_HOUR
            and model_day is not None
            and model_day < tomorrow
            and now - self._market_at >= RETRY_AFTER
        )

    async def async_update(self, now: datetime | None = None) -> None:
        """Refresh what is outdated, retrain when needed and update estimates."""
        now = now or dt_util.utcnow()
        async with self._lock:
            if self._failed_at is not None and now - self._failed_at < RETRY_AFTER:
                return
            if self.status == "off":
                self.status = "loading"
            try:
                retrain = False
                if self._market_outdated(now):
                    await self._fetch_market(now)
                    retrain = True
                if self._archive_at is None or now - self._archive_at >= HISTORY_REFRESH:
                    await self._fetch_archive(now)
                    retrain = True
                if self._live_at is None or now - self._live_at >= LIVE_REFRESH:
                    await self._fetch_live()
                    self._live_at = now
                if retrain or self.model is None:
                    self.model = PriceModel(
                        dt_util.get_default_time_zone(), self.market, self.archive
                    )
                    self.trained_at = now
                await self._estimate(now)
            except Exception as err:  # any source failure falls back to the threshold
                self._failed_at = now
                self.error = f"{type(err).__name__}: {err}"
                self.status = "ready" if self.estimates else "unavailable"
                _LOGGER.warning("Price forecast update failed: %s", self.error)
                return
            self._failed_at = None
            self.error = None
            self.status = "ready" if self.estimates else "unavailable"

    async def _fetch_market(self, now: datetime) -> None:
        today = dt_util.as_local(now).date()
        data = await self._fetch_json(
            PRICE_URL,
            {
                "bzn": BIDDING_ZONE,
                "start": (today - timedelta(days=TRAIN_DAYS + 10)).isoformat(),
                "end": (today + timedelta(days=1)).isoformat(),
            },
        )
        self.market = parse_market(data)
        self._market_at = now

    async def _fetch_archive(self, now: datetime) -> None:
        today = dt_util.as_local(now).date()
        suffixes = [f"_previous_day{lead}" for lead in range(1, MAX_LEAD_DAYS + 1)]
        names = [f"{v}{s}" for v in WEATHER_VARIABLES for s in suffixes]
        archive: dict[datetime, dict[str, float]] = {}
        for point, (lat, lon) in WEATHER_POINTS.items():
            data = await self._fetch_json(
                ARCHIVE_URL,
                {
                    "latitude": lat,
                    "longitude": lon,
                    "hourly": ",".join(names),
                    "start_date": (today - timedelta(days=TRAIN_DAYS + 10)).isoformat(),
                    "end_date": today.isoformat(),
                    "timezone": "UTC",
                },
            )
            for hour, values in parse_weather(data, point, suffixes).items():
                archive.setdefault(hour, {}).update(values)
        self.archive = archive
        self._archive_at = now

    async def _fetch_live(self) -> None:
        live: dict[datetime, dict[str, float]] = {}
        for point, (lat, lon) in WEATHER_POINTS.items():
            data = await self._fetch_json(
                FORECAST_URL,
                {
                    "latitude": lat,
                    "longitude": lon,
                    "hourly": ",".join(WEATHER_VARIABLES),
                    "forecast_days": MAX_LEAD_DAYS + 1,
                    "timezone": "UTC",
                },
            )
            for hour, values in parse_weather(data, point, [""]).items():
                live.setdefault(hour, {}).update(values)
        self.live = live

    async def _estimate(self, now: datetime) -> None:
        model = self.model
        start = now.replace(minute=0, second=0, microsecond=0)
        hours = [start + i * HOUR for i in range((MAX_LEAD_DAYS + 1) * 24)]
        # Training a (lag, lead) pair takes a moment: keep it off the event loop.
        self.estimates = await self.hass.async_add_executor_job(
            model.predict, now, hours, self.live
        )
        self.estimated_at = now

    def estimate(self, known: list[Slot], deadline: datetime) -> tuple[list[Slot], Calibration]:
        """Estimated all-in price slots from the end of `known` to the deadline.

        The market-to-all-in relation is fitted on the published hours, so it
        includes VAT, taxes, supplier fees and the configured price adjustment.
        """
        if not self.estimates or self.model is None:
            raise ForecastUnavailable(self.error or "Price forecast is not ready")
        hourly: dict[datetime, list[float]] = {}
        for slot in known:
            hour = slot.start.replace(minute=0, second=0, microsecond=0)
            hourly.setdefault(hour, []).append(slot.price)
        pairs = [
            (self.model.market[hour], sum(prices) / len(prices))
            for hour, prices in hourly.items()
            if hour in self.model.market
        ]
        calibration = fit_calibration(pairs)
        if calibration is None:
            raise ForecastUnavailable("Published prices do not match market prices")
        known_end = max((slot.end for slot in known), default=None)
        if known_end is None:
            raise ForecastUnavailable("No published prices")
        hour = known_end.replace(minute=0, second=0, microsecond=0)
        if hour < known_end:
            hour += HOUR
        slots = []
        while hour < deadline:
            market = self.estimates.get(hour)
            if market is not None:
                slots.append(Slot(hour, hour + HOUR, calibration.apply(market), True))
            hour += HOUR
        return slots, calibration
