"""Keeping the car's drives from its device tracker."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant, State

from custom_components.dynamic_car_charger.trips import (
    PAUSE,
    TripRecorder,
    record,
    simplify,
    split,
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
    assert trip["ended"] == (T0 + timedelta(seconds=60)).isoformat()
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


def test_response_is_newest_first(recorder):
    recorder.trips = [{"started": str(i)} for i in range(5)]
    answer = recorder.response(2)
    assert [trip["started"] for trip in answer["trips"]] == ["4", "3"]
    assert answer["total"] == 5


async def test_get_trips_service(recorder):
    hass = recorder.hass
    from custom_components.dynamic_car_charger import async_setup

    recorder.trips = [record([at(0, 0), at(60, 10)])]
    coordinator = SimpleNamespace(trips=recorder)
    await async_setup(hass, {})
    with patch("custom_components.dynamic_car_charger._coordinator_for", return_value=coordinator):
        answer = await hass.services.async_call(
            "dynamic_car_charger", "get_trips", {"limit": 5}, blocking=True, return_response=True
        )
    assert answer["total"] == 1
    assert answer["trips"][0]["distance_km"] == pytest.approx(1.11, abs=0.01)
