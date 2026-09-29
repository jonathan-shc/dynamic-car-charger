"""Keeping the car's drives from its device tracker."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant, State

from custom_components.dynamic_car_charger.trips import (
    PAUSE,
    TripRecorder,
    battery_used,
    record,
    simplify,
    split,
    with_zones,
)

T0 = datetime(2026, 9, 18, 17, 0, tzinfo=UTC)
TRACKER = "device_tracker.car_location"


def at(seconds: float, north: float, east: float = 0.0):
    """A position: 0.001° north is about 111 m."""
    return (T0 + timedelta(seconds=seconds), 52 + north * 0.001, 5 + east * 0.001)


@pytest.fixture
async def recorder(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    trips = TripRecorder(hass, "test", TRACKER)
    trips.store = SimpleNamespace(async_delay_save=lambda *a: None)
    yield trips
    await hass.async_stop(force=True)


def test_split_finds_drives_and_ignores_a_parked_cars_wander():
    points = [
        at(0, 0),
        at(30, 0.0002),  # 22 m: GPS wander while parked
        at(40, 1),
        at(50, 3),
        at(60, 6),
        at(70, 10),
        at(80, 10.0002),
        # A 5-minute stop at traffic lights or a shop, then on: the same drive.
        at(370, 10.0001),
        at(380, 12),
        # Parked for an hour, then a 30 m shuffle: not a drive.
        at(4000, 12.0003),
        at(4010, 12.3),
    ]
    drives = split(points)
    assert len(drives) == 1
    assert drives[0][0] == at(30, 0.0002)
    assert drives[0][-1] == at(380, 12)
    assert at(370, 10.0001) in drives[0]


def test_a_drive_ends_where_the_car_parked_and_keeps_slow_parts():
    points = [
        at(0, 0),
        at(10, 5),
        at(20, 5.2),  # 22 m: crawling in traffic, still part of the route
        at(30, 10),
        at(40, 10.2),  # parking: slower than a step
        at(90, 10.25),  # where it stands, within two minutes
        at(400, 10.25),  # later: not part of the drive
    ]
    (drive,) = split(points)
    assert at(20, 5.2) in drive
    assert drive[-1] == at(90, 10.25)


def test_a_drive_after_a_night_asleep_starts_in_the_morning():
    # Parked at 17:00; the car sends nothing overnight, then drives off at 07:00.
    morning = 14 * 3600
    points = [at(0, 0), at(morning, 1.2), at(morning + 10, 3), at(morning + 20, 6)]
    (drive,) = split(points)
    # Where it stood, but 16 s (133 m at town speed) before the first step, not at 17:00.
    assert drive[0][1:] == points[0][1:]
    assert (points[1][0] - drive[0][0]).total_seconds() == pytest.approx(16, abs=1)
    assert record(drive)["started"] == drive[0][0].isoformat()


def test_a_long_stop_starts_a_new_drive():
    points = [at(0, 0), at(10, 5), at(20, 10), at(20 + 11 * 60, 10), at(20 + 11 * 60 + 10, 15)]
    assert len(split(points)) == 2


def test_simplify_keeps_the_shape_with_fewer_points():
    straight = [at(i * 10, i) for i in range(50)]
    assert simplify(straight) == [straight[0], straight[-1]]
    # A right angle keeps its corner.
    corner = [at(i * 10, i) for i in range(10)] + [at(100 + i * 10, 9, i + 1) for i in range(10)]
    kept = simplify(corner)
    assert at(90, 9) in kept and len(kept) == 3


def test_record_has_times_distance_and_route():
    kept = record([at(0, 0), at(60, 5), at(120, 10)])
    assert kept["started"] == T0.isoformat()
    assert kept["ended"] == (T0 + timedelta(seconds=120)).isoformat()
    assert kept["distance_km"] == pytest.approx(1.11, abs=0.01)
    assert kept["route"] == [[52.0, 5.0, 0], [52.01, 5.0, 120]]


def test_live_positions_become_a_drive_after_ten_minutes_parked(recorder):
    recorder.add(at(0, 0))
    recorder.add(at(20, 0.0001))  # wander: nothing yet
    assert recorder._drive == []
    for i, north in enumerate([1, 3, 6, 10]):
        recorder.add(at(30 + i * 10, north))
    recorder.add(at(120, 10.0001))
    recorder.finish_if_parked(T0 + timedelta(seconds=120))
    assert recorder.trips == []  # parked for a minute: maybe a traffic light
    recorder.finish_if_parked(T0 + timedelta(seconds=60) + PAUSE)
    assert len(recorder.trips) == 1
    trip = recorder.trips[0]
    assert trip["started"] == (T0 + timedelta(seconds=20)).isoformat()
    # It ends where the car parked, a minute after the last step.
    assert trip["ended"] == (T0 + timedelta(seconds=120)).isoformat()
    assert recorder._drive == []


def _state(point) -> State:
    time, lat, lon = point
    state = State(TRACKER, "home", {"latitude": lat, "longitude": lon})
    state.last_updated = time
    return state


def test_import_keeps_finished_drives_once_and_carries_on_one_under_way(recorder):
    finished = [at(0, 0), at(10, 5), at(20, 10)]
    under_way = [at(3600, 10), at(3610, 15), at(3620, 20)]
    states = [_state(point) for point in finished + under_way]
    now = T0 + timedelta(seconds=3620 + 60)
    assert recorder.import_states(states, now) == 1
    assert recorder.trips[0]["ended"] == (T0 + timedelta(seconds=20)).isoformat()
    # The drive under way isn't kept half: the live positions carry it on.
    assert recorder._drive[0] == at(3600, 10)
    recorder.add(at(3700, 25))
    recorder.finish_if_parked(T0 + timedelta(seconds=3700) + PAUSE)
    assert len(recorder.trips) == 2
    assert recorder.trips[1]["ended"] == (T0 + timedelta(seconds=3700)).isoformat()
    # Importing the same history again adds nothing.
    assert recorder.import_states(states, now + PAUSE) == 0


def test_drives_are_named_by_the_zones_as_they_are_now():
    zones = [
        {
            "entity_id": "zone.home",
            "name": "Home",
            "latitude": 52.0,
            "longitude": 5.0,
            "radius": 100,
        },
        {
            "entity_id": "zone.town",
            "name": "Town",
            "latitude": 52.01,
            "longitude": 5.0,
            "radius": 2000,
        },
        {
            "entity_id": "zone.work",
            "name": "Work",
            "latitude": 52.01,
            "longitude": 5.0,
            "radius": 150,
        },
    ]
    kept = record([at(0, 0), at(60, 5), at(120, 10)])
    named = with_zones(kept, zones)
    assert (named["from_zone"], named["from_name"]) == ("zone.home", "Home")
    # In two zones: the smaller one names it better.
    assert (named["to_zone"], named["to_name"]) == ("zone.work", "Work")
    # A zone that has moved away no longer names it.
    moved = [dict(zones[0], latitude=53.0)]
    assert "from_zone" not in with_zones(named, moved)
    assert "to_zone" not in with_zones(record([at(0, 0), at(60, 30)]), zones)


async def test_drives_cut_the_old_way_are_cut_again(recorder):
    old = {"started": "2020-01-01T00:00:00+00:00", "ended": "2020-01-01T01:00:00+00:00"}
    recent = {"started": T0.isoformat(), "ended": (T0 + timedelta(hours=1)).isoformat()}

    async def load():
        return {"trips": [old, recent]}

    recorder.store = SimpleNamespace(async_load=load, async_delay_save=lambda *a: None)
    with patch(
        "custom_components.dynamic_car_charger.trips.dt_util.utcnow",
        return_value=T0 + timedelta(days=1),
    ):
        await recorder.async_start()
    # The recorder still has the recent one: it's cut again from there; the old one stays.
    assert recorder.trips == [old]
    await recorder.async_stop()


def test_response_is_newest_first(recorder):
    recorder.trips = [{"started": str(i)} for i in range(5)]
    answer = recorder.response(2)
    assert [trip["started"] for trip in answer["trips"]] == ["4", "3"]
    assert answer["total"] == 5


def soc(seconds: float, percent: float):
    return (T0 + timedelta(seconds=seconds), percent)


def test_battery_used_from_the_start_to_where_it_settles():
    readings = [soc(-3600, 80.0), soc(30, 79.8), soc(300, 78.1), soc(600, 77.0)]
    # After the drive it wavers: the middle reading of the minutes after counts.
    readings += [soc(610, 76.6), soc(640, 76.9), soc(700, 76.9)]
    assert battery_used(T0, T0 + timedelta(seconds=600), readings) == 3.1
    # A wobble up on a short drive is none used, not less than none.
    assert battery_used(T0, T0 + timedelta(seconds=60), [soc(-10, 80.0), soc(70, 80.2)]) == 0.0
    # Charged on the way, or no reading before it: not known.
    assert battery_used(T0, T0 + timedelta(seconds=60), [soc(-10, 80.0), soc(70, 85.0)]) is None
    assert battery_used(T0, T0 + timedelta(seconds=60), [soc(70, 80.0)]) is None


def test_a_drive_kept_live_has_its_battery_use(recorder):
    recorder.add_reading(soc(-7200, 90.0))
    for i, north in enumerate([0, 1, 3, 6, 10]):
        recorder.add(at(i * 10, north))
        recorder.add_reading(soc(i * 10 + 5, 90.0 - i * 0.2))
    recorder.add_reading(soc(200, 89.0))
    recorder.add_reading(soc(250, 89.0))
    recorder.finish_if_parked(T0 + timedelta(seconds=40) + PAUSE)
    # 90 at the start; after it 89.2, 89.0 and 89.0.
    assert recorder.trips[0]["battery_used"] == 1.0
    # Old readings go, but the one the next drive starts from stays.
    recorder.add_reading(soc(8 * 3600, 89.0))
    assert recorder._readings[0] == soc(250, 89.0)


def test_battery_use_is_filled_in_for_drives_the_recorder_still_has(recorder):
    recorder.trips = [record([at(0, 0), at(60, 10)])]
    assert recorder.fill_battery_used([soc(-60, 70.0), soc(90, 69.5)]) == 1
    assert recorder.trips[0]["battery_used"] == 0.5
    # From before the readings: left as it is.
    recorder.trips.append(record([at(-9000, 0), at(-8940, 10)]))
    assert recorder.fill_battery_used([soc(-60, 70.0), soc(90, 69.5)]) == 0


def test_response_has_energy_and_cost_where_the_use_is_known(recorder):
    recorder.trips = [record([at(0, 0), at(60, 10)]), record([at(900, 10), at(960, 20)])]
    recorder.trips[0]["battery_used"] = 2.0
    answer = recorder.response(5, capacity_kwh=60, efficiency=0.9, price=0.27, currency="EUR")
    newest, oldest = answer["trips"]
    assert "energy_kwh" not in newest
    assert oldest["energy_kwh"] == 1.2
    # Charging it back: 1.2 kWh / 0.9 at 0.27 a kWh.
    assert (oldest["cost"], oldest["currency"]) == (0.36, "EUR")


async def test_get_trips_service(recorder):
    hass = recorder.hass
    from custom_components.dynamic_car_charger import async_setup

    recorder.trips = [record([at(0, 0), at(60, 10)])]
    coordinator = SimpleNamespace(trips_response=recorder.response)
    await async_setup(hass, {})
    with patch("custom_components.dynamic_car_charger._coordinator_for", return_value=coordinator):
        answer = await hass.services.async_call(
            "dynamic_car_charger", "get_trips", {"limit": 5}, blocking=True, return_response=True
        )
    assert answer["total"] == 1
    assert answer["trips"][0]["distance_km"] == pytest.approx(1.11, abs=0.01)
