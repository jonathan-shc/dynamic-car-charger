"""Plan and reconcile the charger against live Home Assistant state."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import DOMAIN, EVENT_CAR_CONNECTED, NAME
from .forecaster import ForecastUnavailable, PriceForecaster
from .planner import Plan, Slot, make_plan, number, parse_prices, price_unit, timestamp

_LOGGER = logging.getLogger(__name__)

TICK = timedelta(seconds=15)
# A command that is not confirmed within this time is reported as an error,
# but it is still retried at the normal retry interval.
CONFIRM_TIMEOUT = timedelta(minutes=5)
RETRY_INTERVAL = timedelta(seconds=120)
REPLAN_SETTLE = timedelta(seconds=30)
# Maximum charging after the power estimate reaches the target while the car
# has not yet reported the target percentage.
SOC_CONFIRMATION_LIMIT = timedelta(minutes=30)
# An input or control problem that lasts this long raises a repair issue.
REPAIR_DELAY = timedelta(minutes=30)
# How often the price forecast checks for new prices and weather forecasts.
FORECAST_INTERVAL = timedelta(minutes=15)
SESSION_END_STATUSES = ("set_deadline", "target_reached", "deadline_passed")
PENDING_STATUSES = ("unlocking_charger", "starting_charge", "stopping_charge")


class ChargerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Own the charging session for one charger."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=NAME, config_entry=entry)
        self.entry = entry
        self.settings: dict[str, Any] = {**entry.data, **entry.options}
        # The configured threshold, before any live change from the Number entity.
        self._configured_max_price = self.settings.get("max_price_eur_kwh")
        self.store: Store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self.enabled = False
        self.immediate_charging = False
        self.target = 80.0
        # Without a battery sensor the integration charges an amount of energy
        # instead of up to a battery percentage.
        self.energy_mode = not self.settings.get("soc_entity")
        self.energy_goal = 20.0
        self._delivered_kwh = 0.0
        self.currency = "EUR"
        self.deadline: datetime | None = None
        self.session: dict[str, Any] | None = None
        self.use_forecast = False
        self.forecaster = PriceForecaster(hass, bidding_zone=self.settings.get("bidding_zone"))
        self.forecast_calibration = None
        self._forecast_unsub = None
        self._lock = asyncio.Lock()
        self._observed_soc: float | None = None
        self._credit_kwh = 0.0
        self._interval_kwh = 0.0
        self._sample_time: datetime | None = None
        self._sample_power = 0.0
        self._power_report_old = False
        self._command: bool | None = None
        self._command_time: datetime | None = None
        self._command_attempt_time: datetime | None = None
        self._unlock_command_time: datetime | None = None
        self._unlock_attempt_time: datetime | None = None
        # Whether the plan asked for charging at the last check, and whether the
        # charger still has to be locked because the plan stopped charging.
        self._charge_requested = False
        self._lock_pending = False
        self._lock_attempt_time: datetime | None = None
        self._stopped_by_user = False
        self._active_charge_until: datetime | None = None
        self._active_plan_context: tuple | None = None
        self._replan_stop_time: datetime | None = None
        self._soc_confirmation_since: datetime | None = None
        self._problem: str | None = None
        self._problem_since: datetime | None = None
        self._issue_id: str | None = None
        self._boot = dt_util.utcnow()
        self._stopping = False
        self._pending_stop = False
        self._charger_available = False
        self._unsubs: list = []
        self._stop_unsub = None
        self.data = {"status": "set_deadline", "slots": []}

    async def async_start(self) -> None:
        saved = await self.store.async_load() or {}
        self.target = number(saved.get("target", 80), 0, 100)
        self.energy_goal = number(saved.get("energy_goal", 20), 0, 200)
        self._delivered_kwh = number(saved.get("delivered_kwh", 0), 0)
        self.enabled = bool(saved.get("enabled", False))
        self.immediate_charging = bool(saved.get("immediate_charging", False))
        self._pending_stop = bool(saved.get("pending_stop", False))
        self._charge_requested = bool(saved.get("charge_requested", False))
        self._lock_pending = bool(saved.get("lock_pending", False))
        self.deadline = timestamp(saved["deadline"]) if saved.get("deadline") else None
        self._observed_soc = saved.get("observed_soc")
        self._credit_kwh = number(saved.get("credit_kwh", 0), 0)
        self.session = saved.get("session")
        self.use_forecast = bool(saved.get("use_forecast", False))
        # Keep a live threshold change across restarts, unless the configured
        # threshold was changed in the options since it was saved.
        if (
            saved.get("max_price_eur_kwh") is not None
            and saved.get("configured_max_price_eur_kwh") == self._configured_max_price
        ):
            self.settings["max_price_eur_kwh"] = number(saved["max_price_eur_kwh"], 0, 5)
        charger_state = self.hass.states.get(self.settings["charger_entity"])
        self._charger_available = self._is_available(charger_state)
        tracked_entities = [
            self.settings[key]
            for key in ("charger_entity", "soc_entity", "price_entity", "power_entity")
            if self.settings.get(key)
        ]
        tracked_entities.extend(
            self.settings[key]
            for key in ("connected_entity", "lock_entity")
            if self.settings.get(key)
        )
        self._stop_unsub = self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._shutdown)
        self._unsubs = [
            async_track_state_change_event(self.hass, tracked_entities, self._changed),
            async_track_time_interval(self.hass, self._tick, TICK),
        ]
        self._set_forecast_tracking()
        await self.async_reconcile()

    @callback
    def _changed(self, event: Event) -> None:
        entity_id = event.data.get("entity_id")
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        connection_entity = self.settings.get("connected_entity")
        if entity_id == connection_entity:
            if self._is_car_connected(new_state) and not self._is_car_connected(old_state):
                self._fire_car_connected(new_state)
        elif not connection_entity and entity_id == self.settings["charger_entity"]:
            # Without a connection sensor, a charger switch that becomes
            # available is the best sign that a car was plugged in.
            available = self._is_available(new_state)
            if available and not self._charger_available:
                self._fire_car_connected(new_state)
            self._charger_available = available
        if not self._stopping:
            self.hass.async_create_task(self.async_reconcile())

    def _fire_car_connected(self, state: State | None) -> None:
        if self.energy_mode:
            # A newly connected car starts a new amount of energy to charge.
            self._delivered_kwh = 0.0
        self.hass.bus.async_fire(
            EVENT_CAR_CONNECTED,
            {
                "config_entry_id": self.entry.entry_id,
                "charger_entity": self.settings["charger_entity"],
                "connected_entity": self.settings.get("connected_entity"),
                "status": state.state if state is not None else None,
            },
        )

    @staticmethod
    def _state_text(state: State | None) -> str:
        return state.state.strip().casefold() if state is not None else ""

    @staticmethod
    def _state_list(value: str | list[str] | None) -> set[str]:
        """States from the setup: a list, or (0.8) a comma-separated text."""
        parts = value if isinstance(value, list) else (value or "").split(",")
        return {str(part).strip().casefold() for part in parts if str(part).strip()}

    def _is_car_connected(self, state: State | None) -> bool:
        """Whether this state of the connected sensor means a car is plugged in.

        A binary sensor is connected when on; other sensors use the configured states.
        """
        if state is None:
            return False
        states = self._state_list(self.settings.get("connected_states"))
        if not states and state.domain == "binary_sensor":
            states = {"on"}
        return self._state_text(state) in states

    def _car_connected(self) -> bool | None:
        """Whether a car is plugged in, or None without a connected sensor."""
        entity_id = self.settings.get("connected_entity")
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if not self._is_available(state):
            return None
        return self._is_car_connected(state)

    @staticmethod
    def _is_available(state: State | None) -> bool:
        return state is not None and state.state not in ("unknown", "unavailable")

    async def _tick(self, now: datetime) -> None:
        await self.async_reconcile()

    def _save_data(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "enabled": self.enabled,
            "immediate_charging": self.immediate_charging,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "observed_soc": self._observed_soc,
            "credit_kwh": self._credit_kwh,
            "pending_stop": self._pending_stop,
            "charge_requested": self._charge_requested,
            "lock_pending": self._lock_pending,
            "max_price_eur_kwh": self.settings.get("max_price_eur_kwh"),
            "configured_max_price_eur_kwh": self._configured_max_price,
            "session": self.session,
            "use_forecast": self.use_forecast,
            "energy_goal": self.energy_goal,
            "delivered_kwh": self._delivered_kwh,
        }

    async def async_set_deadline_preset(self, days: int, hour: int) -> None:
        """Set the deadline to a local-time preset calculated by the integration."""
        current = dt_util.now()
        deadline = current.replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(
            days=days
        )
        await self.async_change(deadline=deadline)

    async def async_change_price_threshold(self, value: float) -> None:
        """Update the live price threshold from the Number entity."""
        async with self._lock:
            self.settings["max_price_eur_kwh"] = number(value, 0, 5)
            await self.store.async_save(self._save_data())
        await self.async_reconcile()

    async def async_new_charge(self) -> None:
        """Energy mode: start counting the energy to charge from zero again."""
        async with self._lock:
            self._delivered_kwh = 0.0
            await self.store.async_save(self._save_data())
        await self.async_reconcile()

    async def async_change(self, **changes: Any) -> None:
        async with self._lock:
            if (
                self.energy_mode
                and "deadline" in changes
                and (self.deadline is None or self.deadline <= dt_util.utcnow())
            ):
                # A new deadline after the previous one passed is a new charge.
                self._delivered_kwh = 0.0
            if changes.get("enabled") is False or changes.get("immediate_charging") is False:
                # Switched off by hand: the plan didn't stop, so leave the lock alone.
                self._stopped_by_user = True
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
            if "use_forecast" in changes:
                self._set_forecast_tracking()
            await self.store.async_save(self._save_data())
        await self.async_reconcile(force_stop=changes.get("enabled") is False)

    @callback
    def _set_forecast_tracking(self) -> None:
        """Only fetch prices and weather while the price forecast is used."""
        if self.use_forecast and self._forecast_unsub is None and not self._stopping:
            self._forecast_unsub = async_track_time_interval(
                self.hass, self._forecast_tick, FORECAST_INTERVAL
            )
            self.hass.async_create_background_task(
                self._forecast_tick(), f"{DOMAIN} price forecast"
            )
        elif not self.use_forecast and self._forecast_unsub is not None:
            self._forecast_unsub()
            self._forecast_unsub = None
            self.forecaster.status = "off"

    async def _forecast_tick(self, now: datetime | None = None) -> None:
        await self.forecaster.async_update()
        if not self._stopping:
            await self.async_reconcile()

    def _state(self, key: str) -> State:
        state = self.hass.states.get(self.settings[key])
        if (
            state is None
            or state.state in ("unknown", "unavailable")
            or state.attributes.get("restored")
        ):
            raise ValueError(f"{key} is unavailable")
        return state

    def _prices(self) -> list[Slot]:
        price_state = self._state("price_entity")
        scale, self.currency = price_unit(price_state.attributes)
        return parse_prices(
            price_state.attributes,
            int(float(self.settings["interval_minutes"])),
            self.settings["price_adjustment"],
            scale,
        )

    def _setup_details(self) -> dict[str, Any]:
        """The entities and car details in use, so dashboards and apps can set themselves up."""
        keys = (
            "charger_entity",
            "soc_entity",
            "price_entity",
            "power_entity",
            "connected_entity",
            "lock_entity",
        )
        details: dict[str, Any] = {key: self.settings.get(key) for key in keys}
        details["mode"] = "energy" if self.energy_mode else "battery"
        details["currency"] = self.currency
        details["power_kw"] = self.settings["power_kw"]
        details["capacity_kwh"] = self.settings["capacity_kwh"]
        return details

    def _price_rows(self, now: datetime) -> list[dict[str, Any]]:
        """Today's and later prices as the planner reads them, adjustment included.

        Price sensors use different attribute layouts; this gives one layout.
        """
        try:
            slots = self._prices()
        except (ValueError, TypeError, KeyError, AttributeError):
            return []
        today = dt_util.start_of_local_day(dt_util.as_local(now))
        return [
            {"start": s.start.isoformat(), "end": s.end.isoformat(), "price": round(s.price, 6)}
            for s in slots
            if s.end > today
        ]

    def _goal_target(self) -> float:
        """The target in percent: of the battery, or of the energy to charge."""
        return 100.0 if self.energy_mode else self.target

    def _capacity(self) -> float:
        """Energy for 100%: the battery, or the energy to charge."""
        return max(self.energy_goal, 0.1) if self.energy_mode else self.settings["capacity_kwh"]

    def _efficiency(self) -> float:
        """Energy mode counts grid energy, so no losses apply."""
        return 1.0 if self.energy_mode else self.settings["efficiency"]

    def _read(self, now: datetime, *, include_prices: bool = True):
        if self.energy_mode:
            soc = None
        else:
            soc_state = self._state("soc_entity")
            if soc_state.attributes.get("unit_of_measurement") != "%":
                raise ValueError("Battery sensor must report %")
            soc = number(soc_state.state, 0, 100)
        power_state = self._state("power_entity")
        power = number(power_state.state, 0, 50_000)
        unit = power_state.attributes.get("unit_of_measurement")
        if unit == "W":
            power /= 1000
        elif unit != "kW":
            raise ValueError("Power sensor must report W or kW")
        if power > 50:
            raise ValueError("Charging power out of range")
        power_report = power_state.last_reported
        self._power_report_old = power_report < self._boot or now - power_report > timedelta(
            minutes=5
        )
        if self._power_report_old:
            # Many car and charger integrations stop publishing power after a
            # session finishes. Never integrate that stale reading, but do not
            # turn a normal idle state into an input error either.
            power = 0.0
        # Grid energy delivered since the previous sample.
        self._interval_kwh = 0.0
        if self._sample_time is not None:
            elapsed = (now - self._sample_time).total_seconds()
            if 0 <= elapsed <= 60:
                self._interval_kwh = self._sample_power * elapsed / 3600
        self._sample_time, self._sample_power = now, power
        if self.energy_mode:
            # Progress is the measured energy as a share of the energy to charge.
            self._delivered_kwh += self._interval_kwh
            soc = min(100.0, 100 * self._delivered_kwh / self._capacity())
            prices = self._prices() if include_prices else []
            return soc, soc, prices
        if soc != self._observed_soc or not self.enabled:
            self._observed_soc, self._credit_kwh = soc, 0.0
        else:
            self._credit_kwh += self._interval_kwh * self.settings["efficiency"]
        effective_soc = min(100.0, soc + 100 * self._credit_kwh / self.settings["capacity_kwh"])
        prices = self._prices() if include_prices else []
        return soc, effective_soc, prices

    def _battery_details(self, now: datetime, soc: float, effective: float) -> dict[str, Any]:
        if self.energy_mode:
            return {
                "measured_soc": round(soc, 1),
                "estimated_soc": round(effective, 2),
                "energy_goal_kwh": self.energy_goal,
                "energy_delivered_kwh": round(self._delivered_kwh, 3),
                "charging_power_report_old": self._power_report_old,
            }
        reported = self.hass.states.get(self.settings["soc_entity"]).last_reported
        age = now - reported
        # The age is informational only: a battery percentage that does not
        # change is normal while parked and must not stop the plan.
        return {
            "soc_reported_at": reported.isoformat(),
            "soc_report_old": age > timedelta(minutes=self.settings["soc_max_age_minutes"]),
            "measured_soc": soc,
            "estimated_soc": round(effective, 2),
            "charging_power_report_old": self._power_report_old,
        }

    def _clear_active_run(self) -> None:
        self._active_charge_until = None
        self._active_plan_context = None
        self._replan_stop_time = None
        self._soc_confirmation_since = None

    async def async_reconcile(self, force_stop: bool = False) -> None:
        async with self._lock:
            if self._stopping:
                return
            now = dt_util.utcnow()
            desired = False
            prices: list[Slot] = []
            self._interval_kwh = 0.0
            data: dict[str, Any] = {
                "status": "set_deadline",
                "slots": [],
                "enabled": self.enabled,
                "immediate_charging": self.immediate_charging,
                "target_percentage": self._goal_target(),
                "mode": "energy" if self.energy_mode else "battery",
                "currency": self.currency,
                "deadline": self.deadline.isoformat() if self.deadline else None,
                "error": None,
                "estimated_cost_eur": None,
                "setup": self._setup_details(),
                # So dashboards and apps don't need to know charger or car wording.
                "car_connected": self._car_connected(),
                "prices": self._price_rows(now),
            }
            try:
                if self.immediate_charging:
                    desired = self._reconcile_immediate(now, data)
                elif self.deadline is not None:
                    desired, prices = self._reconcile_deadline(now, data)
            except (ValueError, TypeError, KeyError, OverflowError) as err:
                data.update(status="input_error", error=str(err))
                self._sample_time = None
                desired = False
            plan_status = data.get("plan_status", data["status"])
            control_enabled = self.enabled or self.immediate_charging
            data["charging_requested"] = desired and control_enabled
            if control_enabled or force_stop or self._pending_stop:
                error = await self._control(desired and control_enabled, now, force_stop)
                if error == "waiting_for_car":
                    data.update(status="waiting_for_car", error=None, charging_requested=False)
                elif error in PENDING_STATUSES:
                    data.update(status=error, error=None)
                elif error:
                    data.update(status="control_error", error=error)
                elif not self.enabled:
                    self._pending_stop = False
            # What the plan wants, also while waiting for the car or the charger.
            await self._update_lock(now, desired and control_enabled)
            self._update_session(
                now, prices, data["charging_requested"], control_enabled, plan_status
            )
            self._update_repair_issue(now, data, control_enabled)
            # The currency is only known once the price sensor has been read.
            data["currency"] = data["setup"]["currency"] = self.currency
            self.async_set_updated_data(data)
            self.store.async_delay_save(self._save_data, 30)

    def _reconcile_immediate(self, now: datetime, data: dict[str, Any]) -> bool:
        soc, effective, _prices = self._read(now, include_prices=False)
        data.update(self._battery_details(now, soc, effective))
        data["plan_status"] = "immediate_charging"
        data["plan_is_provisional"] = False
        if soc >= self._goal_target():
            self.immediate_charging = False
            if not self.enabled:
                self._pending_stop = True
            data["immediate_charging"] = False
            data["status"] = data["plan_status"] = "target_reached"
            return False
        data["status"] = "charging"
        return True

    def _choose_plan(self, prices, now, effective, data) -> tuple[Plan, bool]:
        """Return the plan to follow and whether published prices reach the deadline."""
        threshold = self.settings.get("max_price_eur_kwh", 0.20)
        args = (
            now,
            self.deadline,
            effective,
            self._goal_target(),
            self._capacity(),
            self.settings["power_kw"],
            self._efficiency(),
        )
        threshold_plan = make_plan(prices, *args, max_price=threshold)
        full_plan = make_plan(prices, *args)
        safety_hours = max(1.0, threshold_plan.required_kwh / self.settings["power_kw"] * 1.5)
        safety_mode = (self.deadline - now).total_seconds() / 3600 <= safety_hours
        data["price_threshold_eur_kwh"] = threshold
        data["threshold_safety_mode"] = safety_mode
        data["forecast_status"] = self.forecaster.status if self.use_forecast else "off"
        data["forecast_error"] = None
        # Once prices are known continuously through the deadline, use the
        # normal least-cost plan. Near the deadline, use any published price.
        if full_plan.coverage_complete or safety_mode:
            data["planning_method"] = "published_prices"
            return full_plan, full_plan.coverage_complete
        if self.use_forecast:
            # Plan over published and estimated prices together. Estimated
            # hours never start charging; they only show whether waiting for
            # unpublished prices is likely to be cheaper.
            try:
                estimated, self.forecast_calibration = self.forecaster.estimate(
                    prices, self.deadline, self.currency
                )
            except ForecastUnavailable as err:
                data["forecast_error"] = str(err)
            else:
                data["planning_method"] = "forecast"
                return make_plan(prices + estimated, *args), False
        # The threshold limits provisional planning while future prices are
        # unknown, and is the fallback when the forecast is unavailable.
        data["planning_method"] = "threshold"
        return threshold_plan, False

    def _reconcile_deadline(self, now: datetime, data: dict[str, Any]):
        grace_minutes = number(self.settings.get("deadline_grace_minutes", 60), 0, 720)
        extension_until = self.deadline + timedelta(minutes=grace_minutes)
        after_deadline = now >= self.deadline
        soc, effective, prices = self._read(now, include_prices=not after_deadline)
        target = self._goal_target()
        plan_context = (
            tuple((slot.start, slot.end, slot.price) for slot in prices),
            self.deadline,
            target,
            self.settings.get("max_price_eur_kwh", 0.20),
            self.use_forecast,
        )
        plan, coverage_complete = self._choose_plan(prices, now, effective, data)
        data.update(plan.as_dict(self.settings["power_kw"]))
        data.update(self._battery_details(now, soc, effective))
        data["deadline_grace_minutes"] = grace_minutes
        data["deadline_extension_until"] = (
            extension_until.isoformat() if grace_minutes > 0 else None
        )

        planned_now = plan.charging_at(now)
        awaiting_soc_confirmation = (
            soc < target
            and effective >= target
            and self._credit_kwh > 0
            and self._active_charge_until is not None
        )
        data["soc_confirmation_until"] = None
        if awaiting_soc_confirmation:
            # The energy estimate must not end an active session before the
            # car itself reports the requested SOC. Keep charging in a short
            # rolling window, but for at most SOC_CONFIRMATION_LIMIT, so a
            # car limit below the target or a rounded sensor cannot keep
            # charging outside the plan until the deadline.
            if self._soc_confirmation_since is None:
                self._soc_confirmation_since = now
            confirmation_until = self._soc_confirmation_since + SOC_CONFIRMATION_LIMIT
            if now < confirmation_until:
                planned_now = True
                self._active_charge_until = min(now + timedelta(minutes=5), confirmation_until)
                data["soc_confirmation_until"] = confirmation_until.isoformat()
            else:
                planned_now = False
                self._active_charge_until = None
                self._active_plan_context = None
        elif planned_now:
            continuous_end = None
            for slot in (s for s in plan.slots if not s.estimated):
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
            # Keep an already-started continuous run stable. A new SOC report
            # or replanning must not create an off/on cycle at an hourly
            # price boundary.
            planned_now = True
        elif self._active_charge_until is not None and now < self._active_charge_until:
            # Price/deadline updates can arrive in several HA state changes.
            # Give the new plan one short settling window so an adjacent block
            # is not interrupted by an intermediate calculation. A real changed
            # plan still stops afterwards.
            if self._replan_stop_time is None:
                self._replan_stop_time = now
            if now - self._replan_stop_time < REPLAN_SETTLE:
                planned_now = True
            else:
                self._clear_active_run()
        else:
            self._clear_active_run()

        charger_state = self.hass.states.get(self.settings["charger_entity"])
        active_session = (
            charger_state is not None and charger_state.state == "on"
        ) or self._command is True
        extension_active = (
            after_deadline
            and grace_minutes > 0
            and now < extension_until
            and soc < target
            and active_session
        )
        desired = ((planned_now and not after_deadline) or extension_active) and soc < target
        data["deadline_extension_active"] = extension_active
        if extension_active:
            self._active_charge_until = extension_until
        data["active_charge_until"] = (
            self._active_charge_until.isoformat() if self._active_charge_until else None
        )
        if soc >= target:
            self._clear_active_run()
            status = "target_reached"
        elif extension_active:
            status = "charging_overtime"
        elif after_deadline:
            self._clear_active_run()
            status = "deadline_passed"
        elif effective >= target:
            status = "awaiting_soc_confirmation"
        elif desired:
            # The car is actively being asked to charge. The underlying price
            # plan may still be provisional, but that should not hide the
            # current charging state.
            status = "charging"
        elif plan.shortfall_kwh > 0.001:
            status = "insufficient_time" if coverage_complete else "waiting_for_prices"
        elif not coverage_complete:
            status = "provisional_plan"
        else:
            status = "scheduled"
        data["plan_is_provisional"] = not coverage_complete
        data["status"] = status if self.enabled or status == "target_reached" else "preview"
        data["plan_status"] = status
        return desired, prices

    def _price_at(self, now: datetime, prices: list[Slot]) -> float | None:
        if not prices:
            try:
                prices = self._prices()
            except (ValueError, TypeError, KeyError, OverflowError):
                return None
        for slot in prices:
            if slot.start <= now < slot.end:
                return slot.price
        return None

    def _update_session(
        self,
        now: datetime,
        prices: list[Slot],
        charging_requested: bool,
        control_enabled: bool,
        plan_status: str,
    ) -> None:
        """Account measured grid energy and its cost per charging session."""
        session = self.session
        active = session is not None and session.get("active")
        if not active and charging_requested:
            session = self.session = {
                "active": True,
                "started": now.isoformat(),
                "ended": None,
                "energy_kwh": 0.0,
                "cost_eur": 0.0,
                "cost_complete": True,
            }
            active = True
        if not active:
            return
        if self._interval_kwh > 0:
            price = self._price_at(now, prices)
            session["energy_kwh"] = round(session["energy_kwh"] + self._interval_kwh, 4)
            if price is None:
                session["cost_complete"] = False
            else:
                session["cost_eur"] = round(session["cost_eur"] + self._interval_kwh * price, 4)
        if not control_enabled or plan_status in SESSION_END_STATUSES:
            session["active"] = False
            session["ended"] = now.isoformat()

    def _update_repair_issue(
        self, now: datetime, data: dict[str, Any], control_enabled: bool
    ) -> None:
        """Raise a repair issue when an input or control problem persists."""
        problem = data["status"] if data["status"] in ("input_error", "control_error") else None
        if not control_enabled:
            problem = None
        if problem != self._problem:
            self._problem, self._problem_since = problem, now
        issue_id = f"{problem}_{self.entry.entry_id}" if problem else None
        if self._issue_id and self._issue_id != issue_id:
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
            self._issue_id = None
        if problem and not self._issue_id and now - self._problem_since >= REPAIR_DELAY:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=problem,
                translation_placeholders={
                    "title": self.entry.title,
                    "error": str(data.get("error")),
                },
            )
            self._issue_id = issue_id

    async def _control(self, desired: bool, now: datetime, force: bool = False) -> str | None:
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

        pending = "starting_charge" if desired else "stopping_charge"
        # After the confirmation timeout the command is reported as an error,
        # but it keeps being retried so the charger recovers on its own.
        if not force and now - self._command_time >= CONFIRM_TIMEOUT:
            pending = f"Charger did not confirm {wanted} within 5 minutes; retrying"
        should_send = (
            force
            or self._command_attempt_time is None
            or now - self._command_attempt_time >= RETRY_INTERVAL
        )
        if not should_send:
            return pending

        self._command_attempt_time = now
        try:
            async with asyncio.timeout(30):
                await self.hass.services.async_call(
                    "switch", f"turn_{wanted}", {"entity_id": entity_id}, blocking=True
                )
        except (HomeAssistantError, TimeoutError):
            return f"Charger did not accept {wanted}; will retry"
        return pending

    async def _ensure_unlocked(self, now: datetime) -> str | None:
        """Unlock the charger before the first start request."""
        entity_id = self.settings.get("lock_entity")
        if not entity_id:
            return None
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
        pending = "unlocking_charger"
        if now - self._unlock_command_time >= CONFIRM_TIMEOUT:
            pending = "Charger lock did not confirm unlocked within 5 minutes; retrying"
        if self._unlock_attempt_time is None or now - self._unlock_attempt_time >= RETRY_INTERVAL:
            self._unlock_attempt_time = now
            try:
                async with asyncio.timeout(30):
                    await self.hass.services.async_call(
                        "lock", "unlock", {"entity_id": entity_id}, blocking=True
                    )
            except (HomeAssistantError, TimeoutError):
                return "Charger did not accept unlock; will retry"
        return pending

    async def _update_lock(self, now: datetime, requested: bool) -> None:
        """Lock the charger once when the plan stops charging.

        Only the moment the plan stops counts: a charger unlocked by hand while
        the plan isn't charging stays unlocked. Switching automatic charging or
        charging now off by hand leaves the lock alone.
        """
        stopped = self._charge_requested and not requested and not self._stopped_by_user
        self._charge_requested = requested
        self._stopped_by_user = False
        if requested or not self.settings.get("lock_entity"):
            self._lock_pending = False
            return
        if stopped:
            self._lock_pending = True
            self._lock_attempt_time = None
        if not self._lock_pending:
            return
        entity_id = self.settings["lock_entity"]
        state = self.hass.states.get(entity_id)
        if state is not None and state.state == "locked":
            self._lock_pending = False
            return
        if state is None or state.state in ("unknown", "unavailable"):
            return  # try again once the lock is back
        if self._lock_attempt_time is not None and now - self._lock_attempt_time < RETRY_INTERVAL:
            return
        self._lock_attempt_time = now
        try:
            async with asyncio.timeout(30):
                await self.hass.services.async_call(
                    "lock", "lock", {"entity_id": entity_id}, blocking=True
                )
        except (HomeAssistantError, TimeoutError):
            _LOGGER.warning("The charger could not be locked after charging; will retry")
            return
        self._lock_pending = False
        self._unlock_command_time = None
        self._unlock_attempt_time = None

    async def _shutdown(self, event: Event) -> None:
        # A one-time listener is already removed once it has fired.
        self._stop_unsub = None
        await self.async_stop()

    async def async_stop(self) -> None:
        async with self._lock:
            self._stopping = True
            for unsub in self._unsubs:
                unsub()
            self._unsubs.clear()
            if self._stop_unsub is not None:
                self._stop_unsub()
                self._stop_unsub = None
            if self._forecast_unsub is not None:
                self._forecast_unsub()
                self._forecast_unsub = None
            if self.enabled or self.immediate_charging:
                error = await self._control(False, dt_util.utcnow(), force=True)
                if error and error not in PENDING_STATUSES:
                    _LOGGER.warning("Integration stopped: %s. Check the charger", error)
            if self._issue_id:
                ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
                self._issue_id = None
            await self.store.async_save(self._save_data())
