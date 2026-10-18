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
    for second in range(100, 136):
        b.update(NOW + timedelta(seconds=second), BikeObservation(live=True, speed=15))
    b.update(
        NOW + timedelta(seconds=200),
        BikeObservation(live=True, speed=0, trip_km=0.64, trip_report=NOW + timedelta(seconds=200)),
    )
    assert b.state == "arriving"
    b.update(
        NOW + timedelta(seconds=231),
        BikeObservation(live=True, speed=0, trip_km=0.64, trip_report=NOW + timedelta(seconds=231)),
    )
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
    o.plug_on, o.watts, o.power_report = True, 0, t + timedelta(seconds=300)
    b.update(t + timedelta(seconds=300), o)
    assert b.control(
        t + timedelta(seconds=300), o, scheduled=False, allow_probe=True, cancel=False
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


def test_contrary_phone_home_cancels_departure_intent_permanently():
    b = BikeLifecycle()
    b.update(NOW, BikeObservation(live=True, speed=5))
    b.update(NOW + timedelta(seconds=12), BikeObservation(live=True, speed=5))
    b.update(NOW + timedelta(seconds=90), BikeObservation(rider_home=True))
    b.update(NOW + timedelta(minutes=10), BikeObservation())
    assert b.state == "home_unreachable"
    assert not b.away


def test_configured_but_stale_phone_does_not_confirm_departure():
    b = BikeLifecycle()
    b.update(NOW, BikeObservation(live=True, speed=5))
    b.update(NOW + timedelta(seconds=12), BikeObservation(live=True, speed=5))
    b.update(NOW + timedelta(seconds=90), BikeObservation(rider_configured=True))
    assert b.state == "home_unreachable"
    b.update(NOW + timedelta(seconds=120), BikeObservation(rider_configured=True, rider_home=False))
    assert b.state == "away"


def test_short_bluetooth_motion_is_enough_with_fresh_rider_away():
    b = BikeLifecycle()
    b.update(NOW, BikeObservation(live=True, speed=0))
    b.update(
        NOW + timedelta(seconds=1), BikeObservation(live=True, speed=15, rider_configured=True)
    )
    b.update(NOW + timedelta(seconds=5), BikeObservation(rider_configured=True))
    b.update(NOW + timedelta(seconds=65), BikeObservation(rider_configured=True, rider_home=False))
    assert b.state == "away"


def test_short_motion_alone_cannot_confirm_departure():
    b = BikeLifecycle()
    b.update(NOW, BikeObservation(live=True, speed=0))
    b.update(NOW + timedelta(seconds=1), BikeObservation(live=True, speed=15))
    b.update(NOW + timedelta(seconds=65), BikeObservation())
    assert b.state == "home_unreachable"


def inbound(b, trip, start=0, seconds=10):
    for second in range(start, start + seconds + 1):
        now = NOW + timedelta(seconds=second)
        b.update(now, BikeObservation(live=True, speed=12, trip_km=trip, trip_report=now))


def park(b, second, trip=0.64):
    for tick in range(second, second + 31):
        now = NOW + timedelta(seconds=tick)
        o = BikeObservation(live=True, speed=0, trip_km=trip, trip_report=now)
        b.update(now, o)
    return b.control(now, o, scheduled=False, allow_probe=True, cancel=False)


def test_inbound_ten_seconds_and_fresh_trip_triggers_probe():
    b = BikeLifecycle()
    inbound(b, 0.64)
    assert park(b, 11) == (True, True)
    assert b.reason == "fresh_trip_arrival"


def test_zero_and_short_trip_do_not_probe():
    for trip in [0, 0.01, 0.09]:
        b = BikeLifecycle()
        inbound(b, trip)
        assert park(b, 11, trip) == (False, False)
        assert b.arrived_at is None


def test_old_trip_packet_and_missing_timestamp_do_not_qualify():
    for reported in [None, NOW - timedelta(minutes=1)]:
        b = BikeLifecycle()
        for second in range(45):
            b.update(
                NOW + timedelta(seconds=second),
                BikeObservation(live=True, speed=0, trip_km=0.64, trip_report=reported),
            )
        assert b.arrived_at is None
        assert not b.ride_qualified


def test_same_trip_does_not_probe_again_after_wake_or_restart():
    b = BikeLifecycle()
    assert park(b, 0) == (True, True)
    off = BikeObservation(powered=False)
    b.update(NOW + timedelta(seconds=50), off)
    b.control(NOW + timedelta(seconds=50), off, scheduled=False, allow_probe=True, cancel=False)
    assert park(b, 60) == (False, False)
    restarted = BikeLifecycle()
    restarted.restore(b.saved())
    assert park(restarted, 100) == (False, False)


def test_trip_reset_then_another_ride_allows_new_probe():
    b = BikeLifecycle()
    park(b, 0)
    b.update(
        NOW + timedelta(seconds=40),
        BikeObservation(live=True, speed=0, trip_km=0, trip_report=NOW + timedelta(seconds=40)),
    )
    inbound(b, 0.2, start=50)
    assert park(b, 61, 0.2) == (True, True)


def test_phone_away_does_not_block_fresh_bike_trip_arrival():
    b = BikeLifecycle()
    for second in range(31):
        now = NOW + timedelta(seconds=second)
        b.update(
            now,
            BikeObservation(
                live=True,
                speed=0,
                trip_km=0.64,
                trip_report=now,
                rider_home=False,
                rider_configured=True,
            ),
        )
    assert b.arrived_at is not None


def test_configured_minimum_trip_distance():
    b = BikeLifecycle(1000)
    inbound(b, 0.64)
    assert park(b, 11) == (False, False)


def test_cable_confirmation_survives_restart_and_stationary_wake():
    b = BikeLifecycle()
    b.update(NOW, BikeObservation(live=True, speed=0, charging=True))
    restarted = BikeLifecycle()
    restarted.restore(b.saved())
    assert restarted.cable == "connected"
    assert restarted.cable_at == NOW
    restarted.update(NOW + timedelta(minutes=3), BikeObservation())
    assert restarted.cable == "connected"
    restarted.update(NOW + timedelta(minutes=4), BikeObservation(live=True, speed=0))
    assert restarted.cable == "connected" and restarted.cable_at == NOW
    restarted.update(NOW + timedelta(minutes=5), BikeObservation(live=True, speed=3))
    assert restarted.cable == "unknown"
    again = BikeLifecycle()
    again.restore(restarted.saved())
    assert again.cable == "unknown"


def test_invalid_or_away_saved_confirmation_is_not_restored():
    for extra in [
        {"cable_confirmed_at": "bad"},
        {"cable_confirmed_at": "2026-10-10"},
        {"away": True},
        {"cable_evidence": "bike_moving"},
    ]:
        b = BikeLifecycle()
        b.restore(
            {
                "home": True,
                "cable": "connected",
                "cable_confirmed_at": NOW.isoformat(),
                "cable_evidence": "ble_charging",
                **extra,
            }
        )
        assert b.cable == "unknown"


def test_probe_waits_five_minutes_but_stops_immediately_on_cable():
    b = BikeLifecycle()
    t = arrival(b)
    o = BikeObservation(live=True, speed=0, plug_on=False)
    b.control(t, o, scheduled=False, allow_probe=True, cancel=False)
    o.plug_on = True
    assert b.control(
        t + timedelta(seconds=299), o, scheduled=False, allow_probe=True, cancel=False
    ) == (True, True)
    o.charging = True
    b.update(t + timedelta(seconds=299), o)
    assert b.control(
        t + timedelta(seconds=299), o, scheduled=False, allow_probe=True, cancel=False
    ) == (False, True)
    assert b.control(
        t + timedelta(seconds=299), o, scheduled=True, allow_probe=True, cancel=False
    ) == (True, True)


def test_short_movement_after_checked_trip_cannot_reuse_old_distance():
    b = BikeLifecycle()
    park(b, 0)
    off = BikeObservation(powered=False)
    b.update(NOW + timedelta(seconds=40), off)
    b.control(NOW + timedelta(seconds=40), off, scheduled=False, allow_probe=True, cancel=False)
    inbound(b, 0.65, start=50)
    assert park(b, 61, 0.65) == (False, False)


def test_new_hundred_metres_after_checked_trip_counts_exact_threshold():
    b = BikeLifecycle()
    park(b, 0, 0.64)
    off = BikeObservation(powered=False)
    b.update(NOW + timedelta(seconds=40), off)
    b.control(NOW + timedelta(seconds=40), off, scheduled=False, allow_probe=True, cancel=False)
    inbound(b, 0.74, start=50)
    assert park(b, 61, 0.74) == (True, True)


def test_unknown_speed_breaks_stationary_arrival_timer():
    b = BikeLifecycle()
    b.update(NOW, BikeObservation(live=True, speed=0, trip_km=0.64, trip_report=NOW))
    b.update(
        NOW + timedelta(seconds=20),
        BikeObservation(
            live=True, speed=None, trip_km=0.64, trip_report=NOW + timedelta(seconds=20)
        ),
    )
    b.update(
        NOW + timedelta(seconds=35),
        BikeObservation(live=True, speed=0, trip_km=0.64, trip_report=NOW + timedelta(seconds=35)),
    )
    assert b.arrived_at is None
    b.update(
        NOW + timedelta(seconds=65),
        BikeObservation(live=True, speed=0, trip_km=0.64, trip_report=NOW + timedelta(seconds=65)),
    )
    assert b.arrived_at is not None


def test_reconnect_waits_for_actual_trip_before_comparing_reset():
    b = BikeLifecycle()
    park(b, 0, 0.64)
    off = BikeObservation(powered=False)
    b.update(NOW + timedelta(seconds=40), off)
    b.control(NOW + timedelta(seconds=40), off, scheduled=False, allow_probe=True, cancel=False)
    b.update(NOW + timedelta(seconds=50), BikeObservation(live=True, speed=0))
    assert park(b, 51, 0.2) == (True, True)


def lost_after_a_short_ride(b):
    """As on 15 October: about nine seconds of riding, then out of Bluetooth range."""
    b.update(NOW, BikeObservation(live=True, speed=0, live_report=NOW))
    for second in range(17, 26):
        t = NOW + timedelta(seconds=second)
        b.update(t, BikeObservation(live=True, speed=15, live_report=t))
    b.update(NOW + timedelta(seconds=35), BikeObservation(rider_configured=True))


def test_the_bikes_own_location_confirms_a_departure_bluetooth_lost():
    b = BikeLifecycle()
    lost_after_a_short_ride(b)
    assert not b.wants_location(NOW + timedelta(seconds=40), charge_planned=False)
    assert b.wants_location(NOW + timedelta(seconds=90), charge_planned=False)
    fixed = NOW + timedelta(seconds=95)
    b.update(
        NOW + timedelta(seconds=96),
        BikeObservation(rider_configured=True, tracker_home=False, tracker_report=fixed),
    )
    assert b.state == "away" and b.reason == "location_away"
    assert b.cable == "disconnected"
    assert not b.wants_location(NOW + timedelta(minutes=30), charge_planned=True)


def test_hundred_watts_at_home_overrides_a_stale_away_location():
    b = BikeLifecycle()
    b.update(NOW, BikeObservation(live=True, speed=0, live_report=NOW))
    fixed = NOW + timedelta(minutes=1)
    b.update(
        fixed,
        BikeObservation(tracker_home=False, tracker_report=fixed),
    )
    assert b.state == "away" and b.cable == "disconnected"

    below = fixed + timedelta(seconds=10)
    b.update(
        below,
        BikeObservation(plug_on=True, watts=99, power_report=below),
    )
    assert b.state == "away" and b.cable == "disconnected"

    threshold = below + timedelta(seconds=1)
    b.update(
        threshold,
        BikeObservation(plug_on=True, watts=100, power_report=threshold),
    )
    assert b.state == "home_unreachable" and b.reason == "charging_power_home"
    assert b.home and not b.away
    assert b.cable == "connected" and b.cable_evidence == "measured_power"


def test_a_location_from_before_the_last_contact_decides_nothing():
    b = BikeLifecycle()
    lost_after_a_short_ride(b)
    old = NOW - timedelta(hours=1)
    b.update(NOW + timedelta(minutes=5), BikeObservation(tracker_home=False, tracker_report=old))
    assert b.state == "home_unreachable"


def test_the_bikes_own_location_brings_it_home_without_bluetooth():
    b = BikeLifecycle()
    lost_after_a_short_ride(b)
    away = NOW + timedelta(seconds=95)
    b.update(away, BikeObservation(tracker_home=False, tracker_report=away))
    assert b.state == "away"
    # The same fix again changes nothing; a newer one at home brings it back.
    b.update(NOW + timedelta(hours=1), BikeObservation(tracker_home=False, tracker_report=away))
    assert b.state == "away"
    back = NOW + timedelta(hours=8)
    b.update(back, BikeObservation(tracker_home=True, tracker_report=back))
    assert b.state == "home_unreachable" and b.reason == "location_home"
    assert b.home and not b.away


def test_the_location_is_asked_before_charging_a_bike_out_of_range():
    b = BikeLifecycle()
    b.update(NOW, BikeObservation(live=True, speed=0, live_report=NOW))
    b.update(NOW + timedelta(minutes=10), BikeObservation())
    assert b.state == "home_unreachable"
    assert not b.wants_location(NOW + timedelta(minutes=10), charge_planned=False)
    assert b.wants_location(NOW + timedelta(minutes=10), charge_planned=True)
