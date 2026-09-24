"""Replay charging sessions against real prices to compare planning strategies.

Each session is simulated hour by hour. At every hour a strategy only sees the
prices published at that moment (next-day prices from 15:00 local time) and
decides how much to charge in the current hour, using the integration's own
planner. The oracle knows all prices in advance and gives the lowest possible
cost. Results are reported as extra cost compared with the oracle.

Usage: python tools/backtest/backtest.py [--start 2024-06-01] [--end 2026-09-22]
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from forecast import (  # noqa: E402
    HOUR,
    LOCAL,
    History,
    ProfileModel,
    WeatherModel,
    known_until,
)

from custom_components.dynamic_car_charger.planner import Slot, make_plan  # noqa: E402

RESULTS = Path(__file__).parent / "results"
POWER_KW = 9.9

# (name, plug-in local hour, days until deadline, deadline local hour, minute)
SCENARIOS = (
    ("evening to next morning", 18, 1, 7, 30),
    ("morning to next morning", 9, 1, 7, 30),
    ("evening to second morning", 18, 2, 7, 30),
    ("evening to second late morning", 18, 2, 11, 30),
    ("evening to third morning", 20, 3, 7, 30),
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
            plug = datetime(day.year, day.month, day.day, plug_hour, tzinfo=LOCAL)
            deadline_day = day + timedelta(days=days)
            deadline = datetime(
                deadline_day.year,
                deadline_day.month,
                deadline_day.day,
                deadline_hour,
                deadline_minute,
                tzinfo=LOCAL,
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
            now = datetime(day.year, day.month, day.day, hour, tzinfo=LOCAL)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2024, 6, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=None)
    args = parser.parse_args()

    history = History()
    end = args.end or (history.last_hour.astimezone(LOCAL).date() - timedelta(days=3))
    profile, weather = ProfileModel(history), WeatherModel(history)
    strategies = [
        Oracle(history),
        Threshold(None),
        *(Threshold(t) for t in (0.18, 0.20, 0.25, 0.30)),
        *(Forecast(profile, m) for m in (0.0, 0.02, 0.04)),
        *(Forecast(weather, m) for m in (0.0, 0.02, 0.03, 0.04, 0.06)),
    ]

    RESULTS.mkdir(exist_ok=True)
    rows = []
    for count, (scenario, day, plug, deadline, energy) in enumerate(
        sessions(history, args.start, end), 1
    ):
        if count % 500 == 0:
            print(f"  {count} sessions, at {day}", flush=True)
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
    with open(RESULTS / "sessions.csv", "w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    accuracy = forecast_accuracy(history, [profile, weather], args.start, end)
    write_report(rows, accuracy, strategies, args.start, end)


def write_report(rows, accuracy, strategies, start, end):
    by_strategy = defaultdict(list)
    by_scenario = defaultdict(list)
    for row in rows:
        by_strategy[row["strategy"]].append(row)
        by_scenario[(row["scenario"], row["strategy"])].append(row)
    sessions_count = len(by_strategy[strategies[0].name])
    oracle_cost = mean(r["cost_eur"] for r in by_strategy[strategies[0].name])

    lines = [
        "# Price forecast backtest",
        "",
        f"Period {start} to {end}, {sessions_count} sessions, charging power {POWER_KW} kW, "
        f"energies {', '.join(str(e) for e in ENERGIES_KWH)} kWh. Prices are all-in NextEnergy "
        "prices; next-day prices become known at 15:00.",
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
    (RESULTS / "report.md").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
