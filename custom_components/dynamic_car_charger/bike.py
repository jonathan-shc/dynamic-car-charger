"""Conservative shed-bike presence and bounded charger discovery.

BLE range is assumed to cover home. Radio loss alone is never an away/off verdict.
A cable confirmation is historical while the charger is off, not a physical sensor.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class BikeObservation:
    live: bool = False
    speed: float | None = None
    powered: bool | None = None
    rider_home: bool | None = None
    rider_configured: bool = False
    charging: bool | None = None
    watts: float | None = None
    plug_on: bool = False
    power_report: datetime | None = None


class BikeLifecycle:
    def __init__(self):
        self.state = "unknown"
        self.reason = "no_observation"
        self.home = False
        self.away = False
        self.cable = "unknown"
        self.cable_at: datetime | None = None
        self.cable_evidence: str | None = None
        self.moving_since: datetime | None = None
        self.last_motion: datetime | None = None
        self.departure_pending = False
        self.still_since: datetime | None = None
        self.arrived_at: datetime | None = None
        self.probed_arrival: datetime | None = None
        self.probe_started: datetime | None = None
        self.probe_owned = False
        self.probe_seen_on = False
        self.probe_result: str | None = None
        self.recover_stop = False

    def restore(self, saved):
        # Restart cannot assert current presence or resume a probe. Clean up its plug.
        self.home = bool(saved.get("home"))
        self.away = bool(saved.get("away"))
        self.recover_stop = bool(saved.get("probe_owned"))
        self.cable = "unknown"

    def saved(self):
        return {"home": self.home, "away": self.away, "probe_owned": self.probe_owned}

    def update(self, now: datetime, o: BikeObservation):
        previous = self.state
        moving = o.live and o.speed is not None and o.speed >= 2
        if moving:
            self.moving_since = self.moving_since or now
            self.last_motion = now
            self.departure_pending = True
            self.still_since = None
        else:
            self.moving_since = None
            if o.live and o.speed is not None and o.speed < 2:
                self.still_since = self.still_since or now
            elif not o.live:
                self.still_since = None
        if not o.live and self.departure_pending and self.last_motion:
            elapsed = now - self.last_motion
            if elapsed > timedelta(minutes=3) or (
                elapsed >= timedelta(seconds=60) and o.rider_home is True
            ):
                # Contrary home evidence/expired intent must not turn a later
                # stale phone update into a delayed, false departure.
                self.departure_pending = False
        if o.live:
            # Reappearance after a credible departure is arrival, even when the
            # first speed frame is already zero. A stationary wake-up isn't arrival.
            if self.away:
                self.state, self.reason = "arriving", "returned_to_home_bluetooth"
                if self.still_since and now - self.still_since >= timedelta(seconds=30):
                    self.arrived_at = now
                    self.home, self.away = True, False
                    self.state, self.reason = "home_on", "arrival_settled"
            elif moving and self.moving_since and now - self.moving_since >= timedelta(seconds=10):
                self.state, self.reason = "departing", "sustained_bike_motion"
                self.home = True
                self.departure_pending = True
            elif not moving:
                self.home = True
                if self.departure_pending and (
                    not self.still_since or now - self.still_since < timedelta(seconds=30)
                ):
                    self.state, self.reason = "departing", "brief_stop_before_departure"
                else:
                    self.departure_pending = False
                    self.state, self.reason = "home_on", "fresh_home_bluetooth"
        elif o.powered is False and self.away and previous == "arriving":
            self.home, self.away = True, False
            self.departure_pending = False
            self.arrived_at = now
            self.state, self.reason = "home_off", "returned_then_powered_off"
        elif o.powered is False and self.home and not self.departure_pending and previous != "away":
            self.state, self.reason = "home_off", "observed_power_off"
        elif (
            self.last_motion
            and now - self.last_motion >= timedelta(seconds=60)
            and self.departure_pending
            and (
                o.rider_configured
                or previous == "departing"
                or self.reason == "motion_awaiting_radio_confirmation"
            )
            and (o.rider_home is False if o.rider_configured else o.rider_home is not True)
        ):
            self.home, self.away = False, True
            self.departure_pending = False
            self.state, self.reason = "away", "motion_then_radio_loss"
            self.cable, self.cable_evidence, self.cable_at = "disconnected", "bike_departed", now
        elif self.away:
            self.state, self.reason = "away", "last_confirmed_departure"
        elif self.home:
            self.state, self.reason = (
                "home_unreachable",
                (
                    "motion_awaiting_radio_confirmation"
                    if self.departure_pending and previous == "departing"
                    else self.reason
                    if self.reason == "motion_awaiting_radio_confirmation"
                    and self.departure_pending
                    else "radio_loss_without_departure"
                ),
            )
        else:
            self.state, self.reason = "unknown", "no_home_evidence"
        # Fresh charging proves a cable. Measured power must follow plug activation
        # and is sampled again after ten seconds; standby/full idle proves nothing.
        power_proof = (
            o.plug_on
            and o.watts is not None
            and o.watts >= 10
            and o.power_report is not None
            and timedelta(0) <= now - o.power_report <= timedelta(seconds=30)
            and (
                self.probe_started is None
                or (
                    o.power_report >= self.probe_started + timedelta(seconds=10)
                    and now - self.probe_started >= timedelta(seconds=10)
                )
            )
        )
        if o.charging is True or power_proof:
            self.cable, self.cable_at = "connected", now
            self.cable_evidence = "ble_charging" if o.charging is True else "measured_power"
        elif self.state == "departing":
            self.cable, self.cable_evidence = "unknown", "bike_moving"
        # A retained cable confirmation must never survive a new arrival/wake-up.
        elif previous in ("away", "home_off", "home_unreachable") and o.live:
            self.cable, self.cable_evidence = "unknown", "connection_needs_check"

    def control(self, now, o, *, scheduled, allow_probe, cancel):
        """Return (plug demand, own control). Normal plan always has priority."""
        if cancel and self.arrived_at:
            self.probed_arrival = self.arrived_at  # don't restart after a manual stop
        if scheduled and not cancel:
            self.probe_started, self.probe_owned, self.recover_stop = None, False, False
            return True, True
        if self.recover_stop:
            if o.plug_on:
                return False, True
            self.recover_stop = False
        if self.probe_owned:
            externally_stopped = self.probe_seen_on and not o.plug_on
            self.probe_seen_on = self.probe_seen_on or o.plug_on
            stop = (
                externally_stopped
                or cancel
                or o.speed is None
                or o.speed >= 2
                or not o.live
                or self.state != "home_on"
                or self.cable == "connected"
                or now - self.probe_started >= timedelta(seconds=90)
            )
            if stop:
                self.probe_result = "connected" if self.cable == "connected" else "inconclusive"
                if not o.plug_on:
                    self.probe_owned, self.probe_started = False, None
                    return False, False
                return False, True  # retain ownership until off is confirmed
            return True, True
        if (
            allow_probe
            and not cancel
            and o.live
            and o.speed is not None
            and o.speed < 2
            and self.state == "home_on"
            and self.arrived_at is not None
            and self.probed_arrival != self.arrived_at
            and now - self.arrived_at <= timedelta(minutes=3)
            and self.cable != "connected"
            and not o.plug_on
        ):
            self.probed_arrival = self.arrived_at
            self.probe_started, self.probe_owned = now, True
            self.probe_seen_on = False
            self.probe_result = "checking"
            return True, True
        return False, False

    def details(self):
        return {
            "state": self.state,
            "reason": self.reason,
            "confidence": "observed" if self.state in ("home_on", "home_off") else "inferred",
            "cable": self.cable,
            "cable_evidence": self.cable_evidence,
            "cable_confirmed_at": self.cable_at.isoformat() if self.cable_at else None,
            "probe_active": self.probe_owned,
            "probe_result": self.probe_result,
        }
