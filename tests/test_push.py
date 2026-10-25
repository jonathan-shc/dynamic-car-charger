"""Live Activities and notifications for the Charge Remote app, pushed from here."""

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.dynamic_car_charger.push import (
    PushHub,
    apple_time,
    content_state,
    headline,
)


def plan(status, start=None, end=None, **extra):
    data = {"status": status, "target_percentage": 80, "estimated_soc": 55.4, "slots": []}
    if start:
        data["slots"] = [{"start": start.isoformat(), "end": end.isoformat(), "estimated": False}]
    data.update(extra)
    return data


def test_apple_time_counts_from_2001():
    assert apple_time(datetime(2001, 1, 1, 0, 1, tzinfo=UTC)) == 60


def test_headline_reads_like_the_app():
    now = dt_util.utcnow()
    zone = dt_util.get_default_time_zone()
    end = now + timedelta(minutes=50)
    assert headline(plan("charging", now - timedelta(minutes=5), end), "nl", now, "car") == (
        "Laadt tot " + end.astimezone(zone).strftime("%H:%M")
    )
    later = now + timedelta(hours=2)
    text = headline(plan("scheduled", later, later + timedelta(hours=1)), "en", now, "car")
    assert text.startswith("Next charge ") and later.astimezone(zone).strftime("%H:%M") in text
    assert headline(plan("waiting_for_car"), "nl", now, "bike") == "Wacht op de e-bike"
    assert headline(plan("control_error"), "nl", now, "scooter") == "Stekker reageert niet"


def test_content_state_has_the_apps_field_names():
    now = dt_util.utcnow()
    state = content_state(
        plan(
            "charging",
            now - timedelta(minutes=5),
            now + timedelta(hours=1),
            deadline=(now + timedelta(hours=8)).isoformat(),
        ),
        "entry",
        "nl",
        0.8,
        "scooter",
    )
    assert state["charging"] is True and state["powerKw"] == 0.8
    assert state["soc"] == 55.4 and state["target"] == 80
    assert state["vehicleEntryID"] == "entry"
    assert abs(state["readyBy"] - apple_time(now + timedelta(hours=8))) < 1
    assert "cheapPrice" not in state


