"""Replay charging sessions against real prices to compare planning strategies.

Each session is simulated hour by hour. At every hour a strategy only sees the
prices published at that moment (next-day prices from 15:00 local time) and
decides how much to charge in the current hour, using the integration's own
planner. The oracle knows all prices in advance and gives the lowest possible
cost. Results are reported as extra cost compared with the oracle.

Usage: python tools/backtest/backtest.py [--zone NL] [--start 2024-06-01] [--end 2026-09-22]

With several --zone options (or --all-zones) the zones run in parallel, and
results/zones.md compares them. Results per zone go to results/<zone>/.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import forecast  # noqa: E402
from forecast import (  # noqa: E402
    HOUR,
    History,
    ProfileModel,
    WeatherModel,
    known_until,
)
from zone_data import ZONES, planner  # noqa: E402

Slot, make_plan = planner.Slot, planner.make_plan

RESULTS = Path(__file__).parent / "results"
POWER_KW = 9.9

# (name, plug-in local hour, days until deadline, deadline local hour, minute)
SCENARIOS = (
    ("evening to next morning", 18, 1, 7, 30),
    ("morning to next morning", 9, 1, 7, 30),
    ("evening to second morning", 18, 2, 7, 30),
    ("evening to second late morning", 18, 2, 11, 30),
    ("evening to third morning", 20, 3, 7, 30),
    ("evening to fourth morning", 18, 4, 7, 30),
    ("evening to fifth morning", 18, 5, 7, 30),
)
ENERGIES_KWH = (10.0, 22.5, 40.0)


def plan(prices, now, deadline, energy, max_price=None):
    """The integration's planner, in kWh instead of battery percentages."""
    return make_plan(prices, now, deadline, 0, energy, 100, POWER_KW, 1, max_price=max_price)


def safety_mode(now, deadline, energy):
    """The integration's rule: near the deadline, use any known price."""
    safety_hours = max(1.0, energy / POWER_KW * 1.5)
    return (deadline - now).total_seconds() / 3600 <= safety_hours


class Oracle:
    name = "oracle (all prices known)"

    def __init__(self, history):
        self.history = history

    def decide(self, now, known, deadline, energy):
        hour = now
        prices = []
        while hour < deadline:
            prices.append(Slot(hour, hour + HOUR, self.history.prices[hour]))
            hour += HOUR
        return plan(prices, now, deadline, energy)


class Immediate:
    """No smart charging: charge at full power from plug-in until done."""

    name = "charge immediately"

    def decide(self, now, known, deadline, energy):
        return plan([Slot(now, deadline, 0.0)], now, deadline, energy)


class Threshold:
    """The current integration: a price threshold while prices are incomplete."""

    def __init__(self, threshold):
        self.threshold = threshold
        if threshold is None:
            self.name = "cheapest known, no threshold"
        else:
            self.name = f"threshold {threshold:.2f}"

    def decide(self, now, known, deadline, energy):
        full = plan(known, now, deadline, energy)
        if full.coverage_complete or self.threshold is None or safety_mode(now, deadline, energy):
            return full
        return plan(known, now, deadline, energy, max_price=self.threshold)


class Forecast:
    """Plan over known and forecast prices; only charge in known hours."""

    def __init__(self, model, margin):
        self.model = model
        self.margin = margin
        self.name = f"{model.name} forecast, margin {margin:.2f}"

    def decide(self, now, known, deadline, energy):
        full = plan(known, now, deadline, energy)
        if full.coverage_complete or safety_mode(now, deadline, energy):
            return full
        start = known[-1].end if known else now
        unknown = []
        hour = start
        while hour < deadline:
            unknown.append(hour)
            hour += HOUR
        predicted = self.model.predict(now, unknown)
        # A forecast is less certain than a published price: add a margin so
        # the plan only waits when the forecast is clearly cheaper.
        forecast_slots = [Slot(h, h + HOUR, price + self.margin) for h, price in predicted.items()]
        return plan(known + forecast_slots, now, deadline, energy)


@dataclass
class Result:
    cost: float
    shortfall: float


def simulate(strategy, history, plug, deadline, energy) -> Result:
    remaining, cost, now = energy, 0.0, plug
    while now < deadline and remaining > 1e-6:
        until = min(known_until(now), deadline)
        known = []
        hour = now
        while hour < until:
            known.append(Slot(hour, hour + HOUR, history.prices[hour]))
            hour += HOUR
        chosen = strategy.decide(now, known, deadline, remaining)
        step_end = min(now + HOUR, deadline)
        charged = sum(
            (min(s.end, step_end) - s.start).total_seconds() / 3600 * POWER_KW
            for s in chosen.slots
            if s.start < step_end
        )
        charged = min(charged, remaining)
        cost += charged * history.prices[now]
        remaining -= charged
        now += HOUR
    return Result(cost, max(0.0, remaining))


