"""Cost, calendar and input correctness for the real scheduler."""

from datetime import UTC, datetime, timedelta
from itertools import combinations

import pytest

from custom_components.dynamic_car_charger.planner import Slot, make_plan, parse_prices, price_unit

NOW = datetime(2026, 9, 17, 18, tzinfo=UTC)


def slots(prices):
    return [
        Slot(NOW + timedelta(hours=i), NOW + timedelta(hours=i + 1), p)
        for i, p in enumerate(prices)
    ]


def plan(prices, **kwargs):
    params = dict(
        now=NOW,
        deadline=NOW + timedelta(hours=len(prices)),
        soc=20,
        target=80,
        capacity=50,
        power=10,
        efficiency=1,
    )
    params.update(kwargs)
    return make_plan(slots(prices), **params)


def test_cheapest_noncontiguous():
    result = plan([0.8, 0.1, 0.6, 0.2, 0.3])
    assert [s.start.hour for s in result.slots] == [19, 21, 22]
    assert result.cost == pytest.approx(6)
    assert result.shortfall_kwh == 0


def test_fractional_last_slot_and_losses():
    result = plan([0.2, 0.4, 0.6], target=50, efficiency=0.8)
    assert result.required_kwh == pytest.approx(18.75)
    assert result.slots[-1].hours == pytest.approx(0.875)
    assert result.cost == pytest.approx(5.5)


def test_adjacent_partial_hours_are_made_contiguous():
    result = plan([0.1334, 0.1326], target=50)

    assert result.required_kwh == 15
    assert result.slots[0].start == NOW + timedelta(minutes=30)
    assert result.slots[0].end == result.slots[1].start
    assert result.slots[1].end == NOW + timedelta(hours=2)
    assert result.cost == pytest.approx(5 * 0.1334 + 10 * 0.1326)


def test_different_price_adjacent_hours_are_also_made_contiguous():
    result = plan([0.15, 0.13], target=50)

    assert result.slots[0].start == NOW + timedelta(minutes=30)
    assert result.slots[0].end == NOW + timedelta(hours=1)
    assert result.slots[0].end == result.slots[1].start
    assert result.slots[1].end == NOW + timedelta(hours=2)
    assert result.cost == pytest.approx(5 * 0.15 + 10 * 0.13)


def test_partial_consecutive_deadline_slots_are_packed_at_boundary():
    power = 9.9
    deadline = NOW + timedelta(hours=1, minutes=30)
    result = plan(
        [0.156, 0.147],
        deadline=deadline,
        soc=90,
        target=100,
        capacity=57.7,
        power=power,
    )

    assert result.slots[0].end == result.slots[1].start == NOW + timedelta(hours=1)
    assert result.slots[1].end == deadline
    expected_start = NOW + timedelta(hours=1) - timedelta(hours=(5.77 - 4.95) / power)
    assert (result.slots[0].start - expected_start).total_seconds() == pytest.approx(0)
    assert result.planned_kwh == pytest.approx(5.77)
    assert result.cost == pytest.approx(0.82 * 0.156 + 4.95 * 0.147)


def test_negative_prices_do_not_overcharge():
    result = plan([-0.4, -0.2, -0.1], target=40)
    assert result.planned_kwh == 10
    assert result.cost == -4
    assert len(result.slots) == 1


def test_already_at_target():
    assert plan([0.1, 0.2], soc=80).slots == ()


def test_insufficient_time():
    result = plan([0.1, 0.2])
    assert result.shortfall_kwh == 10
    assert result.coverage_complete


def test_clip_now_and_deadline():
    result = plan(
        [0.1, 0.2], now=NOW + timedelta(minutes=30), deadline=NOW + timedelta(hours=1, minutes=30)
    )
    assert result.planned_kwh == 10
    assert result.slots[0].start == NOW + timedelta(minutes=30)
    assert result.slots[-1].end == NOW + timedelta(hours=1, minutes=30)


def test_missing_prices_provisional():
    assert not plan([0.1], deadline=NOW + timedelta(days=2)).coverage_complete


def test_gaps_are_not_filled():
    prices = [slots([0.1])[0], Slot(NOW + timedelta(hours=2), NOW + timedelta(hours=3), 0.2)]
    result = make_plan(prices, NOW, NOW + timedelta(hours=3), 0, 100, 30, 10, 1)
    assert not result.coverage_complete
    assert result.shortfall_kwh == 10


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", -1, 101, True])
def test_bad_soc(value):
    with pytest.raises((ValueError, TypeError)):
        plan([0.1], soc=value)


