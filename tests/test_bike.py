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


def local_ride(b, start=0, seconds=36, speed=12):
    for second in range(start, start + seconds + 1):
        b.update(
            NOW + timedelta(seconds=second),
            BikeObservation(live=True, speed=speed, rider_home=True, rider_configured=True),
        )


def park(b, second):
    o = BikeObservation(live=True, speed=0, rider_home=True, rider_configured=True)
    b.update(NOW + timedelta(seconds=second), o)
    b.update(NOW + timedelta(seconds=second + 31), o)
    return b.control(
        NOW + timedelta(seconds=second + 31), o, scheduled=False, allow_probe=True, cancel=False
    )


def test_local_ride_inside_home_zone_triggers_once():
    b = BikeLifecycle()
    local_ride(b)
    assert b.ride_qualified
    assert park(b, 37) == (True, True)
    assert b.reason == "qualified_local_ride_arrival"
    assert not b.ride_qualified


def test_shed_movement_and_fast_short_motion_do_not_probe():
    for seconds, speed in [(40, 3), (10, 45), (36, 8)]:
        b = BikeLifecycle()
        local_ride(b, seconds=seconds, speed=speed)
        assert park(b, seconds + 1) == (False, False)
        assert b.arrived_at is None


def test_short_power_cycle_retains_measured_ride():
    b = BikeLifecycle()
    local_ride(b, seconds=20)
    b.update(NOW + timedelta(seconds=21), BikeObservation(powered=False, rider_home=True))
    local_ride(b, start=55, seconds=20)
    assert b.ride_qualified
    assert park(b, 76) == (True, True)


def test_repeated_speed_or_radio_gap_cannot_manufacture_ride():
    b = BikeLifecycle()
    for second in range(50):
        b.update(
            NOW + timedelta(seconds=second), BikeObservation(live=True, speed=15, speed_report=NOW)
        )
    assert b.ride_distance_m == 0
    assert b.ride_motion_seconds == 0
    b.update(NOW + timedelta(seconds=60), BikeObservation(live=True, speed=15))
    b.update(NOW + timedelta(seconds=80), BikeObservation(live=True, speed=15))
    assert b.ride_distance_m == 0
    assert park(b, 81) == (False, False)


def test_higher_configured_distance_and_old_episode():
    b = BikeLifecycle(200)
    local_ride(b)
    assert not b.ride_qualified
    assert park(b, 37) == (False, False)
    b.update(NOW + timedelta(minutes=16), BikeObservation(powered=False))
    assert b.ride_distance_m == 0


def test_restart_cannot_restore_qualified_local_ride():
    b = BikeLifecycle()
    local_ride(b)
    restarted = BikeLifecycle()
    restarted.restore(b.saved())
    assert park(restarted, 37) == (False, False)


def test_phone_away_blocks_local_arrival_probe():
    b = BikeLifecycle()
    local_ride(b)
    for second in (37, 68):
        b.update(
            NOW + timedelta(seconds=second),
            BikeObservation(live=True, speed=0, rider_home=False, rider_configured=True),
        )
    assert b.arrived_at is None


def test_qualified_away_ride_survives_normal_ride_duration():
    b = BikeLifecycle()
    local_ride(b)
    b.update(NOW + timedelta(seconds=110), BikeObservation(rider_home=False, rider_configured=True))
    assert b.away
    b.update(NOW + timedelta(hours=1), BikeObservation(rider_home=False, rider_configured=True))
    assert b.ride_qualified
    assert park(b, 3601) == (True, True)


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


def test_known_qualified_away_ride_accepts_ten_seconds_of_return_bluetooth():
    b = BikeLifecycle()
    local_ride(b)
    b.update(NOW + timedelta(seconds=110), BikeObservation(rider_home=False, rider_configured=True))
    assert b.away
    for second in range(200, 210):
        b.update(
            NOW + timedelta(seconds=second), BikeObservation(live=True, speed=10, rider_home=True)
        )
    assert park(b, 210) == (True, True)


def test_unknown_inbound_ride_with_only_ten_seconds_does_not_bypass_threshold():
    b = BikeLifecycle()
    local_ride(b, seconds=10, speed=12)
    assert park(b, 11) == (False, False)
