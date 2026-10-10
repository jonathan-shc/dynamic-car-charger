"""Presence is evidence based; cable tests have one owner and a hard bound."""

from datetime import UTC, datetime, timedelta

from custom_components.dynamic_car_charger.bike import BikeLifecycle, BikeObservation

NOW = datetime(2026, 10, 10, 18, tzinfo=UTC)


def departure(b):
    b.update(NOW, BikeObservation(live=True, speed=0))
    b.update(NOW + timedelta(seconds=1), BikeObservation(live=True, speed=8))
    b.update(NOW + timedelta(seconds=12), BikeObservation(live=True, speed=8))
    assert b.state == "departing"
    b.update(NOW + timedelta(seconds=30), BikeObservation())
    b.update(NOW + timedelta(seconds=80), BikeObservation(rider_home=False))
    assert b.state == "away"


def arrival(b):
    departure(b)
    b.update(NOW + timedelta(seconds=200), BikeObservation(live=True, speed=0))
    assert b.state == "arriving"
    b.update(NOW + timedelta(seconds=231), BikeObservation(live=True, speed=0))
    assert b.state == "home_on"
    return NOW + timedelta(seconds=231)


def test_stationary_wake_does_not_probe():
    b = BikeLifecycle()
    o = BikeObservation(live=True, speed=0)
    b.update(NOW, o)
    assert b.state == "home_on"
    assert b.control(NOW, o, scheduled=False, allow_probe=True, cancel=False) == (False, False)
    b.update(NOW + timedelta(seconds=40), BikeObservation())
    assert b.state == "home_unreachable"
    b.update(NOW + timedelta(seconds=60), BikeObservation(powered=False))
    assert b.state == "home_off"


def test_radio_loss_is_not_departure():
    b = BikeLifecycle()
    b.update(NOW, BikeObservation(live=True, speed=0))
    b.update(NOW + timedelta(minutes=20), BikeObservation(rider_home=False))
    assert b.state == "home_unreachable"
    assert b.cable == "unknown"


def test_probe_power_requires_new_report_and_stops_after_confirmation():
    b = BikeLifecycle()
    t = arrival(b)
    o = BikeObservation(live=True, speed=0)
    assert b.control(t, o, scheduled=False, allow_probe=True, cancel=False) == (True, True)
    b.update(
        t + timedelta(seconds=15),
        BikeObservation(
            live=True, speed=0, plug_on=True, watts=140, power_report=t - timedelta(seconds=1)
        ),
    )
    assert b.cable == "unknown"
    o = BikeObservation(
        live=True, speed=0, plug_on=True, watts=140, power_report=t + timedelta(seconds=15)
    )
    b.update(t + timedelta(seconds=15), o)
    assert b.cable == "connected"
    assert b.control(
        t + timedelta(seconds=15), o, scheduled=False, allow_probe=True, cancel=False
    ) == (False, True)
    o.plug_on = False
    assert b.control(
        t + timedelta(seconds=16), o, scheduled=False, allow_probe=True, cancel=False
    ) == (False, False)
    assert not b.probe_owned
    assert b.control(
        t + timedelta(seconds=30), o, scheduled=False, allow_probe=True, cancel=False
    ) == (False, False)


def test_timeout_is_inconclusive_not_disconnected():
    b = BikeLifecycle()
    t = arrival(b)
    o = BikeObservation(live=True, speed=0)
    b.control(t, o, scheduled=False, allow_probe=True, cancel=False)
    o.plug_on, o.watts, o.power_report = True, 0, t + timedelta(seconds=90)
    b.update(t + timedelta(seconds=90), o)
    assert b.control(
        t + timedelta(seconds=90), o, scheduled=False, allow_probe=True, cancel=False
    ) == (False, True)
    assert b.cable == "unknown"
    assert b.probe_result == "inconclusive"


def test_schedule_takes_over_and_restart_cleans_up_probe():
    b = BikeLifecycle()
    t = arrival(b)
    o = BikeObservation(live=True, speed=0)
    b.control(t, o, scheduled=False, allow_probe=True, cancel=False)
    restarted = BikeLifecycle()
    restarted.restore(b.saved())
    o.plug_on = True
    assert restarted.control(t, o, scheduled=False, allow_probe=True, cancel=False) == (False, True)
    assert restarted.state == "unknown" and restarted.cable == "unknown"
    assert b.control(t, o, scheduled=True, allow_probe=True, cancel=False) == (True, True)
    assert not b.probe_owned


def test_radio_loss_cancels_probe():
    b = BikeLifecycle()
    t = arrival(b)
    o = BikeObservation(live=True, speed=0)
    b.control(t, o, scheduled=False, allow_probe=True, cancel=False)
    o.live, o.plug_on = False, True
    b.update(t + timedelta(seconds=15), o)
    assert b.control(
        t + timedelta(seconds=15), o, scheduled=False, allow_probe=True, cancel=False
    ) == (False, True)


def test_direct_plug_stop_and_manual_cancel_do_not_restart_probe():
    b = BikeLifecycle()
    t = arrival(b)
    o = BikeObservation(live=True, speed=0)
    b.control(t, o, scheduled=False, allow_probe=True, cancel=False)
    o.plug_on = True
    b.control(t + timedelta(seconds=15), o, scheduled=False, allow_probe=True, cancel=False)
    o.plug_on = False
    assert b.control(
        t + timedelta(seconds=30), o, scheduled=False, allow_probe=True, cancel=False
    ) == (False, False)
    assert b.control(
        t + timedelta(seconds=31), o, scheduled=False, allow_probe=True, cancel=False
    ) == (False, False)


def test_fresh_phone_home_prevents_false_departure_after_wheel_test():
    b = BikeLifecycle()
    b.update(NOW, BikeObservation(live=True, speed=5))
    b.update(NOW + timedelta(seconds=12), BikeObservation(live=True, speed=5))
    b.update(NOW + timedelta(seconds=90), BikeObservation(rider_home=True))
    assert b.state == "home_unreachable"
