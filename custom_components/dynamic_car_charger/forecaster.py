"""Fetch price history and weather forecasts, and keep price estimates current.

Sources, no API keys needed:
- Energy-Charts (Fraunhofer ISE): day-ahead market prices of the bidding zone,
  CC BY 4.0, source Bundesnetzagentur | SMARD.de.
- SMARD.de (Bundesnetzagentur, CC BY 4.0): the same prices, when Energy-Charts is down.
- Open-Meteo: live weather forecasts, and archived forecasts to train on.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import numpy as np
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .planner import Slot
from .price_forecast import (
    HOUR,
    MAX_LEAD_DAYS,
    TRAIN_DAYS,
    WEATHER_VARIABLES,
    Calibration,
    PriceModel,
    fit_calibration,
    has_complete_day,
)
from .zones import WEATHER_POINTS, zone

_LOGGER = logging.getLogger(__name__)

PRICE_URL = "https://api.energy-charts.info/price"
# Energy-Charts' own source, asked when it is down: the same day-ahead prices, a file a
# week. The filter per bidding zone; zones without one have no second source.
SMARD_URL = "https://www.smard.de/app/chart_data/{filter}/DE/"
SMARD_FILTERS = {
    "NL": 256,
    "DE-LU": 4169,
    "BE": 4996,
    "FR": 254,
    "AT": 4170,
    "CH": 259,
    "PL": 257,
    "DK1": 252,
    "DK2": 253,
}
WEEK = timedelta(days=7)
ARCHIVE_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

HISTORY_REFRESH = timedelta(hours=24)
# The exchange publishes the next day's prices from about 12:45, usually before 13:00;
# the sources follow some time after. From then both are asked until the prices are in:
# every five minutes at first, as cars often charge in these very hours.
MARKET_PUBLICATION = time(12, 45)
EAGER_UNTIL = time(14, 0)
EAGER_RETRY = timedelta(minutes=5)
# How long each source keeps being asked just to note when it had them (`arrivals`).
ARRIVALS_UNTIL = time(16, 0)
ARRIVALS_KEPT = 30
LIVE_REFRESH = timedelta(hours=1)
RETRY_AFTER = timedelta(minutes=15)
ENERGY_CHARTS, SMARD = "energy-charts", "smard"

FetchJson = Callable[[str, dict[str, Any]], Awaitable[Any]]


def forecast_error_code(error: str | None) -> str | None:
    """Stable public codes; keep detailed source errors out of client messages."""
    if not error:
        return None
    if error == "Published prices do not match market prices":
        return "tariff_mismatch"
    if "matching market prices" in error or "overlap" in error.lower():
        return "calibration_data_incomplete"
    if error == "Price forecast is not ready":
        return "not_ready"
    return "source_unavailable"


class ForecastUnavailable(Exception):
    """Estimates cannot be made right now; plan with the threshold instead."""


class CalibrationDataUnavailable(ForecastUnavailable):
    """Not enough usable evidence to check the supplier tariff against the market."""


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


def shared_forecaster(hass: HomeAssistant, bidding_zone: str | None) -> PriceForecaster:
    """One forecaster per market: schedulers for a car and a scooter learn the same prices."""
    forecasters = hass.data.setdefault(f"{DOMAIN}_forecasters", {})
    code = zone(bidding_zone).code
    if code not in forecasters:
        forecasters[code] = PriceForecaster(hass, bidding_zone=bidding_zone)
    return forecasters[code]


class PriceForecaster:
    """Keeps market history, weather forecasts and a trained model in memory.

    The market history is also kept on disk: when its source is down, also after a
    restart, the forecast carries on from the last prices it had."""

    def __init__(
        self,
        hass: HomeAssistant,
        fetch_json: FetchJson | None = None,
        bidding_zone: str | None = None,
    ) -> None:
        self.hass = hass
        self.zone = zone(bidding_zone)
        self._fetch_json = fetch_json or self._default_fetch
        self._store: Store = Store(hass, 1, f"{DOMAIN}.market.{self.zone.code.lower()}")
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
        self._market_failed_at: datetime | None = None
        # Where the latest market prices in use came from: "energy-charts", "smard", or
        # "kept" (those on disk, when neither answers).
        self.market_source: str | None = None
        # When each source first had the next day's prices, the last days:
        # [{"day": "2026-10-06", "energy-charts": "2026-10-05T13:10+02:00", "smard": ...}].
        self.arrivals: list[dict[str, Any]] = []
        self._asked: dict[str, datetime] = {}
        self._polled_at: datetime | None = None
        self._loaded = False
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

    def _tomorrow(self, now: datetime) -> date:
        return now.astimezone(self.zone.tz).date() + timedelta(days=1)

    def _has_day(self, prices: dict[datetime, float], day: date) -> bool:
        """Whether the prices reach into a day of the bidding zone."""
        return has_complete_day(prices, day, self.zone.tz)

    def _sources_due(self, now: datetime) -> set[str]:
        """Which sources to ask for market prices now."""
        # Both just failed: carry on from the prices kept, and ask again later.
        if self._market_failed_at is not None and now - self._market_failed_at < RETRY_AFTER:
            return set()
        due = set()
        if self._market_at is None or now - self._market_at >= HISTORY_REFRESH:
            due.add(ENERGY_CHARTS)
        local = now.astimezone(self.zone.tz).time()
        if local < MARKET_PUBLICATION:
            return due
        gap = EAGER_RETRY if local < EAGER_UNTIL else RETRY_AFTER
        if self._polled_at is not None and now - self._polled_at < gap:
            return due
        both = {ENERGY_CHARTS, SMARD} if self.zone.code in SMARD_FILTERS else {ENERGY_CHARTS}
        tomorrow = self._tomorrow(now)
        if not self._has_day(self.market, tomorrow):
            # After publication, ask both until tomorrow's prices are in.
            due |= both
        elif local < ARRIVALS_UNTIL:
            # In from one: the other only to note when it has them too.
            seen = self._arrival(tomorrow, create=False) or {}
            due |= {source for source in both if not seen.get(source)}
        return due

    def _arrival(self, day: date, create: bool = True) -> dict[str, Any] | None:
        for entry in self.arrivals:
            if entry["day"] == day.isoformat():
                return entry
        if not create:
            return None
        self.arrivals.append({"day": day.isoformat()})
        del self.arrivals[:-ARRIVALS_KEPT]
        return self.arrivals[-1]

    def _note(self, source: str, prices: dict[datetime, float], now: datetime) -> None:
        """Note when a source first had tomorrow's prices: the time, or "<" and the time
        when it wasn't asked shortly before, so they may have been there a while."""
        asked_before, self._asked[source] = self._asked.get(source), now
        tomorrow = self._tomorrow(now)
        if not self._has_day(prices, tomorrow):
            return
        entry = self._arrival(tomorrow)
        if entry.get(source):
            return
        watched = asked_before is not None and now - asked_before <= RETRY_AFTER + EAGER_RETRY
        stamp = now.astimezone(self.zone.tz).isoformat(timespec="minutes")
        entry[source] = stamp if watched else f"<{stamp}"
        self._store.async_delay_save(self._to_save, 5)

    async def async_update(self, now: datetime | None = None) -> None:
        """Refresh what is outdated, retrain when needed and update estimates."""
        now = now or dt_util.utcnow()
        async with self._lock:
            if self._failed_at is not None and now - self._failed_at < RETRY_AFTER:
                return
            if self.status == "off":
                self.status = "loading"
            market_error = None
            try:
                retrain = False
                if not self._loaded:
                    await self._load_saved()
                if due := self._sources_due(now):
                    before = dict(self.market)
                    market_error = await self._refresh_market(now, due)
                    retrain = before != self.market
                if self._archive_at is None or now - self._archive_at >= HISTORY_REFRESH:
                    await self._fetch_archive(now)
                    retrain = True
                if self._live_at is None or now - self._live_at >= LIVE_REFRESH:
                    await self._fetch_live()
                    self._live_at = now
                if retrain or self.model is None:
                    self.model = PriceModel(self.zone.tz, self.market, self.archive, self.zone)
                    self.trained_at = now
                await self._estimate(now)
            except Exception as err:  # any source failure falls back to the threshold
                self._failed_at = now
                self.error = f"{type(err).__name__}: {err}"
                self.status = "ready" if self.estimates else "unavailable"
                _LOGGER.warning("Price forecast update failed: %s", self.error)
                return
            self._failed_at = None
            # Still shown while the forecast works from the prices kept.
            if market_error is None and self._market_failed_at is not None:
                market_error = self.error
            self.error = market_error
            self.status = "ready" if self.estimates else "unavailable"

    async def _refresh_market(self, now: datetime, sources: set[str]) -> str | None:
        """Market prices from Energy-Charts, else (or as well, when asked) from SMARD, else
        those kept on disk. Returns the failure to show while the last are in use; raises
        without any prices at all."""
        self._polled_at = now
        failure = None
        if ENERGY_CHARTS in sources:
            try:
                fetched = await self._fetch_energy_charts(now)
            except Exception as err:  # noqa: BLE001 - there are two more places to look
                failure = f"{type(err).__name__}: {err}"
            else:
                # Hours only SMARD had so far stay until Energy-Charts has them too.
                later = {h: p for h, p in self.market.items() if h > max(fetched)}
                self.market = {**fetched, **later}
                self.market_source = SMARD if later else ENERGY_CHARTS
                self._note(ENERGY_CHARTS, fetched, now)
        if SMARD in sources or failure is not None:
            try:
                fetched = await self._fetch_smard(now)
            except Exception as err:  # noqa: BLE001 - carry on from the prices there are
                if failure is None:
                    _LOGGER.debug("SMARD didn't answer: %s", err)
                else:
                    failure += f"; SMARD: {type(err).__name__}: {err}"
                    if not self.market:
                        raise ForecastUnavailable(failure) from err
                    self._market_failed_at = now
                    self.market_source = "kept"
                    _LOGGER.warning(
                        "Market prices unavailable (%s); using those up to %s",
                        failure,
                        max(self.market).date(),
                    )
                    return failure
            else:
                if failure is not None or any(hour not in self.market for hour in fetched):
                    self.market_source = SMARD
                if failure is not None:
                    _LOGGER.info("Energy-Charts unavailable (%s); prices from SMARD", failure)
                self.market = {**self.market, **fetched}
                self._note(SMARD, fetched, now)
        oldest = self._oldest(now)
        self.market = {hour: price for hour, price in self.market.items() if hour >= oldest}
        self._market_at = now
        self._market_failed_at = None
        self._store.async_delay_save(self._to_save, 5)
        return None

    def _oldest(self, now: datetime) -> datetime:
        """The start of the history the model trains on."""
        today = now.astimezone(self.zone.tz).date()
        return self.zone_start(today - timedelta(days=TRAIN_DAYS + 10))

    def zone_start(self, day: date) -> datetime:
        """Midnight of a day in the bidding zone, in UTC."""
        return datetime(day.year, day.month, day.day, tzinfo=self.zone.tz).astimezone(UTC)

    async def _fetch_smard(self, now: datetime) -> dict[datetime, float]:
        """The weeks still missing, from SMARD: all of the training year without prices
        yet, otherwise from the week before the last price."""
        if (number := SMARD_FILTERS.get(self.zone.code)) is None:
            raise ForecastUnavailable(f"No second source for {self.zone.code}")
        base = SMARD_URL.format(filter=number)
        oldest = self._oldest(now)
        since = max(oldest, max(self.market) - WEEK) if self.market else oldest
        index = await self._fetch_json(f"{base}index_hour.json", {})
        fetched: dict[datetime, float] = {}
        for stamp in index["timestamps"]:
            if datetime.fromtimestamp(stamp / 1000, UTC) + WEEK <= since:
                continue
            data = await self._fetch_json(f"{base}{number}_DE_hour_{stamp}.json", {})
            for millis, price in data["series"]:
                if price is not None:
                    fetched[datetime.fromtimestamp(millis / 1000, UTC)] = price / 1000
        if not fetched:
            raise ForecastUnavailable("SMARD has no prices")
        return fetched

    async def _fetch_energy_charts(self, now: datetime) -> dict[datetime, float]:
        today = now.astimezone(self.zone.tz).date()
        data = await self._fetch_json(
            PRICE_URL,
            {
                "bzn": self.zone.code,
                "start": (today - timedelta(days=TRAIN_DAYS + 10)).isoformat(),
                "end": (today + timedelta(days=1)).isoformat(),
            },
        )
        fetched = parse_market(data)
        if not fetched:
            raise ForecastUnavailable("Energy-Charts has no prices")
        return fetched

    def _to_save(self) -> dict[str, Any]:
        return {
            "market": {str(int(hour.timestamp())): price for hour, price in self.market.items()},
            "arrivals": self.arrivals,
        }

    async def _load_saved(self) -> None:
        """The market prices of the last fetch that worked and the arrivals, from disk."""
        self._loaded = True
        try:
            saved = await self._store.async_load() or {}
            self.market = {
                datetime.fromtimestamp(int(stamp), UTC): float(price)
                for stamp, price in (saved.get("market") or {}).items()
            }
            self.arrivals = [dict(entry) for entry in saved.get("arrivals") or []]
        except Exception:  # noqa: BLE001 - a broken cache is no worse than none
            self.market, self.arrivals = {}, []

    async def _fetch_archive(self, now: datetime) -> None:
        today = now.astimezone(self.zone.tz).date()
        suffixes = [f"_previous_day{lead}" for lead in range(1, MAX_LEAD_DAYS + 1)]
        names = [f"{v}{s}" for v in WEATHER_VARIABLES for s in suffixes]
        archive: dict[datetime, dict[str, float]] = {}
        for point in self.zone.points:
            lat, lon = WEATHER_POINTS[point]
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
        for point in self.zone.points:
            lat, lon = WEATHER_POINTS[point]
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

    def estimate(
        self,
        known: list[Slot],
        deadline: datetime,
        currency: str = "EUR",
        *,
        calibration: Calibration | None = None,
    ) -> tuple[list[Slot], Calibration]:
        """Estimated all-in price slots from the end of `known` to the deadline.

        The market-to-all-in relation is fitted on the published hours, so it
        includes VAT, taxes, supplier fees, the configured price adjustment and,
        for another currency than the euro, the exchange rate.
        """
        calibration = calibration or self.calibration(known, currency)
        known_end = max((slot.end for slot in known), default=None)
        if known_end is None:
            raise ForecastUnavailable("No published prices")
        hour = known_end.replace(minute=0, second=0, microsecond=0)
        if hour < known_end:
            hour += HOUR
        slots = []
        # The days the market has published are its own prices, as good as known; only
        # the days after are forecast from the weather.
        published = self.model.last_known_day
        while hour < deadline:
            market = self.estimates.get(hour)
            if market is not None:
                forecast = published is None or self.model.local_date(hour) > published
                slots.append(Slot(hour, hour + HOUR, calibration.apply(market), True, forecast))
            hour += HOUR
        return slots, calibration

    def calibration(self, known: list[Slot], currency: str = "EUR") -> Calibration:
        """How market prices turn into the all-in prices of `known`, the published slots."""
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
        if len(pairs) < 12:
            raise CalibrationDataUnavailable(
                f"Insufficient matching market prices ({len(pairs)} hours; at least 12 needed)"
            )
        if np.std([market for market, _ in pairs]) < 1e-4:
            raise CalibrationDataUnavailable("Insufficient variation in matching market prices")
        calibration = fit_calibration(pairs, euro=currency == "EUR")
        if calibration is None:
            raise ForecastUnavailable("Published prices do not match market prices")
        matched_until = max(hour for hour in hourly if hour in self.model.market) + HOUR
        return replace(calibration, matched_until=matched_until)