def sessions(history, start, end):
    day = start
    while day <= end:
        for name, plug_hour, days, deadline_hour, deadline_minute in SCENARIOS:
            plug = datetime(day.year, day.month, day.day, plug_hour, tzinfo=forecast.LOCAL)
            deadline_day = day + timedelta(days=days)
            deadline = datetime(
                deadline_day.year,
                deadline_day.month,
                deadline_day.day,
                deadline_hour,
                deadline_minute,
                tzinfo=forecast.LOCAL,
            )
            if deadline.astimezone(history.last_hour.tzinfo) > history.last_hour:
                continue
            for energy in ENERGIES_KWH:
                yield name, day, plug.astimezone(history.last_hour.tzinfo), deadline, energy
        day += timedelta(days=1)


def forecast_accuracy(history, models, start, end):
    """Mean absolute error per model and lead, at 10:00 and 18:00 decisions."""
    errors = defaultdict(list)
    day = start
    while day <= end:
        for hour in (10, 18):
            now = datetime(day.year, day.month, day.day, hour, tzinfo=forecast.LOCAL)
            until = known_until(now)
            targets = [until + i * HOUR for i in range(48)]
            targets = [t for t in targets if t in history.prices]
            for model in models:
                for target, predicted in model.predict(now, targets).items():
                    lead_hours = (target - until).total_seconds() / 3600
                    bucket = "0-24 h past known prices" if lead_hours < 24 else "24-48 h"
                    errors[(model.name, bucket)].append(abs(predicted - history.prices[target]))
        day += timedelta(days=1)
    return {key: mean(values) for key, values in errors.items()}


