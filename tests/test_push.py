"""Live Activities and notifications for the Charge Remote app, pushed from here."""

from datetime import UTC, datetime, timedelta

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
    # And a notification, as it went from planned to charging.
    assert any(kind == "alert" and token == "dev" for token, kind, _ in hub.sent)
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
