"""Download Dutch day-ahead prices and archived weather forecasts for the backtest.

Prices: Energy-Charts (Fraunhofer ISE), CC BY 4.0, source Bundesnetzagentur | SMARD.de.
Weather: Open-Meteo previous-runs API: the forecast as it was known 1, 2 and 3 days
before each hour, so the backtest only uses information available at decision time.

No API keys are needed. Output goes to tools/backtest/data/ (not committed).
"""

import argparse
import csv
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

DATA = Path(__file__).parent / "data"

# Points that drive the coupled NL/DE market: offshore and onshore wind, and solar.
WEATHER_POINTS = {
    "nl_offshore": (53.5, 4.5),
    "nl_onshore": (52.9, 5.9),
    "de_north": (54.0, 9.0),
    "de_central": (51.0, 10.0),
}
WEATHER_VARIABLES = ("wind_speed_100m", "shortwave_radiation", "temperature_2m")
LEADS = (1, 2, 3)
# Archived forecasts with a 1-3 day lead are complete from this date.
WEATHER_START = date(2024, 3, 1)


def get_json(url: str) -> dict:
    """GET with backoff on rate limiting."""
    delay = 20
    while True:
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as err:
            if err.code != 429 or delay > 320:
                raise
            print(f"  rate limited, waiting {delay}s")
            time.sleep(delay)
            delay *= 2


def fetch_prices(start: date, end: date) -> None:
    """Hourly NL day-ahead prices in EUR/kWh; quarter hours are averaged per hour."""
    hourly: dict[datetime, list[float]] = defaultdict(list)
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(end, date(chunk_start.year, 12, 31))
        print(f"prices {chunk_start} .. {chunk_end}")
        query = urllib.parse.urlencode(
            {"bzn": "NL", "start": chunk_start.isoformat(), "end": chunk_end.isoformat()}
        )
        data = get_json(f"https://api.energy-charts.info/price?{query}")
        for ts, price in zip(data["unix_seconds"], data["price"], strict=True):
            if price is not None:
                hour = datetime.fromtimestamp(ts, UTC).replace(minute=0)
                hourly[hour].append(price / 1000)
        chunk_start = chunk_end + timedelta(days=1)
        time.sleep(5)
    DATA.mkdir(exist_ok=True)
    with open(DATA / "prices.csv", "w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["utc_hour", "market_eur_kwh", "samples"])
        for hour in sorted(hourly):
            values = hourly[hour]
            writer.writerow([hour.isoformat(), round(sum(values) / len(values), 6), len(values)])
    print(f"wrote {len(hourly)} hours to {DATA / 'prices.csv'}")


def fetch_weather(start: date, end: date) -> None:
    """Archived hourly forecasts per point, variable and lead (days before)."""
    names = [f"{var}_previous_day{lead}" for var in WEATHER_VARIABLES for lead in LEADS]
    rows: dict[str, dict[str, float | None]] = defaultdict(dict)
    for point, (lat, lon) in WEATHER_POINTS.items():
        print(f"weather {point}")
        query = urllib.parse.urlencode(
            {
                "latitude": lat,
                "longitude": lon,
                "hourly": ",".join(names),
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "timezone": "UTC",
            }
        )
        data = get_json(f"https://previous-runs-api.open-meteo.com/v1/forecast?{query}")
        hourly = data["hourly"]
        for index, stamp in enumerate(hourly["time"]):
            for name in names:
                rows[stamp][f"{point}.{name}"] = hourly[name][index]
        time.sleep(2)
    columns = [f"{point}.{name}" for point in WEATHER_POINTS for name in names]
    with open(DATA / "weather.csv", "w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["utc_hour", *columns])
        for stamp in sorted(rows):
            writer.writerow([stamp + ":00+00:00", *(rows[stamp].get(c) for c in columns)])
    print(f"wrote {len(rows)} hours to {DATA / 'weather.csv'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2024, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date.today() + timedelta(days=1))
    args = parser.parse_args()
    DATA.mkdir(exist_ok=True)
    fetch_prices(args.start, args.end)
    fetch_weather(max(args.start, WEATHER_START), args.end)


if __name__ == "__main__":
    main()
