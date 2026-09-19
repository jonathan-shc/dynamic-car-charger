"""Exercise the coordinator against real Home Assistant states and services."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.dynamic_car_charger.config_flow import schema, validate
from custom_components.dynamic_car_charger.const import DEFAULTS
from custom_components.dynamic_car_charger.coordinator import ChargerCoordinator


@pytest.fixture
async def rig(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    settings = dict(
        DEFAULTS,
        charger_entity="switch.wallbox",
        soc_entity="sensor.battery",
        price_entity="sensor.nextenergy",
        power_entity="sensor.wallbox_power",
        capacity_kwh=50.0,
        power_kw=10.0,
        efficiency=1.0,
    )
    entry = SimpleNamespace(entry_id="test", data=settings, options={}, async_on_unload=lambda f: None)
    c = ChargerCoordinator(hass, entry)
    c.store = SimpleNamespace(async_save=AsyncMock(), async_delay_save=lambda *a: None)
    calls = []

    async def command(call):
        calls.append(call.service)
        hass.states.async_set("switch.wallbox", "on" if call.service == "turn_on" else "off")

    hass.services.async_register("switch", "turn_on", command)
    hass.services.async_register("switch", "turn_off", command)
    hass.states.async_set("switch.wallbox", "off")
    hass.states.async_set("sensor.battery", "20", {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.wallbox_power", "0", {"unit_of_measurement": "kW"})
    now = dt_util.utcnow()
    hass.states.async_set(
        "sensor.nextenergy",
        ".1",
        {
            "unit_of_measurement": "EUR/kWh",
            "prices": [
                {
                    "start": (now - timedelta(minutes=1)).isoformat(),
                    "end": (now + timedelta(minutes=59)).isoformat(),
                    "price": 0.1,
                },
                {
                    "start": (now + timedelta(minutes=59)).isoformat(),
                    "end": (now + timedelta(minutes=119)).isoformat(),
                    "price": 0.3,
                },
            ],
        },
    )
    c.deadline = now + timedelta(minutes=90)
    c.target = 30
    yield hass, c, calls
    await hass.async_block_till_done()
    await hass.async_stop(force=True)


async def test_preview_sends_no_command(rig):
    _, c, calls = rig
    await c.async_reconcile()
    assert c.data["status"] == "preview"
    assert c.data["slots"]
    assert calls == []


async def test_enable_executes_and_disable_pauses(rig):
    _, c, calls = rig
    await c.async_change(enabled=True)
    assert calls == ["turn_on"]
    await c.async_reconcile()
    assert c.data["status"] == "charging"
    await c.async_change(enabled=False)
    assert calls == ["turn_on", "turn_off"]


async def test_target_reached_pauses(rig):
    hass, c, calls = rig
    await c.async_change(enabled=True)
    hass.states.async_set("sensor.battery", "35", {"unit_of_measurement": "%"})
    await c.async_reconcile()
    assert calls[-1] == "turn_off"
    assert not c.data["charging_requested"]


async def test_estimated_target_waits_for_measured_soc(rig):
    hass, c, calls = rig
    c.target = 100
    hass.states.async_set("sensor.battery", "99", {"unit_of_measurement": "%"})

    await c.async_change(enabled=True)
    assert calls == ["turn_on"]
    assert c._active_charge_until is not None

    # The power integration estimates that enough energy has been delivered,
    # but the car itself still reports 99%. Charging must continue.
    c._credit_kwh = 0.5
    await c.async_reconcile()
    assert c.data["status"] == "awaiting_soc_confirmation"
    assert c.data["charging_requested"] is True
    assert calls == ["turn_on"]

    hass.states.async_set("sensor.battery", "100", {"unit_of_measurement": "%"})
    await c.async_reconcile()
    assert c.data["status"] == "target_reached"
    assert c.data["charging_requested"] is False
    assert calls[-1] == "turn_off"


async def test_immediate_charging_works_without_automatic_mode(rig):
    hass, c, calls = rig
    assert c.enabled is False

    await c.async_change(immediate_charging=True)
    assert calls == ["turn_on"]
    assert c.data["charging_requested"] is True

    await c.async_reconcile()
    assert c.data["status"] == "charging"
    assert c.data["plan_status"] == "immediate_charging"

    hass.states.async_set("sensor.battery", "30", {"unit_of_measurement": "%"})
    await c.async_reconcile()
    assert c.immediate_charging is False
    assert c.data["status"] == "target_reached"
    assert calls[-1] == "turn_off"


async def test_missing_soc_pauses_and_recovers(rig):
    hass, c, calls = rig
    await c.async_change(enabled=True)
    hass.states.async_set("sensor.battery", "unavailable")
    await c.async_reconcile()
    assert calls[-1] == "turn_off"
    await c.async_reconcile()
    assert c.data["status"] == "input_error"
    hass.states.async_set("sensor.battery", "21", {"unit_of_measurement": "%"})
    await c.async_reconcile()
    assert calls[-1] == "turn_on"


async def test_stale_power_is_not_integrated_or_treated_as_error(rig):
    _, c, calls = rig
    c._boot = dt_util.utcnow() + timedelta(seconds=1)
    await c.async_change(enabled=True)
    assert calls == ["turn_on"]
    assert c.data["status"] == "starting_charge"
    assert c.data["charging_power_report_old"] is True
    assert c._sample_power == 0


async def test_expired_deadline_stops(rig):
    _, c, calls = rig
    await c.async_change(enabled=True)
    await c.async_change(deadline=dt_util.utcnow() - timedelta(seconds=1))
    assert calls[-1] == "turn_off"
    assert not c.data["charging_requested"]


async def test_failed_service_retries_without_flooding(rig):
    hass, c, calls = rig
    from homeassistant.exceptions import HomeAssistantError

    async def broken(call):
        calls.append("failed")
        raise HomeAssistantError("offline")

    hass.services.async_register("switch", "turn_on", broken)
    await c.async_change(enabled=True)
    assert c.data["status"] == "control_error"
    await c.async_reconcile()
    assert calls == ["failed"]
    c._command_attempt_time -= timedelta(seconds=121)
    await c.async_reconcile()
    assert calls == ["failed", "failed"]


async def test_failed_disable_keeps_retrying(rig):
    hass, c, calls = rig
    await c.async_change(enabled=True)
    from homeassistant.exceptions import HomeAssistantError

    async def broken(call):
        calls.append("failed_stop")
        raise HomeAssistantError("offline")

    hass.services.async_register("switch", "turn_off", broken)
    await c.async_change(enabled=False)
    assert c._pending_stop
    c._command_attempt_time -= timedelta(seconds=121)
    await c.async_reconcile()
    assert calls[-2:] == ["failed_stop", "failed_stop"]


async def test_measured_power_credits_energy_between_soc_changes(rig):
    hass, c, _ = rig
    c.enabled = True
    await c.async_reconcile()
    c._sample_time = dt_util.utcnow() - timedelta(seconds=30)
    c._sample_power = 10
    await c.async_reconcile()
    assert c._credit_kwh == pytest.approx(10 / 120, abs=0.001)
    hass.states.async_set("sensor.battery", "21", {"unit_of_measurement": "%"})
    await c.async_reconcile()
    assert c._credit_kwh == 0


async def test_config_schema_and_units(rig):
    hass, c, _ = rig
    assert schema(c.settings)(c.settings)["power_kw"] == 10
    assert validate(hass, c.settings) == {}
    hass.states.async_set("sensor.nextenergy", "12", {"unit_of_measurement": "ct/kWh"})
    assert validate(hass, c.settings) == {"price_entity": "price_unit"}


async def test_delayed_start_is_pending_before_it_is_an_error(rig):
    hass, c, calls = rig

    async def delayed_start(call):
        calls.append(call.service)

    hass.services.async_register("switch", "turn_on", delayed_start)
    await c.async_change(enabled=True)
    assert c.data["status"] == "starting_charge"
    assert c.data["error"] is None
    assert c.data["charging_requested"] is True

    c._command_time -= timedelta(seconds=90)
    c._command_attempt_time -= timedelta(seconds=90)
    await c.async_reconcile()
    assert c.data["status"] == "starting_charge"
    assert calls == ["turn_on"]

    hass.states.async_set("switch.wallbox", "on")
    await c.async_reconcile()
    assert c.data["status"] == "charging"


async def test_unconfirmed_start_becomes_error_after_five_minutes(rig):
    hass, c, _ = rig

    async def delayed_start(call):
        pass

    hass.services.async_register("switch", "turn_on", delayed_start)
    await c.async_change(enabled=True)
    c._command_time -= timedelta(seconds=301)
    await c.async_reconcile()
    assert c.data["status"] == "control_error"
    assert c.data["error"] == "Charger did not confirm on within 5 minutes"


async def test_new_prices_may_interrupt_active_run(rig):
    hass, c, calls = rig
    await c.async_change(enabled=True)
    assert calls == ["turn_on"]
    assert c._active_charge_until is not None

    now = dt_util.utcnow()
    hass.states.async_set(
        "sensor.nextenergy",
        ".1",
        {
            "unit_of_measurement": "EUR/kWh",
            "prices": [
                {
                    "start": (now - timedelta(minutes=1)).isoformat(),
                    "end": (now + timedelta(minutes=59)).isoformat(),
                    "price": 0.5,
                },
                {
                    "start": (now + timedelta(minutes=59)).isoformat(),
                    "end": (now + timedelta(minutes=119)).isoformat(),
                    "price": 0.1,
                },
            ],
        },
    )
    await c.async_reconcile()
    assert c.data["charging_requested"] is True
    c._replan_stop_time -= timedelta(seconds=31)
    await c.async_reconcile()
    assert c.data["charging_requested"] is False
    assert calls == ["turn_on", "turn_off"]


async def test_adjacent_price_slots_do_not_restart_charging(rig):
    hass, c, calls = rig
    before = dt_util.utcnow()
    boundary = before + timedelta(minutes=1)
    c.deadline = boundary + timedelta(hours=1)
    hass.states.async_set(
        "sensor.nextenergy",
        ".1",
        {
            "unit_of_measurement": "EUR/kWh",
            "prices": [
                {
                    "start": (boundary - timedelta(hours=1)).isoformat(),
                    "end": boundary.isoformat(),
                    "price": 0.1,
                },
                {
                    "start": boundary.isoformat(),
                    "end": (boundary + timedelta(hours=1)).isoformat(),
                    "price": 0.1,
                },
            ],
        },
    )

    with patch(
        "custom_components.dynamic_car_charger.coordinator.dt_util.utcnow",
        return_value=before,
    ):
        await c.async_change(enabled=True)
    assert calls == ["turn_on"]

    with patch(
        "custom_components.dynamic_car_charger.coordinator.dt_util.utcnow",
        return_value=boundary + timedelta(seconds=1),
    ):
        await c.async_reconcile()
    assert c.data["charging_requested"] is True
    assert calls == ["turn_on"]


async def test_deadline_change_may_interrupt_active_run(rig):
    hass, c, calls = rig
    now = dt_util.utcnow()
    hass.states.async_set(
        "sensor.nextenergy",
        ".1",
        {
            "unit_of_measurement": "EUR/kWh",
            "prices": [
                {
                    "start": (now - timedelta(minutes=1)).isoformat(),
                    "end": (now + timedelta(minutes=59)).isoformat(),
                    "price": 0.1,
                },
                {
                    "start": (now + timedelta(minutes=59)).isoformat(),
                    "end": (now + timedelta(minutes=119)).isoformat(),
                    "price": 0.3,
                },
                {
                    "start": (now + timedelta(minutes=119)).isoformat(),
                    "end": (now + timedelta(minutes=179)).isoformat(),
                    "price": 0.05,
                },
            ],
        },
    )
    await c.async_change(enabled=True)
    assert calls == ["turn_on"]

    await c.async_change(deadline=now + timedelta(minutes=179))
    assert c.data["charging_requested"] is True
    c._replan_stop_time -= timedelta(seconds=31)
    await c.async_reconcile()
    assert c.data["charging_requested"] is False
    assert calls == ["turn_on", "turn_off"]
