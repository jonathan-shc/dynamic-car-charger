"""Pure, UTC-based least-cost planning. No Home Assistant dependencies."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite


def number(value, low=None, high=None):
    """Reject missing, nonfinite, and out-of-range inputs."""
    if isinstance(value, bool):
        raise ValueError("Boolean is not a number")
    result = float(value)
    if (
        not isfinite(result)
        or (low is not None and result < low)
        or (high is not None and result > high)
    ):
        raise ValueError("Number outside allowed range")
    return result


def timestamp(value):
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Timestamps must include a timezone offset")
    return result.astimezone(timezone.utc)


@dataclass(frozen=True)
class Slot:
    start: datetime
    end: datetime
    price: float

    @property
    def hours(self):
        return (self.end - self.start).total_seconds() / 3600


@dataclass(frozen=True)
class Plan:
    slots: tuple[Slot, ...]
    required_kwh: float
    planned_kwh: float
    cost: float
    coverage_complete: bool
    shortfall_kwh: float

    def charging_at(self, now):
        return any(s.start <= now < s.end for s in self.slots)

    def as_dict(self, power):
        return {
            "required_grid_kwh": round(self.required_kwh, 4),
            "planned_grid_kwh": round(self.planned_kwh, 4),
            "estimated_cost_eur": round(self.cost, 4),
            "coverage_complete": self.coverage_complete,
            "shortfall_kwh": round(self.shortfall_kwh, 4),
            "slots": [
                dict(
                    start=s.start.isoformat(),
                    end=s.end.isoformat(),
                    price_eur_kwh=s.price,
                    energy_kwh=round(s.hours * power, 4),
                )
                for s in self.slots
            ],
        }


def parse_prices(attributes, interval_minutes=60, adjustment=0):
    """Read Enever lists or explicit {start,end,price} entries in `prices`.

    Never bridge gaps by extending a price to the next available row.
    """
    number(interval_minutes, 1, 60)
    adjustment = number(adjustment)
    if "prices" in attributes:
        groups = [attributes["prices"]]
    else:
        groups = [attributes.get("prices_today"), attributes.get("prices_tomorrow")]
    unique = {}
    for group in groups:
        if group is None:
            continue
        if not isinstance(group, (tuple, list)):
            raise ValueError("Price attributes must be lists")
        for row in group:
            if not isinstance(row, dict):
                raise ValueError("Invalid price row")
            start = timestamp(row.get("start", row.get("time")))
            end = (
                timestamp(row["end"])
                if "end" in row
                else start + timedelta(minutes=interval_minutes)
            )
            price = number(row["price"]) + adjustment
            if end <= start or end - start > timedelta(hours=1):
                raise ValueError("Price intervals must be positive and at most one hour")
            slot = Slot(start, end, price)
            if start in unique and unique[start] != slot:
                raise ValueError("Conflicting price rows")
            unique[start] = slot
    slots = sorted(unique.values(), key=lambda s: s.start)
    if any(a.end > b.start for a, b in zip(slots, slots[1:])):
        raise ValueError("Overlapping price intervals")
    if not slots:
        raise ValueError("No price intervals available")
    return slots


def make_plan(prices, now, deadline, soc, target, capacity, power, efficiency, max_price=None):
    """Fractional cheapest-first allocation, optimal for fixed power/efficiency.

    Unknown periods are never assigned a made-up price. A partial final slot
    starts at the start of its price interval. Ties favor earlier availability.
    """
    now, deadline = timestamp(now), timestamp(deadline)
    soc, target = number(soc, 0, 100), number(target, 0, 100)
    capacity, power = number(capacity, 0.1, 300), number(power, 0.1, 50)
    efficiency = number(efficiency, 0.1, 1)
    required = max(0, target - soc) / 100 * capacity / efficiency
    clipped = [
        Slot(max(s.start, now), min(s.end, deadline), number(s.price))
        for s in prices
        if s.end > now and s.start < deadline
    ]
    clipped.sort(key=lambda s: s.start)
    cursor = now
    for slot in clipped:
        if slot.start > cursor:
            break
        cursor = max(cursor, slot.end)
    coverage = cursor >= deadline
    remaining, cost, chosen = required, 0.0, []
    for slot in sorted(clipped, key=lambda s: (s.price, s.start)):
        if remaining < 1e-8:
            break
        energy = min(remaining, slot.hours * power)
        chosen.append(Slot(slot.start, slot.start + timedelta(hours=energy / power), slot.price))
        remaining -= energy
        cost += energy * slot.price
    return Plan(
        tuple(sorted(chosen, key=lambda s: s.start)),
        required,
        required - remaining,
        cost,
        coverage,
        max(0, remaining),
    )
