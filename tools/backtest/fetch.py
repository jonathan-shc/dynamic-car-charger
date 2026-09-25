"""Download day-ahead prices and archived weather forecasts for the backtest.

Prices: Energy-Charts (Fraunhofer ISE), CC BY 4.0, source Bundesnetzagentur | SMARD.de.
Weather: Open-Meteo previous-runs API: the forecast as it was known 1 to 6 days
before each hour, so the backtest only uses information available at decision time.

No API keys are needed. Output goes to tools/backtest/data/ (not committed):
prices per bidding zone in data/<zone>/prices.csv, and weather per point in
data/weather/<point>.csv, shared by the zones that use it.

Usage: python tools/backtest/fetch.py [--zone NL --zone BE ...]   (default: all zones)
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

from zone_data import WEATHER_POINTS, ZONES

DATA = Path(__file__).parent / "data"
WEATHER_VARIABLES = ("wind_speed_100m", "shortwave_radiation", "temperature_2m")
LEADS = (1, 2, 3, 4, 5, 6)
# Archived forecasts with a 1-6 day lead are complete from this date.
WEATHER_START = date(2024, 3, 1)


def get_json(url: str) -> dict:
    """GET with backoff on rate limiting and timeouts."""
    delay = 20
    while True:
        try:
            with urllib.request.urlopen(url, timeout=300) as response:
                return json.load(response)
        except urllib.error.HTTPError as err:
            if err.code != 429 or delay > 320:
                raise
            print(f"  rate limited, waiting {delay}s")
        except (TimeoutError, urllib.error.URLError) as err:
            if delay > 320:
                raise
            print(f"  {err}, retrying in {delay}s")
        time.sleep(delay)
        delay *= 2


def fetch_prices(zone: str, start: date, end: date) -> None:
    """Hourly day-ahead prices in EUR/kWh; quarter hours are averaged per hour."""
    hourly: dict[datetime, list[float]] = defaultdict(list)
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(end, date(chunk_start.year, 12, 31))
        print(f"prices {zone} {chunk_start} .. {chunk_end}")
        query = urllib.parse.urlencode(
            {"bzn": zone, "start": chunk_start.isoformat(), "end": chunk_end.isoformat()}
        )
        data = get_json(f"https://api.energy-charts.info/price?{query}")
        for ts, price in zip(data["unix_seconds"], data["price"], strict=True):
            if price is not None:
                hour = datetime.fromtimestamp(ts, UTC).replace(minute=0)
                hourly[hour].append(price / 1000)
        chunk_start = chunk_end + timedelta(days=1)
        time.sleep(5)
    folder = DATA / zone
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "prices.csv", "w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["utc_hour", "market_eur_kwh", "samples"])
        for hour in sorted(hourly):
            values = hourly[hour]
            writer.writerow([hour.isoformat(), round(sum(values) / len(values), 6), len(values)])
    print(f"wrote {len(hourly)} hours to {folder / 'prices.csv'}")


def fetch_weather(point: str, start: date, end: date) -> None:
    """Archived hourly forecasts for one point, per variable and lead (days before)."""
    names = [f"{var}_previous_day{lead}" for var in WEATHER_VARIABLES for lead in LEADS]
    lat, lon = WEATHER_POINTS[point]
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
    folder = DATA / "weather"
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / f"{point}.csv", "w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["utc_hour", *(f"{point}.{name}" for name in names)])
        for index, stamp in enumerate(hourly["time"]):
            writer.writerow([stamp + ":00+00:00", *(hourly[name][index] for name in names)])
    time.sleep(2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2024, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date.today() + timedelta(days=1))
    parser.add_argument("--zone", action="append", choices=list(ZONES))
    parser.add_argument("--skip-existing", action="store_true", help="keep files already there")
    args = parser.parse_args()
    zones = args.zone or list(ZONES)
    for zone in zones:
        if not (args.skip_existing and (DATA / zone / "prices.csv").exists()):
            fetch_prices(zone, args.start, args.end)
    points = dict.fromkeys(point for zone in zones for point in ZONES[zone].points)
    for point in points:
        if not (args.skip_existing and (DATA / "weather" / f"{point}.csv").exists()):
            fetch_weather(point, max(args.start, WEATHER_START), args.end)


if __name__ == "__main__":
    main()
