"""Price forecast model, calibration and data handling."""

import math
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.dynamic_car_charger.forecaster import (
    ForecastUnavailable,
    PriceForecaster,
    parse_market,
    parse_weather,
)
from custom_components.dynamic_car_charger.planner import Slot
from custom_components.dynamic_car_charger.price_forecast import (
    MAX_LEAD_DAYS,
    WEATHER_POINTS,
    PriceModel,
    dutch_holidays,
    easter,
    fit_calibration,
    is_day_off,
)

AMS = ZoneInfo("Europe/Amsterdam")
HOUR = timedelta(hours=1)
START = datetime(2026, 1, 1, tzinfo=UTC)
# 70 days of history; the last full day is 11 March (local time).
END = datetime(2026, 3, 11, 23, tzinfo=UTC)


def wind(hour: datetime) -> float:
    """Synthetic wind speed in km/h, varying over days."""
    return 25 + 20 * math.sin(hour.timestamp() / 3600 / 29)


def market_price(hour: datetime) -> float:
    """Synthetic market price: evening peak, cheaper with more wind."""
    local = hour.astimezone(AMS)
    peak = 0.04 if 17 <= local.hour <= 20 else 0.0
    return 0.12 + peak - 0.002 * wind(hour)


def weather_row(hour: datetime, suffixes) -> dict[str, float]:
    row = {}
    for point in WEATHER_POINTS:
        for suffix in suffixes:
            row[f"{point}.wind_speed_100m{suffix}"] = wind(hour)
            row[f"{point}.shortwave_radiation{suffix}"] = 0.0
            row[f"{point}.temperature_2m{suffix}"] = 8.0
    return row


def hours(start, end):
    hour = start
    while hour <= end:
        yield hour
        hour += HOUR


ARCHIVE_SUFFIXES = [f"_previous_day{lead}" for lead in range(1, MAX_LEAD_DAYS + 1)]


@pytest.fixture
def model():
    market = {h: market_price(h) for h in hours(START, END)}
    archive = {h: weather_row(h, ARCHIVE_SUFFIXES) for h in hours(START, END)}
    return PriceModel(AMS, market, archive)


def test_easter_and_dutch_holidays():
    assert easter(2026) == date(2026, 4, 5)
    assert easter(2027) == date(2027, 3, 28)
    holidays = dutch_holidays(2025)
    # King's Day moves to Saturday when 27 April is a Sunday.
    assert date(2025, 4, 26) in holidays
    assert date(2025, 5, 29) in holidays  # Ascension Day
    assert date(2025, 6, 9) in holidays  # Whit Monday
    assert is_day_off(date(2026, 4, 6))  # Easter Monday
    assert not is_day_off(date(2026, 4, 7))


def test_calibration_recovers_supplier_formula():
    pairs = [(m / 100, 1.21 * m / 100 + 0.1327) for m in range(-5, 40)]
    calibration = fit_calibration(pairs)
    assert calibration.slope == pytest.approx(1.21)
    assert calibration.offset == pytest.approx(0.1327)
    assert calibration.apply(0.1) == pytest.approx(0.2537)


def test_calibration_rejects_too_few_or_unrelated_prices():
    assert fit_calibration([(0.1, 0.25)] * 5) is None
    unrelated = [(m / 100, (m * 37 % 11) / 10) for m in range(40)]
    assert fit_calibration(unrelated) is None


def test_model_estimates_unpublished_prices_from_weather(model):
    assert model.last_known_day == date(2026, 3, 11)
    now = datetime(2026, 3, 11, 9, tzinfo=UTC)
    future = list(
        hours(datetime(2026, 3, 11, 23, tzinfo=UTC), datetime(2026, 3, 13, 22, tzinfo=UTC))
    )
    live = {h: weather_row(h, [""]) for h in future}
    estimates = model.predict(now, future, live)
    assert len(estimates) == len(future)
    errors = [abs(estimates[h] - market_price(h)) for h in future]
    assert max(errors) < 0.01


def test_model_returns_published_prices_and_skips_far_hours(model):
    now = datetime(2026, 3, 11, 9, tzinfo=UTC)
    published = datetime(2026, 3, 11, 8, tzinfo=UTC)
    too_far = now + timedelta(days=MAX_LEAD_DAYS, hours=2)
    live = {too_far: weather_row(too_far, [""])}
    estimates = model.predict(now, [published, too_far], live)
    assert estimates == {published: market_price(published)}


def test_model_needs_weather_for_estimates(model):
    now = datetime(2026, 3, 11, 9, tzinfo=UTC)
    future = datetime(2026, 3, 12, 12, tzinfo=UTC)
    assert model.predict(now, [future], {}) == {}


def test_model_only_trains_on_published_days():
    market = {h: market_price(h) for h in hours(START, END)}
    archive = {h: weather_row(h, ARCHIVE_SUFFIXES) for h in hours(START, END)}
    # An incomplete next day must not count as published or be trained on.
    market[END + HOUR] = 5.0
    model = PriceModel(AMS, market, archive)
    assert model.last_known_day == date(2026, 3, 11)
    mean, scale, beta, y_mean = model._fit(1, 1)
    assert y_mean < 0.2