@pytest.fixture
async def hub(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hub = PushHub(hass)
    hub.store.async_delay_save = lambda *args: None
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    await hub.async_set_credentials("KEY1234567", "TEAM123456", "app.chargeremote", pem)
    sent = []

    async def send(token, push_type, payload, priority=10):
        sent.append((token, push_type, payload))
        return 200

    hub._send = send
    hub.sent = sent
    hub.public = key.public_key()
    hub._loaded_at = 0  # past the quiet start
    yield hub
    await hass.async_stop(force=True)


async def test_the_signed_token_names_the_key_and_team(hub):
    token = hub._jwt()
    assert jwt.get_unverified_header(token)["kid"] == "KEY1234567"
    assert jwt.decode(token, hub.public, algorithms=["ES256"])["iss"] == "TEAM123456"


async def test_charging_starts_updates_and_ends_the_live_activity(hub):
    hub.register("phone", {"start_token": "start", "device_token": "dev", "alerts": True})
    now = dt_util.utcnow()
    await hub.async_vehicle_update("car", "Auto", plan("scheduled"), 0, "car")
    assert hub.sent == []  # not charging: nothing to show
    charging = plan("charging", now, now + timedelta(hours=1))
    await hub.async_vehicle_update("car", "Auto", charging, 10.4, "car")
    started = [payload for _, kind, payload in hub.sent if kind == "liveactivity"]
    assert started[0]["aps"]["event"] == "start"
    assert started[0]["aps"]["attributes-type"] == "ChargeActivityAttributes"
    assert started[0]["aps"]["attributes"] == {"vehicle": "car", "name": "Auto", "scooter": False}
    # Its start carries the notification: no second one.
    assert not any(kind == "alert" for _, kind, _ in hub.sent)
    # The phone's activity reports its own token: updates go there, not too often.
    hub.register("phone", {"activities": {"car": "activity"}})
    hub.sent.clear()
    await hub.async_vehicle_update("car", "Auto", charging, 10.4, "car")
    assert hub.sent == []  # nothing changed since the start
    charging["estimated_soc"] = 58
    await hub.async_vehicle_update("car", "Auto", charging, 10.4, "car")
    assert [(token, payload["aps"]["event"]) for token, _, payload in hub.sent] == [
        ("activity", "update")
    ]
    hub.sent.clear()
    await hub.async_vehicle_update("car", "Auto", charging, 10.4, "car")
    assert hub.sent == []
    # Done: the activity ends, and a notification says so.
    await hub.async_vehicle_update("car", "Auto", plan("target_reached"), 0, "car")
    events = [
        payload["aps"].get("event") for token, kind, payload in hub.sent if kind == "liveactivity"
    ]
    assert events == ["end"]
    assert any(kind == "alert" for _, kind, _ in hub.sent)
    assert "car" not in hub.devices["phone"]["activities"]


async def test_a_phone_only_hears_about_the_vehicles_it_follows(hub):
    hub.register("phone", {"start_token": "start", "entries": ["scooter"]})
    now = dt_util.utcnow()
    await hub.async_vehicle_update(
        "car", "Auto", plan("charging", now, now + timedelta(hours=1)), 10, "car"
    )
    assert hub.sent == []


async def test_nothing_is_sent_without_a_key(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hub = PushHub(hass)
    hub.register("phone", {"start_token": "start"})
    called = []

    async def send(*args, **kwargs):
        called.append(args)
        return 200

    hub._send = send
    await hub.async_vehicle_update("car", "Auto", plan("charging"), 10, "car")
    assert called == []
    await hass.async_stop(force=True)


async def test_a_charge_about_to_start_with_nothing_plugged_in_is_told_once(hub):
    hub.register("phone", {"device_token": "dev", "alerts": True})
    now = dt_util.utcnow()
    soon = plan("scheduled", now + timedelta(minutes=10), now + timedelta(hours=1), enabled=True)
    await hub.async_vehicle_update("car", "Auto", soon, 0, "car")  # first: only remembered
    soon["car_connected"] = False
    await hub.async_vehicle_update("car", "Auto", soon, 0, "car")
    alerts = [payload["aps"]["alert"] for _, kind, payload in hub.sent if kind == "alert"]
    assert alerts and alerts[0]["title"] == "Auto isn't plugged in"
    await hub.async_vehicle_update("car", "Auto", soon, 0, "car")
    assert len([1 for _, kind, _ in hub.sent if kind == "alert"]) == 1  # once per block
    # An e-bike that isn't home says so.
    hub.sent.clear()
    bike = plan("scheduled", now + timedelta(minutes=5), now + timedelta(hours=1), enabled=True)
    await hub.async_vehicle_update("bike", "E-bike", bike, 0, "bike")
    bike["bike"] = {"state": "away"}
    await hub.async_vehicle_update("bike", "E-bike", bike, 0, "bike")
    assert [payload["aps"]["alert"]["title"] for _, kind, payload in hub.sent] == [
        "E-bike isn't home"
    ]


async def test_the_push_client_leaves_the_shared_ssl_context_alone(tmp_path):
    from homeassistant.util.ssl import SSL_ALPN_HTTP11, client_context

    from custom_components.dynamic_car_charger.push import http2_client

    hass = HomeAssistant(str(tmp_path))
    shared = client_context(alpn_protocols=SSL_ALPN_HTTP11)
    client = http2_client(hass)
    pool = client._transport._pool
    assert pool._http2 is True
    assert pool._ssl_context is not shared
    await hass.async_stop(force=True)


async def test_push_status_says_whether_the_key_signs_and_who_registered(hub):
    hub.register(
        "phone-1234567890", {"start_token": "start", "device_token": "dev", "alerts": True}
    )
    status = hub.status()
    assert status["configured"] and status["key_signs"] and status["key_error"] is None
    assert status["devices"][0]["device"] == "phone-12"
    assert status["devices"][0]["live_activity_start_token"] is True
    assert "key" not in status and "secret" not in str(status)
    hub.credentials["key"] = "not a key"
    broken = hub.status()
    assert broken["key_signs"] is False and broken["key_error"]


async def test_a_live_activity_starts_once_and_only_when_energy_flows(hub):
    import asyncio

    hub.register("phone", {"start_token": "start"})
    now = dt_util.utcnow()
    # A plug switched on for a moment: starting, nothing drawn yet.
    await hub.async_vehicle_update("bike", "E-bike", plan("starting_charge"), 0, "bike")
    charging = plan("charging", now, now + timedelta(hours=1))
    await hub.async_vehicle_update("bike", "E-bike", charging, 0, "bike")
    assert hub.sent == []
    # Two plan updates at once, then more while charging: one start.
    await asyncio.gather(
        hub.async_vehicle_update("bike", "E-bike", charging, 0.13, "bike"),
        hub.async_vehicle_update("bike", "E-bike", charging, 0.13, "bike"),
    )
    charging["estimated_soc"] = 70
    await hub.async_vehicle_update("bike", "E-bike", charging, 0.13, "bike")
    assert [payload["aps"]["event"] for _, _, payload in hub.sent] == ["start"]
    # After the charge, the next one starts its own.
    await hub.async_vehicle_update("bike", "E-bike", plan("target_reached"), 0, "bike")
    await hub.async_vehicle_update("bike", "E-bike", charging, 0.13, "bike")
    assert [payload["aps"]["event"] for _, _, payload in hub.sent] == ["start", "start"]


async def test_live_activity_keeps_the_original_period_start_when_the_plan_moves(hub):
    hub.register("phone", {"start_token": "start"})
    first_update = datetime(2026, 10, 25, 12, 10, tzinfo=UTC)
    original_start = first_update - timedelta(minutes=10)
    first_plan = plan("charging", original_start, first_update + timedelta(hours=1))

    with patch(
        "custom_components.dynamic_car_charger.push.dt_util.utcnow", return_value=first_update
    ):
        await hub.async_vehicle_update("car", "Auto", first_plan, 10, "car")

    start_state = hub.sent[-1][2]["aps"]["content-state"]
    assert start_state["windowStart"] == apple_time(original_start)

    # A normal replan clips the active slot's start to the current time. The Live
    # Activity must still describe the charging period that began at 12:00.
    hub.register("phone", {"activities": {"car": "activity"}})
    second_update = first_update + timedelta(minutes=5)
    moved_plan = plan("charging", second_update, first_update + timedelta(hours=1))
    moved_plan["estimated_soc"] = 56.4
    with patch(
        "custom_components.dynamic_car_charger.push.dt_util.utcnow", return_value=second_update
    ):
        await hub.async_vehicle_update("car", "Auto", moved_plan, 10, "car")

    update_state = hub.sent[-1][2]["aps"]["content-state"]
    assert hub.sent[-1][2]["aps"]["event"] == "update"
    assert update_state["windowStart"] == apple_time(original_start)

    # Once that period is over, a later charge gets its own start time.
    await hub.async_vehicle_update("car", "Auto", plan("scheduled"), 0, "car")
    hub.register("phone", {"activities": {"car": ""}})
    hub.sent.clear()
    next_start = second_update + timedelta(hours=2)
    next_plan = plan("charging", next_start, next_start + timedelta(hours=1))
    with patch(
        "custom_components.dynamic_car_charger.push.dt_util.utcnow", return_value=next_start
    ):
        await hub.async_vehicle_update("car", "Auto", next_plan, 10, "car")
    assert hub.sent[-1][2]["aps"]["content-state"]["windowStart"] == apple_time(next_start)


def alerts(hub):
    return [
        (payload.get("kind"), payload["aps"]) for _, kind, payload in hub.sent if kind == "alert"
    ]


async def test_plugging_in_tells_the_plan_with_buttons_to_change_it(hub):
    hub.register("phone", {"device_token": "dev", "alerts": True})
    now = dt_util.utcnow()
    deadline = (now + timedelta(days=1)).replace(hour=7, minute=30)
    waiting = plan("scheduled", deadline=deadline.isoformat(), car_connected=False)
    await hub.async_vehicle_update("car", "Auto", waiting, 0, "car")  # first: remembered
    await hub.async_vehicle_update("car", "Auto", waiting, 0, "car")
    plugged = dict(waiting, car_connected=True)
    await hub.async_vehicle_update("car", "Auto", plugged, 0, "car")
    [(kind, aps)] = alerts(hub)
    assert kind == "plugged_in" and aps["category"] == "PLAN"
    assert aps["alert"]["title"] == "Auto plugged in"
    assert aps["alert"]["body"].startswith("Ready ") and "80%" in aps["alert"]["body"]
    assert hub.sent[-1][2]["entry_id"] == "car"
    # Staying plugged in says nothing more.
    await hub.async_vehicle_update("car", "Auto", plugged, 0, "car")
    assert len(alerts(hub)) == 1


async def test_unplugged_while_charging_and_a_target_out_of_reach_are_told(hub):
    hub.register("phone", {"device_token": "dev", "alerts": True})
    now = dt_util.utcnow()
    deadline = (now + timedelta(hours=3)).isoformat()
    charging = plan(
        "charging", now, now + timedelta(hours=1), car_connected=True, deadline=deadline
    )
    await hub.async_vehicle_update("car", "Auto", charging, 10, "car")
    await hub.async_vehicle_update("car", "Auto", charging, 10, "car")
    await hub.async_vehicle_update(
        "car", "Auto", plan("waiting_for_car", car_connected=False, deadline=deadline), 0, "car"
    )
    assert [kind for kind, _ in alerts(hub)] == ["interrupted"]
    hub.sent.clear()
    short = plan("insufficient_time", car_connected=True, deadline=deadline, shortfall_kwh=4)
    await hub.async_vehicle_update("car", "Auto", short, 0, "car")
    assert [kind for kind, _ in alerts(hub)] == ["plugged_in"]  # not yet: maybe a moment
    later = dt_util.utcnow() + timedelta(minutes=11)
    with patch("custom_components.dynamic_car_charger.push.dt_util.utcnow", return_value=later):
        await hub.async_vehicle_update("car", "Auto", short, 0, "car")
        await hub.async_vehicle_update("car", "Auto", short, 0, "car")
    assert [kind for kind, _ in alerts(hub)] == ["plugged_in", "shortfall"]


async def test_a_plan_short_only_for_want_of_prices_says_nothing(hub):
    hub.register("phone", {"device_token": "dev", "alerts": True})
    deadline = (dt_util.utcnow() + timedelta(days=4)).isoformat()
    # As after a restart, while the forecast loads: short, but prices aren't known.
    loading = plan(
        "waiting_for_prices", deadline=deadline, shortfall_kwh=5.2, coverage_complete=False
    )
    later = dt_util.utcnow() + timedelta(minutes=30)
    await hub.async_vehicle_update("car", "Auto", loading, 0, "car")
    with patch("custom_components.dynamic_car_charger.push.dt_util.utcnow", return_value=later):
        await hub.async_vehicle_update("car", "Auto", loading, 0, "car")
    assert alerts(hub) == []


async def test_nothing_is_told_right_after_home_assistant_started(hub):
    hub.register("phone", {"device_token": "dev", "alerts": True})
    hub._loaded_at = time.time()
    await hub.async_vehicle_update("car", "Auto", plan("scheduled", car_connected=False), 0, "car")
    await hub.async_vehicle_update("car", "Auto", plan("scheduled", car_connected=True), 0, "car")
    assert alerts(hub) == []


async def test_a_phone_chooses_which_notifications_it_gets(hub):
    hub.register("phone", {"device_token": "dev", "alerts": True, "alert_types": ["done"]})
    waiting = plan("scheduled", car_connected=False)
    await hub.async_vehicle_update("car", "Auto", waiting, 0, "car")
    await hub.async_vehicle_update("car", "Auto", dict(waiting, car_connected=True), 0, "car")
    assert alerts(hub) == []
    await hub.async_vehicle_update(
        "car", "Auto", plan("target_reached", car_connected=True), 0, "car"
    )
    assert [kind for kind, _ in alerts(hub)] == ["done"]
