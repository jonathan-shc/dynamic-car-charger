"""Plan and reconcile the charger against live Home Assistant state."""

import asyncio
import logging
from datetime import timedelta

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import DOMAIN, NAME
from .planner import make_plan, number, parse_prices, timestamp

_LOGGER = logging.getLogger(__name__)


class ChargerCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, name=NAME, config_entry=entry)
        self.entry = entry
        self.settings = {**entry.data, **entry.options}
        self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self.enabled = False
        self.target = 80.0
        self.deadline = None
        self._lock = asyncio.Lock()
        self._observed_soc = None
        self._credit_kwh = 0.0
        self._sample_time = None
        self._sample_power = 0.0
        self._command = None
        self._command_time = None
        self._boot = dt_util.utcnow()
        self._stopping = False
        self._pending_stop = False
        self._unsubs = []
        self.data = {"status": "set_deadline", "slots": []}

    async def async_start(self):
        saved = await self.store.async_load() or {}
        self.target = number(saved.get("target", 80), 0, 100)
        self.enabled = bool(saved.get("enabled", False))
        self._pending_stop = bool(saved.get("pending_stop", False))
        self.deadline = timestamp(saved["deadline"]) if saved.get("deadline") else None
        self._observed_soc = saved.get("observed_soc")
        self._credit_kwh = number(saved.get("credit_kwh", 0), 0)
        self._unsubs = [
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._shutdown),
            async_track_state_change_event(
                self.hass,
                [
                    self.settings[key]
                    for key in ("charger_entity", "soc_entity", "price_entity", "power_entity")
                ],
                self._changed,
            ),
            async_track_time_interval(self.hass, self._tick, timedelta(seconds=15)),
        ]
        await self.async_reconcile()

    @callback
    def _changed(self, event):
        if not self._stopping:
            self.hass.async_create_task(self.async_reconcile())

    async def _tick(self, now):
        await self.async_reconcile()

    def _save_data(self):
        return {
            "target": self.target,
            "enabled": self.enabled,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "observed_soc": self._observed_soc,
            "credit_kwh": self._credit_kwh,
            "pending_stop": self._pending_stop,
        }

    async def async_change_price_threshold(self, value):
        """Update the live price threshold from the Number entity."""
        async with self._lock:
            self.settings["max_price_eur_kwh"] = number(value, 0, 5)
            await self.store.async_save(self._save_data())
        await self.async_reconcile()

    async def async_change(self, **changes):
        async with self._lock:
            if changes.get("enabled") is False:
                self._pending_stop = True
            elif changes.get("enabled") is True:
                self._pending_stop = False
            for key, value in changes.items():
                setattr(self, key, value)
            await self.store.async_save(self._save_data())
        await self.async_reconcile(force_stop=changes.get("enabled") is False)

    def _state(self, key):
        state = self.hass.states.get(self.settings[key])
        if (
            state is None
            or state.state in ("unknown", "unavailable")
            or state.attributes.get("restored")
        ):
            raise ValueError(f"{key} is unavailable")
        return state

    def _read(self, now):
        soc_state = self._state("soc_entity")
        power_state = self._state("power_entity")
        price_state = self._state("price_entity")
        if soc_state.attributes.get("unit_of_measurement") != "%":
            raise ValueError("Battery sensor must report %")
        if price_state.attributes.get("unit_of_measurement") not in ("EUR/kWh", "€/kWh"):
            raise ValueError("Prices must be EUR/kWh")
        # A fresh HA report is necessary after restart. Cloud timestamps can still
        # be older: use a source that marks stale vehicle telemetry unavailable.
        for state, age, label in (
            (soc_state, self.settings["soc_max_age_minutes"] * 60, "Battery"),
            (power_state, 300, "Charging power"),
        ):
            report = state.last_reported
            if report < self._boot or (now - report).total_seconds() > age:
                raise ValueError(f"{label} needs a fresh report")
        soc = number(soc_state.state, 0, 100)
        power = number(power_state.state, 0, 50_000)
        unit = power_state.attributes.get("unit_of_measurement")
        if unit == "W":
            power /= 1000
        elif unit != "kW":
            raise ValueError("Power sensor must report W or kW")
        if power > 50:
            raise ValueError("Charging power out of range")
        if soc != self._observed_soc or not self.enabled:
            self._observed_soc, self._credit_kwh = soc, 0.0
        elif self._sample_time is not None:
            elapsed = (now - self._sample_time).total_seconds()
            if 0 <= elapsed <= 60:
                self._credit_kwh += (
                    self._sample_power * elapsed / 3600 * self.settings["efficiency"]
                )
        self._sample_time, self._sample_power = now, power
        effective_soc = min(100.0, soc + 100 * self._credit_kwh / self.settings["capacity_kwh"])
        prices = parse_prices(
            price_state.attributes,
            int(self.settings["interval_minutes"]),
            self.settings["price_adjustment"],
        )
        return soc, effective_soc, prices

    async def async_reconcile(self, force_stop=False):
        async with self._lock:
            if self._stopping:
                return
            now = dt_util.utcnow()
            desired = False
            data = {
                "status": "set_deadline",
                "slots": [],
                "enabled": self.enabled,
                "target_percentage": self.target,
                "deadline": self.deadline.isoformat() if self.deadline else None,
                "error": None,
                "estimated_cost_eur": None,
            }
            try:
                if self.deadline is not None:
                    soc, effective, prices = self._read(now)
                    threshold = self.settings.get("max_price_eur_kwh", 0.20)
                    threshold_plan = make_plan(
                        prices, now, self.deadline, effective, self.target,
                        self.settings["capacity_kwh"], self.settings["power_kw"],
                        self.settings["efficiency"], max_price=threshold)
                    full_plan = make_plan(
                        prices, now, self.deadline, effective, self.target,
                        self.settings["capacity_kwh"], self.settings["power_kw"],
                        self.settings["efficiency"])
                    safety_hours = max(1.0, threshold_plan.required_kwh / self.settings["power_kw"] * 1.5)
                    safety_mode = (self.deadline - now).total_seconds() / 3600 <= safety_hours
                    # Once prices are known continuously through the deadline,
                    # use the normal least-cost plan. The threshold only limits
                    # provisional planning while future prices are still unknown.
                    if full_plan.coverage_complete:
                        plan = full_plan
                    elif safety_mode:
                        plan = full_plan
                    else:
                        plan = threshold_plan
                    data["price_threshold_eur_kwh"] = threshold
                    data["threshold_safety_mode"] = safety_mode
                    data.update(plan.as_dict(self.settings["power_kw"]))
                    data["measured_soc"] = soc
                    data["estimated_soc"] = round(effective, 2)
                    desired = plan.charging_at(now) and now < self.deadline and soc < self.target
                    if soc >= self.target:
                        status = "target_reached"
                    elif now >= self.deadline:
                        status = "deadline_passed"
                    elif effective >= self.target:
                        status = "awaiting_soc_confirmation"
                    elif plan.shortfall_kwh > 0.001:
                        status = "insufficient_time" if plan.coverage_complete else "waiting_for_prices"
                    elif not plan.coverage_complete:
                        status = "provisional_plan"
                    else:
                        status = "charging" if desired else "scheduled"
                    data["status"] = status if self.enabled else "preview"
                    data["plan_status"] = status
            except (ValueError, TypeError, KeyError, OverflowError) as err:
                data.update(status="input_error", error=str(err))
                self._sample_time = None
                desired = False
            data["charging_requested"] = desired and self.enabled
            if self.enabled or force_stop or self._pending_stop:
                error = await self._control(desired and self.enabled, now, force_stop)
                if error == "waiting_for_car":
                    data.update(status="waiting_for_car", error=None, charging_requested=False)
                elif error:
                    data.update(status="control_error", error=error)
                elif not self.enabled:
                    self._pending_stop = False
            self.async_set_updated_data(data)
            self.store.async_delay_save(self._save_data, 30)

    async def _control(self, desired, now, force=False):
        entity_id = self.settings["charger_entity"]
        state = self.hass.states.get(entity_id)
        wanted = "on" if desired else "off"
        actual = state.state if state else "unavailable"
        # Reissue after the Wallbox cloud polling interval; a success response
        # is not proof of execution. An opposite command bypasses this cooldown.
        if actual == wanted and self._command in (None, desired):
            return None
        if (
            not force
            and self._command == desired
            and self._command_time
            and (now - self._command_time).total_seconds() < 120
        ):
            return f"Waiting for charger to confirm {wanted}" if actual != wanted else None
        if actual not in ("on", "off"):
            return "waiting_for_car" if desired else None
        self._command, self._command_time = desired, now
        try:
            async with asyncio.timeout(30):
                await self.hass.services.async_call(
                    "switch", f"turn_{wanted}", {"entity_id": entity_id}, blocking=True
                )
        except (HomeAssistantError, TimeoutError):
            return f"Charger did not accept {wanted}; will retry"
        return f"Waiting for charger to confirm {wanted}" if actual != wanted else None

    async def _shutdown(self, event):
        await self.async_stop()

    async def async_stop(self):
        async with self._lock:
            self._stopping = True
            for unsub in self._unsubs:
                unsub()
            self._unsubs.clear()
            if self.enabled:
                error = await self._control(False, dt_util.utcnow(), force=True)
                if error:
                    _LOGGER.warning("Integration stopped: %s. Check the charger", error)
            await self.store.async_save(self._save_data())
