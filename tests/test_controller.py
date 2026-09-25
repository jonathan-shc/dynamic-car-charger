"""Exercise the coordinator against real Home Assistant states and services."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from custom_components.dynamic_car_charger.config_flow import (
    OptionsFlow,
    interval_default,
    schema,
    validate,
)
from custom_components.dynamic_car_charger.const import DEFAULTS, EVENT_CAR_CONNECTED
from custom_components.dynamic_car_charger.coordinator import ChargerCoordinator
from custom_components.dynamic_car_charger.forecaster import ForecastUnavailable
from custom_components.dynamic_car_charger.planner import Slot, make_plan
from custom_components.dynamic_car_charger.price_forecast import Calibration


@pytest.fixture
async def rig(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    settings = dict(
        DEFAULTS,
        charger_entity="switch.charger",
        soc_entity="sensor.battery",
        price_entity="sensor.electricity_price",
        power_entity="sensor.charging_power",
        capacity_kwh=50.0,
        power_kw=10.0,
        efficiency=1.0,
    )
    entry = SimpleNamespace(
        entry_id="test",
        title="Dynamic Car Charger",
        data=settings,
        options={},
        async_on_unload=lambda f: None,
    )
    c = ChargerCoordinator(hass, entry)
    c.store = SimpleNamespace(async_save=AsyncMock(), async_delay_save=lambda *a: None)
    calls = []

    async def command(call):
        calls.append(call.service)
        hass.states.async_set("switch.charger", "on" if call.service == "turn_on" else "off")

    hass.services.async_register("switch", "turn_on", command)
    hass.services.async_register("switch", "turn_off", command)
    hass.states.async_set("switch.charger", "off")
    hass.states.async_set("sensor.battery", "20", {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.charging_power", "0", {"unit_of_measurement": "kW"})
    now = dt_util.utcnow()
    hass.states.async_set(
        "sensor.electricity_price",
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
    assert c.data["status"] == "stopping_charge"
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
    assert c.data["status"] == "stopping_charge"
    await c.async_reconcile()
    assert c.data["status"] == "target_reached"
    assert calls[-1] == "turn_off"


async def test_charger_status_fires_connected_event(rig):
    hass, c, _ = rig
    c.settings["connected_entity"] = "sensor.charger_status"
    c.settings["connected_states"] = ["Locked, car connected"]
    received = []
    hass.bus.async_listen(EVENT_CAR_CONNECTED, received.append)

    c._changed(
        SimpleNamespace(
            data={
                "entity_id": "sensor.charger_status",
                "old_state": State("sensor.charger_status", "Locked"),
                "new_state": State("sensor.charger_status", "Locked, car connected"),
            }
        )
    )
    await hass.async_block_till_done()

    assert len(received) == 1
    assert received[0].data["status"] == "Locked, car connected"


async def test_charging_unlocks_charger_before_resume(rig):
    hass, c, calls = rig
    c.settings["lock_entity"] = "lock.charger"
    hass.states.async_set("lock.charger", "locked")
    lock_calls = []

    async def unlock(call):
        lock_calls.append(call.service)
        hass.states.async_set("lock.charger", "unlocked")

    hass.services.async_register("lock", "unlock", unlock)
    await c.async_change(enabled=True)

    assert lock_calls == ["unlock"]
    assert calls == []
    assert c.data["status"] == "unlocking_charger"

    await c.async_reconcile()
    assert calls == ["turn_on"]


async def test_driving_locks_charger(rig):
    hass, c, _ = rig
    c.settings["lock_entity"] = "lock.charger"
    c.settings["vehicle_state_entity"] = "sensor.car_state"
    c.settings["driving_states"] = ["Driving"]
    hass.states.async_set("lock.charger", "unlocked")
    lock_calls = []

    async def lock(call):
        lock_calls.append(call.service)
        hass.states.async_set("lock.charger", "locked")

    hass.services.async_register("lock", "lock", lock)
    c._changed(
        SimpleNamespace(
            data={
                "entity_id": "sensor.car_state",
                "old_state": State("sensor.car_state", "Parked"),
                "new_state": State("sensor.car_state", "Driving"),
            }
        )
    )
    await hass.async_block_till_done()

    assert lock_calls == ["lock"]
    assert hass.states.get("lock.charger").state == "locked"


async def test_driving_prevents_charger_from_being_unlocked(rig):
    hass, c, calls = rig
    c.settings["lock_entity"] = "lock.charger"
    c.settings["vehicle_state_entity"] = "sensor.car_state"
    c.settings["driving_states"] = ["Driving"]
    hass.states.async_set("lock.charger", "locked")
    hass.states.async_set("sensor.car_state", "Driving")
    unlock_calls = []

    async def unlock(call):
        unlock_calls.append(call.service)

    hass.services.async_register("lock", "unlock", unlock)
    await c.async_change(enabled=True)

    assert unlock_calls == []
    assert calls == []
    assert c.data["status"] == "waiting_for_car"


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
    c.settings["deadline_grace_minutes"] = 0
    await c.async_change(enabled=True)
    await c.async_change(deadline=dt_util.utcnow() - timedelta(seconds=1))
    assert calls[-1] == "turn_off"
    assert not c.data["charging_requested"]


async def test_active_session_continues_after_deadline_within_grace(rig):
    _, c, calls = rig
    now = dt_util.utcnow()
    c.settings["deadline_grace_minutes"] = 60
    c.deadline = now + timedelta(seconds=1)

    await c.async_change(enabled=True)
    assert calls == ["turn_on"]

    with patch(
        "custom_components.dynamic_car_charger.coordinator.dt_util.utcnow",
        return_value=now + timedelta(minutes=30),
    ):
        await c.async_reconcile()
    assert c.data["status"] == "charging_overtime"
    assert c.data["deadline_extension_active"] is True
    assert c.data["charging_requested"] is True
    assert calls == ["turn_on"]

    with patch(
        "custom_components.dynamic_car_charger.coordinator.dt_util.utcnow",
        return_value=now + timedelta(minutes=61, seconds=1),
    ):
        await c.async_reconcile()
    assert c.data["status"] == "stopping_charge"
    assert c.data["charging_requested"] is False
    assert calls[-1] == "turn_off"


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


async def _restart(hass, c, options=None):
    """Start a new coordinator for the same entry with the last saved data."""
    saved = c.store.async_save.call_args.args[0]
    entry = SimpleNamespace(
        entry_id="test",
        title="Dynamic Car Charger",
        data=c.entry.data,
        options=options or {},
        async_on_unload=lambda f: None,
    )
    restarted = ChargerCoordinator(hass, entry)
    restarted.store = SimpleNamespace(
        async_load=AsyncMock(return_value=saved),
        async_save=AsyncMock(),
        async_delay_save=lambda *a: None,
    )
    await restarted.async_start()
    await restarted.async_stop()
    return restarted


async def test_price_threshold_survives_restart(rig):
    hass, c, _ = rig
    await c.async_change_price_threshold(0.12)
    restarted = await _restart(hass, c)
    assert restarted.settings["max_price_eur_kwh"] == 0.12


async def test_changed_configured_threshold_replaces_live_threshold(rig):
    hass, c, _ = rig
    await c.async_change_price_threshold(0.12)
    restarted = await _restart(hass, c, options={"max_price_eur_kwh": 0.25})
    assert restarted.settings["max_price_eur_kwh"] == 0.25


async def test_config_schema_and_units(rig):
    hass, c, _ = rig
    assert schema(c.settings)(c.settings)["power_kw"] == 10
    assert validate(hass, c.settings) == {}
    # Cents and other currencies per kWh are accepted; other units are not.
    hass.states.async_set("sensor.electricity_price", "12", {"unit_of_measurement": "ct/kWh"})
    assert validate(hass, c.settings) == {}
    hass.states.async_set("sensor.electricity_price", "120", {"unit_of_measurement": "EUR/MWh"})
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

    hass.states.async_set("switch.charger", "on")
    await c.async_reconcile()
    assert c.data["status"] == "charging"


async def test_unconfirmed_start_becomes_error_but_keeps_retrying(rig):
    hass, c, calls = rig

    async def delayed_start(call):
        calls.append(call.service)

    hass.services.async_register("switch", "turn_on", delayed_start)
    await c.async_change(enabled=True)
    c._command_time -= timedelta(seconds=301)
    await c.async_reconcile()
    assert c.data["status"] == "control_error"
    assert c.data["error"] == "Charger did not confirm on within 5 minutes; retrying"
    assert calls == ["turn_on"]

    c._command_attempt_time -= timedelta(seconds=121)
    await c.async_reconcile()
    assert calls == ["turn_on", "turn_on"]
    assert c.data["status"] == "control_error"

    hass.states.async_set("switch.charger", "on")
    await c.async_reconcile()
    assert c.data["status"] == "charging"
    assert c.data["error"] is None


async def test_new_prices_may_interrupt_active_run(rig):
    hass, c, calls = rig
    await c.async_change(enabled=True)
    assert calls == ["turn_on"]
    assert c._active_charge_until is not None

    now = dt_util.utcnow()
    hass.states.async_set(
        "sensor.electricity_price",
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
        "sensor.electricity_price",
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
        "sensor.electricity_price",
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


async def test_soc_confirmation_charges_at_most_30_minutes_past_estimate(rig):
    hass, c, calls = rig
    c.target = 100
    hass.states.async_set("sensor.battery", "99", {"unit_of_measurement": "%"})
    await c.async_change(enabled=True)
    c._credit_kwh = 0.5
    await c.async_reconcile()
    assert c.data["status"] == "awaiting_soc_confirmation"
    assert c.data["charging_requested"] is True
    assert c.data["soc_confirmation_until"] is not None

    # The car never reports 100%, for example because its own limit is lower.
    c._soc_confirmation_since -= timedelta(minutes=30)
    await c.async_reconcile()
    assert c.data["charging_requested"] is False
    assert calls[-1] == "turn_off"
    await c.async_reconcile()
    assert c.data["status"] == "awaiting_soc_confirmation"
    assert c.data["charging_requested"] is False
    assert calls == ["turn_on", "turn_off"]


async def test_old_battery_report_is_flagged_but_does_not_stop_the_plan(rig):
    _, c, calls = rig
    c.settings["soc_max_age_minutes"] = 5
    later = dt_util.utcnow() + timedelta(minutes=10)
    with patch(
        "custom_components.dynamic_car_charger.coordinator.dt_util.utcnow",
        return_value=later,
    ):
        await c.async_change(enabled=True)
    assert c.data["soc_report_old"] is True
    assert c.data["charging_requested"] is True
    assert calls == ["turn_on"]


async def test_unconfirmed_unlock_becomes_error_but_keeps_retrying(rig):
    hass, c, calls = rig
    c.settings["lock_entity"] = "lock.charger"
    hass.states.async_set("lock.charger", "locked")
    unlock_calls = []

    async def unlock(call):
        unlock_calls.append(call.service)

    hass.services.async_register("lock", "unlock", unlock)
    await c.async_change(enabled=True)
    assert c.data["status"] == "unlocking_charger"

    c._unlock_command_time -= timedelta(seconds=301)
    c._unlock_attempt_time -= timedelta(seconds=121)
    await c.async_reconcile()
    assert unlock_calls == ["unlock", "unlock"]
    assert c.data["status"] == "control_error"

    hass.states.async_set("lock.charger", "unlocked")
    await c.async_reconcile()
    assert calls == ["turn_on"]


async def test_session_cost_accounts_measured_energy(rig):
    hass, c, _ = rig
    await c.async_change(enabled=True)
    assert c.session["active"] is True

    c._sample_time = dt_util.utcnow() - timedelta(seconds=36)
    c._sample_power = 10
    await c.async_reconcile()
    assert c.session["energy_kwh"] == pytest.approx(0.1, abs=0.001)
    assert c.session["cost_eur"] == pytest.approx(0.01, abs=0.0001)
    assert c.session["cost_complete"] is True

    hass.states.async_set("sensor.battery", "35", {"unit_of_measurement": "%"})
    await c.async_reconcile()
    assert c.session["active"] is False
    assert c.session["ended"] is not None
    assert c.session["energy_kwh"] == pytest.approx(0.1, abs=0.001)


async def test_session_without_price_marks_cost_incomplete(rig):
    hass, c, _ = rig
    await c.async_change(immediate_charging=True)
    hass.states.async_set("sensor.electricity_price", "unavailable")
    c._sample_time = dt_util.utcnow() - timedelta(seconds=36)
    c._sample_power = 10
    await c.async_reconcile()
    assert c.session["energy_kwh"] == pytest.approx(0.1, abs=0.001)
    assert c.session["cost_complete"] is False


async def test_persistent_input_error_raises_and_clears_repair_issue(rig):
    hass, c, _ = rig
    from homeassistant.helpers import issue_registry as ir

    await ir.async_load(hass)
    await c.async_change(enabled=True)
    hass.states.async_set("sensor.battery", "unavailable")
    await c.async_reconcile()
    await c.async_reconcile()
    assert c.data["status"] == "input_error"
    registry = ir.async_get(hass)
    assert registry.async_get_issue("dynamic_car_charger", "input_error_test") is None

    c._problem_since -= timedelta(minutes=30)
    await c.async_reconcile()
    issue = registry.async_get_issue("dynamic_car_charger", "input_error_test")
    assert issue is not None
    assert issue.translation_placeholders["error"] == "soc_entity is unavailable"

    hass.states.async_set("sensor.battery", "21", {"unit_of_measurement": "%"})
    await c.async_reconcile()
    assert registry.async_get_issue("dynamic_car_charger", "input_error_test") is None


async def test_set_session_service_changes_session(rig):
    hass, c, calls = rig
    from custom_components.dynamic_car_charger import async_setup

    await async_setup(hass, {})
    with patch("custom_components.dynamic_car_charger._coordinator_for", return_value=c):
        await hass.services.async_call(
            "dynamic_car_charger",
            "set_session",
            {
                "target_percentage": 70,
                "ready_by": "2030-01-02T07:30:00+01:00",
                "automatic_charging": True,
            },
            blocking=True,
        )
    assert c.target == 70
    assert c.deadline == datetime(2030, 1, 2, 6, 30, tzinfo=UTC)
    assert c.enabled is True


async def test_set_session_service_requires_a_change(rig):
    hass, c, _ = rig
    from homeassistant.exceptions import ServiceValidationError

    from custom_components.dynamic_car_charger import async_setup

    await async_setup(hass, {})
    with (
        patch("custom_components.dynamic_car_charger._coordinator_for", return_value=c),
        pytest.raises(ServiceValidationError),
    ):
        await hass.services.async_call("dynamic_car_charger", "set_session", {}, blocking=True)


async def test_deadline_preset_keeps_local_time_across_dst_change(rig):
    _, c, _ = rig
    evening = datetime(2026, 10, 24, 20, 15, tzinfo=ZoneInfo("Europe/Amsterdam"))
    with patch(
        "custom_components.dynamic_car_charger.coordinator.dt_util.now",
        return_value=evening,
    ):
        await c.async_set_deadline_preset(1, 7)
    # Summer time ends on 25 October: 07:00 local is 06:00 UTC.
    assert c.deadline == datetime(2026, 10, 25, 6, tzinfo=UTC)


async def test_options_flow_updates_unique_id_for_new_charger(rig):
    hass, c, _ = rig
    hass.states.async_set("switch.other_charger", "off")
    entry = SimpleNamespace(
        entry_id="test", unique_id="switch.charger", data=c.settings, options={}
    )
    config_entries = SimpleNamespace(
        async_entries=lambda domain: [entry], async_update_entry=Mock()
    )

    class Flow(OptionsFlow):
        config_entry = entry

    flow = Flow()
    flow.hass = SimpleNamespace(states=hass.states, config_entries=config_entries)
    result = await flow.async_step_init({**c.settings, "charger_entity": "switch.other_charger"})
    assert result["type"] == "create_entry"
    config_entries.async_update_entry.assert_called_once_with(
        entry, unique_id="switch.other_charger"
    )


def test_interval_default_accepts_stored_numbers():
    assert interval_default({"interval_minutes": 60.0}) == "60"
    assert interval_default({"interval_minutes": 15}) == "15"
    assert interval_default({}) == "60"


class FakeForecaster:
    """Stands in for PriceForecaster without network access."""

    def __init__(self, hass=None, slots=(), error=None):
        self.status = "ready"
        self.error = None
        self.model = None
        self.estimates = {}
        self.trained_at = None
        self.estimated_at = None
        self.slots = list(slots)
        self.fail = error
        self.async_update = AsyncMock()

    def estimate(self, known, deadline):
        if self.fail:
            raise ForecastUnavailable(self.fail)
        return self.slots, Calibration(1.21, 0.1327, 48)


def _forecast_rig(c, **kwargs):
    """Deadline beyond the published prices, with a cheap estimated hour."""
    now = dt_util.utcnow()
    c.deadline = now + timedelta(hours=5)
    published_end = now + timedelta(minutes=119)
    cheap = Slot(published_end, published_end + timedelta(hours=1), 0.05, True)
    c.forecaster = FakeForecaster(slots=[cheap], **kwargs)
    return cheap


async def test_forecast_waits_for_cheaper_estimated_hour(rig):
    _, c, calls = rig
    cheap = _forecast_rig(c)
    c.use_forecast = True
    await c.async_change(enabled=True)
    assert c.data["planning_method"] == "forecast"
    assert c.data["charging_requested"] is False
    assert c.data["status"] == "provisional_plan"
    assert c.data["plan_is_provisional"] is True
    assert [s["estimated"] for s in c.data["slots"]] == [True]
    assert c.data["slots"][0]["start"] == cheap.start.isoformat()
    assert calls == []


async def test_threshold_charges_in_the_same_situation(rig):
    _, c, calls = rig
    _forecast_rig(c)
    await c.async_change(enabled=True)
    assert c.data["planning_method"] == "threshold"
    assert c.data["forecast_status"] == "off"
    assert c.data["charging_requested"] is True
    assert calls == ["turn_on"]


async def test_unavailable_forecast_falls_back_to_threshold(rig):
    _, c, calls = rig
    _forecast_rig(c, error="Price forecast is not ready")
    c.use_forecast = True
    await c.async_change(enabled=True)
    assert c.data["planning_method"] == "threshold"
    assert c.data["forecast_error"] == "Price forecast is not ready"
    assert calls == ["turn_on"]


async def test_published_prices_to_deadline_ignore_forecast(rig):
    _, c, _ = rig
    c.forecaster = FakeForecaster()
    c.use_forecast = True
    await c.async_reconcile()
    assert c.data["planning_method"] == "published_prices"
    assert c.data["plan_is_provisional"] is False


async def test_estimated_slot_never_starts_charging(rig):
    _, c, _ = rig
    now = dt_util.utcnow()
    estimated = Slot(now - timedelta(minutes=1), now + timedelta(minutes=59), 0.01, True)
    plan = make_plan([estimated], now, now + timedelta(hours=1), 20, 30, 50, 10, 1)
    assert plan.slots and plan.slots[0].estimated
    assert plan.charging_at(now) is False


async def test_forecast_switch_starts_updates_and_survives_restart(rig):
    hass, c, _ = rig
    c.forecaster = FakeForecaster()
    await c.async_change(use_forecast=True)
    await hass.async_block_till_done()
    c.forecaster.async_update.assert_awaited()
    assert c._forecast_unsub is not None

    with patch("custom_components.dynamic_car_charger.coordinator.PriceForecaster", FakeForecaster):
        restarted = await _restart(hass, c)
    assert restarted.use_forecast is True

    await c.async_change(use_forecast=False)
    assert c._forecast_unsub is None
    assert c.forecaster.status == "off"


EXPECTED_ENTITIES = {
    "sensor": {"plan", "cost", "session_cost", "price_forecast_status"},
    "number": {"target", "price_threshold"},
    "datetime": {"deadline"},
    "switch": {"automatic", "immediate_charging", "price_forecast"},
    "button": {"tomorrow_0700", "tomorrow_0900", "day_after_tomorrow_0900"},
}


async def test_every_platform_creates_its_entities_with_names(rig):
    import importlib
    import json
    from pathlib import Path

    from custom_components.dynamic_car_charger.const import PLATFORMS

    hass, c, _ = rig
    strings = json.loads(
        (
            Path(__file__).parents[1] / "custom_components/dynamic_car_charger/strings.json"
        ).read_text()
    )
    entry = SimpleNamespace(entry_id="test", runtime_data=c)
    assert set(PLATFORMS) == set(EXPECTED_ENTITIES)
    for platform in PLATFORMS:
        module = importlib.import_module(f"custom_components.dynamic_car_charger.{platform}")
        added = []
        await module.async_setup_entry(
            hass, entry, lambda entities, added=added: added.extend(entities)
        )
        keys = {entity.translation_key for entity in added}
        assert keys == EXPECTED_ENTITIES[platform], platform
        assert {entity.unique_id for entity in added} == {f"test_{key}" for key in keys}
        for key in keys:
            assert strings["entity"][platform][key]["name"], (platform, key)


async def test_shutdown_does_not_remove_the_stop_listener_twice(rig, caplog):
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP

    hass, c, _ = rig
    c.store.async_load = AsyncMock(return_value={})
    await c.async_start()
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert c._stopping is True
    assert "Unable to remove unknown job listener" not in caplog.text


async def test_plan_shows_setup_and_prices_for_apps(rig):
    hass, c, _ = rig
    await c.async_reconcile()
    setup = c.data["setup"]
    assert setup["charger_entity"] == "switch.charger"
    assert setup["soc_entity"] == "sensor.battery"
    assert setup["connected_entity"] is None
    assert c.data["car_connected"] is None
    assert setup["power_kw"] == 10.0
    assert [row["price"] for row in c.data["prices"]] == [0.1, 0.3]
    assert {"start", "end", "price"} <= set(c.data["prices"][0])


async def test_plan_prices_are_empty_when_the_price_sensor_is_unusable(rig):
    hass, c, _ = rig
    hass.states.async_set("sensor.electricity_price", "unavailable")
    await c.async_reconcile()
    assert c.data["prices"] == []
    assert c.data["setup"]["price_entity"] == "sensor.electricity_price"


def _energy_mode(c):
    """Turn the rig into a set-up without a battery sensor."""
    c.settings.pop("soc_entity")
    c.energy_mode = True
    c.energy_goal = 5.0


async def test_energy_mode_charges_an_amount_without_a_battery_sensor(rig):
    hass, c, calls = rig
    _energy_mode(c)
    await c.async_change(enabled=True)
    await c.async_reconcile()
    assert c.data["mode"] == "energy"
    assert c.data["required_grid_kwh"] == pytest.approx(5.0)
    assert c.data["energy_goal_kwh"] == 5.0
    assert c.data["status"] == "charging"
    assert calls == ["turn_on"]

    c._delivered_kwh = 5.0
    await c.async_reconcile()
    assert c.data["status"] == "stopping_charge"
    await c.async_reconcile()
    assert c.data["status"] == "target_reached"
    assert calls[-1] == "turn_off"


async def test_energy_mode_counts_delivered_energy_from_the_power_sensor(rig):
    hass, c, _ = rig
    _energy_mode(c)
    hass.states.async_set("sensor.charging_power", "7200", {"unit_of_measurement": "W"})
    start = dt_util.utcnow()
    with patch(
        "custom_components.dynamic_car_charger.coordinator.dt_util.utcnow", return_value=start
    ):
        await c.async_change(enabled=True)
    with patch(
        "custom_components.dynamic_car_charger.coordinator.dt_util.utcnow",
        return_value=start + timedelta(seconds=30),
    ):
        await c.async_reconcile()
    # 7.2 kW for 30 seconds is 0.06 kWh.
    assert c.data["energy_delivered_kwh"] == pytest.approx(0.06, abs=0.001)


async def test_energy_mode_starts_a_new_charge(rig):
    hass, c, _ = rig
    _energy_mode(c)
    c._delivered_kwh = 3.0
    await c.async_new_charge()
    assert c._delivered_kwh == 0

    # A new deadline after the previous one passed is a new charge too.
    c._delivered_kwh = 3.0
    c.deadline = dt_util.utcnow() - timedelta(minutes=1)
    await c.async_change(deadline=dt_util.utcnow() + timedelta(hours=2))
    assert c._delivered_kwh == 0

    # Moving a deadline that hasn't passed keeps counting.
    c._delivered_kwh = 3.0
    await c.async_change(deadline=dt_util.utcnow() + timedelta(hours=3))
    assert c._delivered_kwh == 3.0


async def test_connected_binary_sensor_fires_event_and_starts_new_charge(rig):
    hass, c, _ = rig
    _energy_mode(c)
    c._delivered_kwh = 4.0
    c.settings["connected_entity"] = "binary_sensor.car_plug"
    received = []
    hass.bus.async_listen(EVENT_CAR_CONNECTED, received.append)
    c._changed(
        SimpleNamespace(
            data={
                "entity_id": "binary_sensor.car_plug",
                "old_state": State("binary_sensor.car_plug", "off"),
                "new_state": State("binary_sensor.car_plug", "on"),
            }
        )
    )
    await hass.async_block_till_done()
    assert len(received) == 1
    assert c._delivered_kwh == 0


async def test_connected_states_can_be_configured(rig):
    hass, c, _ = rig
    c.settings["connected_entity"] = "sensor.charger_state"
    c.settings["connected_states"] = "Connected, Charging"
    assert c._is_car_connected(State("sensor.charger_state", "charging"))
    assert c._is_car_connected(State("sensor.charger_state", "Connected"))
    assert not c._is_car_connected(State("sensor.charger_state", "Available"))


async def test_driving_binary_sensor_locks_the_charger(rig):
    hass, c, _ = rig
    c.settings["lock_entity"] = "lock.charger"
    c.settings["vehicle_state_entity"] = "binary_sensor.car_moving"
    hass.states.async_set("lock.charger", "unlocked")
    lock_calls = []

    async def lock(call):
        lock_calls.append(call.service)
        hass.states.async_set("lock.charger", "locked")

    hass.services.async_register("lock", "lock", lock)
    c._changed(
        SimpleNamespace(
            data={
                "entity_id": "binary_sensor.car_moving",
                "old_state": State("binary_sensor.car_moving", "off"),
                "new_state": State("binary_sensor.car_moving", "on"),
            }
        )
    )
    await hass.async_block_till_done()
    assert lock_calls == ["lock"]


async def test_prices_in_cents_and_other_currencies(rig):
    hass, c, _ = rig
    now = dt_util.utcnow()
    hass.states.async_set(
        "sensor.electricity_price",
        "10",
        {
            "unit_of_measurement": "öre/kWh",
            "currency": "SEK",
            "raw_today": [
                {
                    "start": (now - timedelta(minutes=1)).isoformat(),
                    "end": (now + timedelta(minutes=59)).isoformat(),
                    "value": 10,
                },
                {
                    "start": (now + timedelta(minutes=59)).isoformat(),
                    "end": (now + timedelta(minutes=119)).isoformat(),
                    "value": 30,
                },
            ],
        },
    )
    await c.async_reconcile()
    assert c.currency == "SEK"
    assert c.data["currency"] == "SEK"
    assert [row["price"] for row in c.data["prices"]] == [0.1, 0.3]


async def test_energy_mode_creates_energy_entities(rig):
    import importlib

    hass, c, _ = rig
    _energy_mode(c)
    entry = SimpleNamespace(entry_id="test", runtime_data=c)
    for platform, expected in (
        ("number", {"energy_goal", "price_threshold"}),
        ("button", {"tomorrow_0700", "tomorrow_0900", "day_after_tomorrow_0900", "new_charge"}),
    ):
        module = importlib.import_module(f"custom_components.dynamic_car_charger.{platform}")
        added = []
        await module.async_setup_entry(
            hass, entry, lambda entities, added=added: added.extend(entities)
        )
        assert {entity.translation_key for entity in added} == expected


def test_version_1_settings_become_general_fields():
    from custom_components.dynamic_car_charger.config_flow import migrate_settings

    old = {
        "charger_entity": "switch.charger",
        "status_entity": "sensor.charger_status",
        "vehicle_state_entity": "sensor.car_state",
    }
    new = migrate_settings(old)
    assert "status_entity" not in new
    assert new["connected_entity"] == "sensor.charger_status"
    assert new["connected_states"] == ["Locked, car connected"]
    assert new["driving_states"] == ["Driving"]
    # A binary sensor needs no states, and existing general settings stay.
    assert "driving_states" not in migrate_settings(
        {"vehicle_state_entity": "binary_sensor.moving"}
    )
    kept = migrate_settings({"status_entity": "sensor.a", "connected_entity": "binary_sensor.b"})
    assert kept == {"connected_entity": "binary_sensor.b"}


async def test_sensor_needs_its_states_but_binary_sensor_does_not(rig):
    hass, c, _ = rig
    hass.states.async_set("sensor.charger_status", "Charging")
    hass.states.async_set("binary_sensor.car_plug", "on")
    settings = dict(c.settings, connected_entity="sensor.charger_status")
    assert validate(hass, settings) == {"connected_states": "states_required"}
    settings["connected_states"] = "Charging"
    assert validate(hass, settings) == {}
    settings = dict(c.settings, connected_entity="binary_sensor.car_plug")
    assert validate(hass, settings) == {}


async def test_plan_reports_whether_a_car_is_connected(rig):
    hass, c, _ = rig
    c.settings["connected_entity"] = "sensor.charger_status"
    c.settings["connected_states"] = "Charging, Paused"
    hass.states.async_set("sensor.charger_status", "Paused")
    await c.async_reconcile()
    assert c.data["car_connected"] is True
    hass.states.async_set("sensor.charger_status", "Ready")
    await c.async_reconcile()
    assert c.data["car_connected"] is False
    # Without configured states a regular sensor never counts as connected.
    c.settings["connected_states"] = ""
    hass.states.async_set("sensor.charger_status", "Locked, car connected")
    await c.async_reconcile()
    assert c.data["car_connected"] is False