def test_parse_market_averages_quarter_hours():
    base = int(datetime(2026, 9, 24, 10, tzinfo=UTC).timestamp())
    data = {
        "unix_seconds": [base, base + 900, base + 1800, base + 2700, base + 3600],
        "price": [100.0, 120.0, 80.0, 100.0, None],
    }
    assert parse_market(data) == {datetime(2026, 9, 24, 10, tzinfo=UTC): 0.1}


def test_parse_weather_keys_by_point_and_lead():
    data = {
        "hourly": {
            "time": ["2026-09-24T10:00"],
            "wind_speed_100m_previous_day1": [30.0],
            "shortwave_radiation_previous_day1": [None],
            "temperature_2m_previous_day1": [12.0],
        }
    }
    parsed = parse_weather(data, "nl_offshore", ["_previous_day1"])
    assert parsed == {
        datetime(2026, 9, 24, 10, tzinfo=UTC): {
            "nl_offshore.wind_speed_100m_previous_day1": 30.0,
            "nl_offshore.temperature_2m_previous_day1": 12.0,
        }
    }


def api_response(url, params):
    """Fake Energy-Charts and Open-Meteo responses from the synthetic data."""
    if "energy-charts" in url:
        stamps = [int(h.timestamp()) for h in hours(START, END)]
        prices = [market_price(h) * 1000 for h in hours(START, END)]
        return {"unix_seconds": stamps, "price": prices}
    point = next(p for p, (lat, _) in WEATHER_POINTS.items() if lat == params["latitude"])
    names = params["hourly"].split(",")
    if "previous-runs" in url:
        span = list(hours(START, END))
    else:
        span = list(
            hours(datetime(2026, 3, 11, 0, tzinfo=UTC), datetime(2026, 3, 18, 0, tzinfo=UTC))
        )
    hourly = {"time": [h.strftime("%Y-%m-%dT%H:%M") for h in span]}
    for name in names:
        variable = name.split("_previous_day")[0]
        value = {"wind_speed_100m": wind, "shortwave_radiation": lambda h: 0.0}.get(
            variable, lambda h: 8.0
        )
        hourly[name] = [value(h) for h in span]
    assert point in WEATHER_POINTS
    return {"hourly": hourly}


@pytest.fixture
async def hass(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    yield hass
    await hass.async_stop(force=True)


async def test_forecaster_estimates_all_in_slots(hass):
    calls = []

    async def fetch(url, params):
        calls.append(url)
        return api_response(url, params)

    forecaster = PriceForecaster(hass, fetch)
    now = datetime(2026, 3, 11, 9, tzinfo=UTC)
    await forecaster.async_update(now)
    assert forecaster.status == "ready"
    assert forecaster.error is None
    # Market history, 4 archive points and 4 live forecast points.
    assert len(calls) == 9

    known = [
        Slot(h, h + HOUR, 1.21 * market_price(h) + 0.1327)
        for h in hours(datetime(2026, 3, 10, 23, tzinfo=UTC), datetime(2026, 3, 11, 22, tzinfo=UTC))
    ]
    deadline = datetime(2026, 3, 13, 6, tzinfo=UTC)
    slots, calibration = forecaster.estimate(known, deadline)
    assert calibration.slope == pytest.approx(1.21)
    assert all(slot.estimated for slot in slots)
    assert slots[0].start == datetime(2026, 3, 11, 23, tzinfo=UTC)
    assert slots[-1].end == deadline
    for slot in slots:
        expected = 1.21 * market_price(slot.start) + 0.1327
        assert slot.price == pytest.approx(expected, abs=0.015)

    # Nothing is fetched again within the refresh intervals.
    await forecaster.async_update(now + timedelta(minutes=15))
    assert len(calls) == 9


async def test_forecaster_failure_is_reported_and_retried_later(hass):
    async def broken(url, params):
        raise TimeoutError("no connection")

    forecaster = PriceForecaster(hass, broken)
    now = dt_util.utcnow()
    await forecaster.async_update(now)
    assert forecaster.status == "unavailable"
    assert "no connection" in forecaster.error
    with pytest.raises(ForecastUnavailable):
        forecaster.estimate([], now + timedelta(days=1))


async def test_forecaster_rejects_prices_that_do_not_match_the_market(hass):
    async def fetch(url, params):
        return api_response(url, params)

    forecaster = PriceForecaster(hass, fetch)
    await forecaster.async_update(datetime(2026, 3, 11, 9, tzinfo=UTC))
    unrelated = [
        Slot(h, h + HOUR, 0.3 + (h.hour % 3) / 10)
        for h in hours(datetime(2026, 3, 10, 23, tzinfo=UTC), datetime(2026, 3, 11, 22, tzinfo=UTC))
    ]
    with pytest.raises(ForecastUnavailable, match="do not match"):
        forecaster.estimate(unrelated, datetime(2026, 3, 13, 6, tzinfo=UTC))