def test_enever_prices_with_missing_tomorrow():
    result = parse_prices(
        {"prices_today": [{"time": NOW.isoformat(), "price": "0.12"}], "prices_tomorrow": None},
        adjustment=0.01,
    )
    assert result[0].price == pytest.approx(0.13)
    assert result[0].hours == 1


def test_quarter_hours():
    result = parse_prices({"prices": [{"start": NOW.isoformat(), "price": 0.1}]}, 15)
    assert result[0].hours == 0.25


def test_dst_repeated_hour_is_two_distinct_slots():
    result = parse_prices(
        {
            "prices": [
                {"start": "2026-10-25T02:00:00+02:00", "price": 0.1},
                {"start": "2026-10-25T02:00:00+01:00", "price": 0.2},
            ]
        }
    )
    assert result[1].start - result[0].start == timedelta(hours=1)


def test_dst_spring_gap_is_contiguous_utc():
    result = parse_prices(
        {
            "prices": [
                {"start": "2026-03-29T01:00:00+01:00", "price": 0.1},
                {"start": "2026-03-29T03:00:00+02:00", "price": 0.2},
            ]
        }
    )
    assert result[0].end == result[1].start


@pytest.mark.parametrize(
    "rows",
    [
        [{"start": "2026-09-17T10:00:00", "price": 0.1}],
        [{"start": NOW.isoformat(), "price": "nan"}],
        [{"start": NOW.isoformat(), "price": None}],
        [{"start": NOW.isoformat(), "end": NOW.isoformat(), "price": 0.1}],
        [{"start": NOW.isoformat(), "price": 0.1}, {"start": NOW.isoformat(), "price": 0.2}],
        [
            {"start": NOW.isoformat(), "price": 0.1},
            {"start": (NOW + timedelta(minutes=30)).isoformat(), "price": 0.2},
        ],
    ],
)
def test_reject_ambiguous_prices(rows):
    with pytest.raises((ValueError, TypeError)):
        parse_prices({"prices": rows})


def test_optimal_against_all_three_hour_combinations():
    prices = [0.61, -0.1, 0.25, 0.08, 0.19, 0.71, 0.3, 0.13]
    result = plan(prices)
    oracle = min(
        sum(prices[i] * 10 for i in combination)
        for combination in combinations(range(len(prices)), 3)
    )
    assert result.cost == pytest.approx(oracle)


def test_end_exclusive():
    result = plan([0.1], target=30)
    assert result.charging_at(NOW)
    assert not result.charging_at(NOW + timedelta(minutes=30))


def test_expired_deadline_never_charges():
    assert not plan([0.1], deadline=NOW - timedelta(hours=1)).slots


@pytest.mark.parametrize(
    ("unit", "extra", "expected"),
    [
        ("EUR/kWh", {}, (1.0, "EUR")),
        ("€/kWh", {}, (1.0, "EUR")),
        ("SEK/kWh", {}, (1.0, "SEK")),
        ("c/kWh", {}, (0.01, "EUR")),
        ("öre/kWh", {"currency": "NOK"}, (0.01, "NOK")),
        ("kr/kWh", {"currency": "DKK"}, (1.0, "DKK")),
    ],
)
def test_price_unit_accepts_any_currency_per_kwh(unit, extra, expected):
    assert price_unit({"unit_of_measurement": unit, **extra}) == expected


@pytest.mark.parametrize("unit", [None, "EUR", "EUR/MWh", "/kWh", "unknown/kWh"])
def test_price_unit_rejects_other_units(unit):
    with pytest.raises(ValueError):
        price_unit({"unit_of_measurement": unit})


def test_nord_pool_rows_with_values_in_cents():
    hour = timedelta(hours=1)
    result = parse_prices(
        {
            "raw_today": [
                {"start": NOW.isoformat(), "end": (NOW + hour).isoformat(), "value": 12.5},
            ],
            # Tomorrow's values are empty until they are published.
            "raw_tomorrow": [
                {
                    "start": (NOW + hour).isoformat(),
                    "end": (NOW + 2 * hour).isoformat(),
                    "value": None,
                },
            ],
        },
        scale=0.01,
    )
    assert [(s.start, s.price) for s in result] == [(NOW, 0.125)]


def test_price_row_without_any_price_is_rejected():
    with pytest.raises(ValueError):
        parse_prices({"prices": [{"start": NOW.isoformat()}]})
