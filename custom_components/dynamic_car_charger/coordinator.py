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

from .const import DOMAIN, EVENT_CAR_CONNECTED, NAME
from .planner import make_plan, number, parse_prices, timestamp

_LOGGER = logging.getLogger(__name__)


class ChargerCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, name=NAME, config_entry=entry)
        self.entry = entry
        self.settings = {**entry.data, **entry.options}
        self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self.enabled = False
        self.immediate_charging = False
        self.target = 80.0
        self.deadline = None
        self._lock = asyncio.Lock()
        self._observed_soc = None
        self._credit_kwh = 0.0
        self._sample_time = None
        self._sample_power = 0.0
        self._power_report_old = False
        self._command = None
        self._command_time = None
        self._command_attempt_time = None
        self._unlock_command_time = None
        self._unlock_attempt_time = None
        self._active_charge_until = None
        self._active_plan_context = None
        self._replan_stop_time = None
        self._boot = dt_util.utcnow()
        self._stopping = False
        self._pending_stop = False
        self._charger_available = False
        self._unsubs = []
        self.data = {"status": "set_deadline", "slots": []}

    async def async_start(self):
        saved = await self.store.async_load() or {}
        self.target = number(saved.get("target", 80), 0, 100)
        self.enabled = bool(saved.get("enabled", False))
        self.immediate_charging = bool(saved.get("immediate_charging", False))
        self._pending_stop = bool(saved.get("pending_stop", False))
        self.deadline = timestamp(saved["deadline"]) if saved.get("deadline") else None
        self._observed_soc = saved.get("observed_soc")
        self._credit_kwh = number(saved.get("credit_kwh", 0), 0)
        charger_state = self.hass.states.get(self.settings["charger_entity"])
        self._charger_available = self._is_available(charger_state)
        tracked_entities = [
            self.settings[key]
            for key in ("charger_entity", "soc_entity", "price_entity", "power_entity")
        ]
        tracked_entities.extend(
            self.settings[key]
            for key in ("status_entity", "lock_entity", "vehicle_state_entity")
            if self.settings.get(key)
        )
        self._unsubs = [
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._shutdown),
            async_track_state_change_event(
                self.hass,
                tracked_entities,
                self._changed,
            ),
            async_track_time_interval(self.hass, self._tick, timedelta(seconds=15)),
        ]
        vehicle_entity = self.settings.get("vehicle_state_entity")
        if vehicle_entity and self._state_text(
            self.hass.states.get(vehicle_entity)
        ) == "driving":
            await self._async_lock_charger()
        await self.async_reconcile()

    @callback
    def _changed(self, event):
        entity_id = event.data.get("entity_id")
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        status_entity = self.settings.get("status_entity")
        if entity_id == status_entity:
            if self._is_car_connected(new_state) and not self._is_car_connected(old_state):
                self._fire_car_connected(new_state)
        elif not status_entity and entity_id == self.settings["charger_entity"]:
            available = self._is_available(event.data.get("new_state"))
            if available and not self._charger_available:
                self._fire_car_connected(new_state)
            self._charger_available = available
        if (
            entity_id == self.settings.get("vehicle_state_entity")
            and self._state_text(new_state) == "driving"
            and self._state_text(old_state) != "driving"
        ):
            self.hass.async_create_task(self._async_lock_charger())
        if not self._stopping:
            self.hass.async_create_task(self.async_reconcile())

    def _fire_car_connected(self, state):
        self.hass.bus.async_fire(
            EVENT_CAR_CONNECTED,
            {
                "config_entry_id": self.entry.entry_id,
                "charger_entity": self.settings["charger_entity"],
                "status_entity": self.settings.get("status_entity"),
                "status": state.state if state is not None else None,
            },
        )

    @staticmethod
    def _state_text(state):
        return state.state.strip().casefold() if state is not None else ""

    @classmethod
    def _is_car_connected(cls, state):
        return cls._state_text(state) == "locked, car connected"

    @staticmethod
    def _is_available(state):
        return state is not None and state.state not in ("unknown", "unavailable")

    async def _tick(self, now):
        await self.async_reconcile()

    def _save_data(self):
        return {
            "target": self.target,
            "enabled": self.enabled,
            "immediate_charging": self.immediate_charging,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "observed_soc": self._observed_soc,
            "credit_kwh": self._credit_kwh,
            "pending_stop": self._pending_stop,
        }

    async def async_set_deadline_preset(self, days, hour):
        """Set the deadline to a local-time preset calculated by the integration."""
        current = dt_util.now()
        deadline = (current.replace(hour=hour, minute=0, second=0, microsecond=0)
                    + timedelta(days=days))
        await self.async_change(deadline=deadline)

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
            if changes.get("immediate_charging") is False and not self.enabled:
                self._pending_stop = True
            elif changes.get("immediate_charging") is True:
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

    def _read(self, now, *, include_prices=True):
        soc_state = self._state("soc_entity")
        power_state = self._state("power_entity")
        if soc_state.attributes.get("unit_of_measurement") != "%":
            raise ValueError("Battery sensor must report %")
        soc = number(soc_state.state, 0, 100)
        power = number(power_state.state, 0, 50_000)
        unit = power_state.attributes.get("unit_of_measurement")
        if unit == "W":
            power /= 1000
        elif unit != "kW":
            raise ValueError("Power sensor must report W or kW")
        if power > 50:
            raise ValueError("Charging power out of range")
        power_report = power_state.last_reported
        self._power_report_old = (
            power_report < self._boot
            or (now - power_report).total_seconds() > 300
        )
        if self._power_report_old:
            # Many car and charger integrations stop publishing power after a
            # session finishes. Never integrate that stale reading, but do not
            # turn a normal idle state into an input error either.
            power = 0.0
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
        prices = []
        if include_prices:
            price_state = self._state("price_entity")
            if price_state.attributes.get("unit_of_measurement") not in ("EUR/kWh", "€/kWh"):
                raise ValueError("Prices must be EUR/kWh")
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
                "immediate_charging": self.immediate_charging,
                "target_percentage": self.target,
                "deadline": self.deadline.isoformat() if self.deadline else None,
                "error": None,
                "estimated_cost_eur": None,
            }
            try:
                if self.immediate_charging:
                    soc, effective, _prices = self._read(now, include_prices=False)
                    battery_report = self.hass.states.get(
                        self.settings["soc_entity"]
                    ).last_reported
                    age_minutes = max(
                        0, (now - battery_report).total_seconds() / 60
                    )
                    data["soc_report_age_minutes"] = round(age_minutes, 1)
                    data["soc_report_old"] = (
                        age_minutes > self.settings["soc_max_age_minutes"]
                    )
                    data["measured_soc"] = soc
                    data["estimated_soc"] = round(effective, 2)
                    data["charging_power_report_old"] = self._power_report_old
                    data["plan_status"] = "immediate_charging"
                    data["plan_is_provisional"] = False
                    if soc >= self.target:
                        self.immediate_charging = False
                        if not self.enabled:
                            self._pending_stop = True
                        data["immediate_charging"] = False
                        data["status"] = "target_reached"
                        data["plan_status"] = "target_reached"
                    else:
                        desired = True
                        data["status"] = "charging"
                elif self.deadline is not None:
                    deadline_grace_minutes = number(
                        self.settings.get("deadline_grace_minutes", 60), 0, 720
                    )
                    deadline_extension_until = self.deadline + timedelta(
                        minutes=deadline_grace_minutes
                    )
                    after_deadline = now >= self.deadline
                    soc, effective, prices = self._read(
                        now, include_prices=not after_deadline
                    )
                    price_signature = tuple(
                        (slot.start, slot.end, slot.price) for slot in prices
                    )
                    threshold = self.settings.get("max_price_eur_kwh", 0.20)
                    plan_context = (
                        price_signature,
                        self.deadline,
                        self.target,
                        threshold,
                    )
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
                    battery_report = self.hass.states.get(self.settings["soc_entity"]).last_reported
                    age_minutes = max(0, (now - battery_report).total_seconds() / 60)
                    data["soc_report_age_minutes"] = round(age_minutes, 1)
                    data["soc_report_old"] = age_minutes > self.settings["soc_max_age_minutes"]
                    data["measured_soc"] = soc
                    data["estimated_soc"] = round(effective, 2)
                    data["charging_power_report_old"] = self._power_report_old
                    data["deadline_grace_minutes"] = deadline_grace_minutes
                    data["deadline_extension_until"] = (
                        deadline_extension_until.isoformat()
                        if deadline_grace_minutes > 0
                        else None
                    )
                    planned_now = plan.charging_at(now)
                    awaiting_soc_confirmation = (
                        soc < self.target
                        and effective >= self.target
                        and self._credit_kwh > 0
                        and self._active_charge_until is not None
                    )
                    if awaiting_soc_confirmation:
                        # The energy estimate must never end an active session
                        # before the car itself reports the requested SOC. Keep
                        # a short rolling window alive until telemetry confirms
                        # the target; the vehicle BMS remains the final limit.
                        planned_now = True
                        self._active_charge_until = now + timedelta(minutes=5)
                    elif planned_now:
                        continuous_end = None
                        for slot in plan.slots:
                            if slot.start <= now < slot.end:
                                continuous_end = slot.end
                            elif continuous_end is not None and slot.start <= continuous_end:
                                continuous_end = max(continuous_end, slot.end)
                            elif continuous_end is not None:
                                break
                        if continuous_end is not None:
                            self._active_charge_until = continuous_end
                            self._active_plan_context = plan_context
                            self._replan_stop_time = None
                    elif (
                        self._active_charge_until is not None
                        and now < self._active_charge_until
                        and plan_context == self._active_plan_context
                    ):
                        # Keep an already-started continuous run stable. A new
                        # SOC report or replanning must not create an off/on
                        # cycle at an hourly price boundary.
                        planned_now = True
                    elif self._active_charge_until is not None and now < self._active_charge_until:
                        # Price/deadline updates can arrive in several HA state
                        # changes. Give the new plan one short settling window so
                        # an adjacent block is not interrupted by an intermediate
                        # calculation. A real changed plan still stops afterwards.
                        if self._replan_stop_time is None:
                            self._replan_stop_time = now
                        if (now - self._replan_stop_time).total_seconds() < 30:
                            planned_now = True
                        else:
                            self._active_charge_until = None
                            self._active_plan_context = None
                            self._replan_stop_time = None
                    else:
                        self._active_charge_until = None
                        self._active_plan_context = None
                        self._replan_stop_time = None

                    charger_state = self.hass.states.get(self.settings["charger_entity"])
                    active_session = (
                        (charger_state is not None and charger_state.state == "on")
                        or self._command is True
                    )
                    vehicle_entity = self.settings.get("vehicle_state_entity")
                    vehicle_is_driving = bool(
                        vehicle_entity
                        and self._state_text(self.hass.states.get(vehicle_entity)) == "driving"
                    )
                    deadline_extension_active = (
                        after_deadline
                        and deadline_grace_minutes > 0
                        and now < deadline_extension_until
                        and soc < self.target
                        and active_session
                        and not vehicle_is_driving
                    )
                    desired = (
                        (planned_now and not after_deadline)
                        or deadline_extension_active
                    ) and soc < self.target
                    data["deadline_extension_active"] = deadline_extension_active
                    if deadline_extension_active:
                        self._active_charge_until = deadline_extension_until
                    data["active_charge_until"] = (
                        self._active_charge_until.isoformat()
                        if self._active_charge_until is not None
                        else None
                    )
                    if soc >= self.target:
                        self._active_charge_until = None
                        self._active_plan_context = None
                        self._replan_stop_time = None
                        status = "target_reached"
                    elif deadline_extension_active:
                        status = "charging_overtime"
                    elif after_deadline:
                        self._active_charge_until = None
                        self._active_plan_context = None
                        self._replan_stop_time = None
                        status = "deadline_passed"
                    elif effective >= self.target:
                        status = "awaiting_soc_confirmation"
                    elif desired:
                        # The car is actively being asked to charge.  The
                        # underlying price plan may still be provisional, but
                        # that should not hide the current charging state.
                        status = "charging"
                    elif plan.shortfall_kwh > 0.001:
                        status = "insufficient_time" if plan.coverage_complete else "waiting_for_prices"
                    elif not plan.coverage_complete:
                        status = "provisional_plan"
                    else:
                        status = "charging" if desired else "scheduled"
                    data["plan_is_provisional"] = not plan.coverage_complete
                    data["status"] = (
                        status
                        if self.enabled or status == "target_reached"
                        else "preview"
                    )
                    data["plan_status"] = status
            except (ValueError, TypeError, KeyError, OverflowError) as err:
                data.update(status="input_error", error=str(err))
                self._sample_time = None
                desired = False
            control_enabled = self.enabled or self.immediate_charging
            data["charging_requested"] = desired and control_enabled
            if control_enabled or force_stop or self._pending_stop:
                error = await self._control(desired and control_enabled, now, force_stop)
                if error == "waiting_for_car":
                    data.update(status="waiting_for_car", error=None, charging_requested=False)
                elif error in ("unlocking_charger", "starting_charge", "stopping_charge"):
                    data.update(status=error, error=None)
                elif error:
                    data.update(status="control_error", error=error)
                elif not self.enabled:
                    self._pending_stop = False
            self.async_set_updated_data(data)
            self.store.async_delay_save(self._save_data, 30)

    async def _control(self, desired, now, force=False):
        if desired:
            unlock_status = await self._ensure_unlocked(now)
            if unlock_status is not None:
                return unlock_status
        entity_id = self.settings["charger_entity"]
        state = self.hass.states.get(entity_id)
        wanted = "on" if desired else "off"
        actual = state.state if state else "unavailable"
        # The car and charger may need time to wake up and complete their
        # handshake. Keep that normal transition separate from a real failure.
        if actual == wanted:
            self._command = None
            self._command_time = None
            self._command_attempt_time = None
            return None
        if actual not in ("on", "off"):
            return "waiting_for_car" if desired else None

        if self._command != desired:
            self._command = desired
            self._command_time = now
            self._command_attempt_time = None

        elapsed = (now - self._command_time).total_seconds()
        if not force and elapsed >= 300:
            return f"Charger did not confirm {wanted} within 5 minutes"

        should_send = (
            force
            or self._command_attempt_time is None
            or (now - self._command_attempt_time).total_seconds() >= 120
        )
        if not should_send:
            return "starting_charge" if desired else "stopping_charge"

        self._command_attempt_time = now
        try:
            async with asyncio.timeout(30):
                await self.hass.services.async_call(
                    "switch", f"turn_{wanted}", {"entity_id": entity_id}, blocking=True
                )
        except (HomeAssistantError, TimeoutError):
            return f"Charger did not accept {wanted}; will retry"
        return "starting_charge" if desired else "stopping_charge"

    async def _ensure_unlocked(self, now):
        """Unlock the Wallbox before the first resume/start request."""
        entity_id = self.settings.get("lock_entity")
        if not entity_id:
            return None
        vehicle_entity = self.settings.get("vehicle_state_entity")
        if vehicle_entity and self._state_text(
            self.hass.states.get(vehicle_entity)
        ) == "driving":
            return "waiting_for_car"
        state = self.hass.states.get(entity_id)
        actual = state.state if state is not None else "unavailable"
        if actual == "unlocked":
            self._unlock_command_time = None
            self._unlock_attempt_time = None
            return None
        if actual in ("unknown", "unavailable"):
            return "waiting_for_car"
        if self._unlock_command_time is None:
            self._unlock_command_time = now
            self._unlock_attempt_time = None
        if (now - self._unlock_command_time).total_seconds() >= 300:
            return "Charger lock did not confirm unlocked within 5 minutes"
        should_send = (
            self._unlock_attempt_time is None
            or (now - self._unlock_attempt_time).total_seconds() >= 120
        )
        if should_send:
            self._unlock_attempt_time = now
            try:
                async with asyncio.timeout(30):
                    await self.hass.services.async_call(
                        "lock", "unlock", {"entity_id": entity_id}, blocking=True
                    )
            except (HomeAssistantError, TimeoutError):
                return "Charger did not accept unlock; will retry"
        return "unlocking_charger"

    async def _async_lock_charger(self):
        """Lock the Wallbox when the vehicle reports that it is driving."""
        entity_id = self.settings.get("lock_entity")
        if not entity_id:
            return
        state = self.hass.states.get(entity_id)
        if state is not None and state.state == "locked":
            return
        try:
            async with asyncio.timeout(30):
                await self.hass.services.async_call(
                    "lock", "lock", {"entity_id": entity_id}, blocking=True
                )
            self._unlock_command_time = None
            self._unlock_attempt_time = None
        except (HomeAssistantError, TimeoutError):
            _LOGGER.warning("Wallbox could not be locked after the car disconnected")

    async def _shutdown(self, event):
        await self.async_stop()

    async def async_stop(self):
        async with self._lock:
            self._stopping = True
            for unsub in self._unsubs:
                unsub()
            self._unsubs.clear()
            if self.enabled or self.immediate_charging:
                error = await self._control(False, dt_util.utcnow(), force=True)
                if error:
                    _LOGGER.warning("Integration stopped: %s. Check the charger", error)
            await self.store.async_save(self._save_data())