def run_zone(zone, start, end_arg, core):
    """Backtest one bidding zone; returns its summary row for results/zones.md."""
    forecast.use_zone(zone)
    history = History()
    end = end_arg or (history.last_hour.astimezone(forecast.LOCAL).date() - timedelta(days=3))
    profile, weather = ProfileModel(history), WeatherModel(history)
    if core:
        strategies = [
            Oracle(history),
            Immediate(),
            Threshold(None),
            Threshold(0.20),
            Forecast(profile, 0.0),
            Forecast(weather, 0.0),
        ]
    else:
        strategies = [
            Oracle(history),
            Immediate(),
            Threshold(None),
            *(Threshold(t) for t in (0.18, 0.20, 0.25, 0.30)),
            *(Forecast(profile, m) for m in (0.0, 0.02)),
            *(Forecast(weather, m) for m in (0.0, 0.02, 0.04)),
        ]

    results_dir = RESULTS / zone
    results_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for count, (scenario, day, plug, deadline, energy) in enumerate(
        sessions(history, start, end), 1
    ):
        if count % 2000 == 0:
            print(f"  {zone}: {count} sessions, at {day}", flush=True)
        results = {s.name: simulate(s, history, plug, deadline, energy) for s in strategies}
        best = results[strategies[0].name].cost
        for name, result in results.items():
            rows.append(
                {
                    "scenario": scenario,
                    "day": day.isoformat(),
                    "energy_kwh": energy,
                    "strategy": name,
                    "cost_eur": round(result.cost, 4),
                    "extra_eur": round(result.cost - best, 4),
                    "shortfall_kwh": round(result.shortfall, 4),
                }
            )
    with open(results_dir / "sessions.csv", "w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    accuracy = forecast_accuracy(history, [profile, weather], start, end)
    write_report(zone, results_dir, rows, accuracy, strategies, start, end)
    return summary(zone, rows, accuracy, strategies, start, end)


def summary(zone, rows, accuracy, strategies, start, end):
    extra = defaultdict(list)
    for row in rows:
        extra[row["strategy"]].append(row)
    oracle = mean(r["cost_eur"] for r in extra[strategies[0].name])

    def pct(name):
        return 100 * mean(r["extra_eur"] for r in extra[name]) / oracle

    return {
        "zone": zone,
        "period": f"{start} to {end}",
        "sessions": len(extra[strategies[0].name]),
        "oracle": oracle,
        "cheapest": pct("cheapest known, no threshold"),
        "threshold": pct("threshold 0.20"),
        "profile": pct("profile forecast, margin 0.00"),
        "weather": pct("weather forecast, margin 0.00"),
        "error": accuracy.get(("weather", "0-24 h past known prices")),
    }


def write_zones(summaries):
    lines = [
        "# Price forecast backtest per bidding zone",
        "",
        "Extra cost compared with knowing all prices in advance (oracle), over all sessions. "
        "Every zone uses the same all-in formula as the Dutch contract, (market + 0.10967) x 1.21, "
        "so only the market prices differ. Next-day prices become known at 15:00 local time.",
        "",
        "| Zone | Period | Sessions | Oracle cost per session (EUR) | Cheapest known | "
        "Threshold 0.20 | Profile forecast | Weather forecast | Weather model error, "
        "first day (EUR/kWh) |",
        "| --- | --- | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for row in summaries:
        lines.append(
            f"| {row['zone']} | {row['period']} | {row['sessions']} | {row['oracle']:.2f} "
            f"| {row['cheapest']:.1f}% | {row['threshold']:.1f}% | {row['profile']:.1f}% "
            f"| {row['weather']:.1f}% | {row['error']:.4f} |"
        )
    report = "\n".join(lines) + "\n"
    (RESULTS / "zones.md").write_text(report)
    print(report)


def summary_from_files(zone):
    """A zone's summary row from its results/<zone>/ files, without running it again."""
    rows = []
    with open(RESULTS / zone / "sessions.csv") as file:
        for row in csv.DictReader(file):
            row["cost_eur"] = float(row["cost_eur"])
            row["extra_eur"] = float(row["extra_eur"])
            rows.append(row)
    accuracy = {}
    for line in (RESULTS / zone / "report.md").read_text().splitlines():
        parts = [part.strip() for part in line.strip("|").split("|")]
        if len(parts) == 3 and parts[0] in ("profile", "weather"):
            accuracy[(parts[0], parts[1])] = float(parts[2])
    days = sorted({row["day"] for row in rows})
    strategies = [Oracle(None)]
    return summary(zone, rows, accuracy, strategies, days[0], days[-1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zone", action="append", choices=list(ZONES))
    parser.add_argument("--all-zones", action="store_true")
    parser.add_argument("--core", action="store_true", help="fewer strategies, runs faster")
    parser.add_argument(
        "--summary", action="store_true", help="only write results/zones.md from earlier runs"
    )
    parser.add_argument("--start", type=date.fromisoformat, default=date(2024, 6, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=None)
    args = parser.parse_args()
    zones = list(ZONES) if args.all_zones else (args.zone or ["NL"])
    if args.summary:
        write_zones([summary_from_files(zone) for zone in zones])
        return
    if len(zones) == 1:
        run_zone(zones[0], args.start, args.end, args.core)
        return
    with ProcessPoolExecutor() as pool:
        jobs = [pool.submit(run_zone, z, args.start, args.end, args.core) for z in zones]
        write_zones([job.result() for job in jobs])


def write_report(zone, results_dir, rows, accuracy, strategies, start, end):
    by_strategy = defaultdict(list)
    by_scenario = defaultdict(list)
    for row in rows:
        by_strategy[row["strategy"]].append(row)
        by_scenario[(row["scenario"], row["strategy"])].append(row)
    sessions_count = len(by_strategy[strategies[0].name])
    oracle_cost = mean(r["cost_eur"] for r in by_strategy[strategies[0].name])

    lines = [
        f"# Price forecast backtest: {zone}",
        "",
        f"Period {start} to {end}, {sessions_count} sessions, charging power {POWER_KW} kW, "
        f"energies {', '.join(str(e) for e in ENERGIES_KWH)} kWh. Prices are {zone} market "
        "prices with the all-in formula of the Dutch NextEnergy contract; next-day prices "
        "become known at 15:00 local time.",
        "",
        f"Average oracle cost per session: EUR {oracle_cost:.2f}.",
        "",
        "## All sessions",
        "",
        "| Strategy | Extra cost per session (EUR) | Extra cost (%) "
        "| Sessions short | Average shortfall (kWh) |",
        "| --- | --: | --: | --: | --: |",
    ]
    for strategy in strategies:
        results = by_strategy[strategy.name]
        extra = mean(r["extra_eur"] for r in results)
        short = [r["shortfall_kwh"] for r in results if r["shortfall_kwh"] > 0.01]
        lines.append(
            f"| {strategy.name} | {extra:.3f} | {100 * extra / oracle_cost:.1f} "
            f"| {len(short)} | {mean(short) if short else 0:.2f} |"
        )
    lines += ["", "## Extra cost per session by scenario (EUR)", ""]
    scenarios = [s[0] for s in SCENARIOS]
    lines.append("| Strategy | " + " | ".join(scenarios) + " |")
    lines.append("| --- |" + " --: |" * len(scenarios))
    for strategy in strategies:
        cells = [
            f"{mean(r['extra_eur'] for r in by_scenario[(scenario, strategy.name)]):.3f}"
            for scenario in scenarios
        ]
        lines.append(f"| {strategy.name} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Forecast accuracy (mean absolute error, EUR/kWh)",
        "",
        "| Model | Horizon | Error |",
        "| --- | --- | --: |",
    ]
    for (model, bucket), error in sorted(accuracy.items()):
        lines.append(f"| {model} | {bucket} | {error:.4f} |")
    report = "\n".join(lines) + "\n"
    (results_dir / "report.md").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
