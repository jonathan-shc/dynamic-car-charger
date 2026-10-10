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
    speed_report: datetime | None = None
    trip_km: float | None = None
    trip_report: datetime | None = None
    powered: bool | None = None
    rider_home: bool | None = None
    rider_configured: bool = False
    charging: bool | None = None
    watts: float | None = None
    plug_on: bool = False
    power_report: datetime | None = None


class BikeLifecycle:
    def __init__(self, min_ride_distance_m=100):
        self.min_ride_distance_m = max(100, float(min_ride_distance_m))
        self.ride_distance_m = 0.0
        self._trip_consumed = False
        self._consumed_trip_km: float | None = None
        self._trip_live = False
        self._trip_base_km = 0.0
        self._latest_trip_km: float | None = None
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
        consumed = saved.get("consumed_trip_km")
        if isinstance(consumed, (int, float)) and 0 <= consumed <= 655.35:
            self._consumed_trip_km = float(consumed)
            self._trip_consumed = True
            self._trip_base_km = float(consumed)
        self.cable = "unknown"
        # Historical connection evidence survives restart; it is never a new
        # measurement. Ride/motion invalidates it, not a stationary wake-up.
        try:
            confirmed = datetime.fromisoformat(saved.get("cable_confirmed_at") or "")
        except (ValueError, TypeError):
            confirmed = None
        evidence = saved.get("cable_evidence")
        if (
            self.home
            and not self.away
            and saved.get("cable") == "connected"
            and confirmed is not None
            and confirmed.tzinfo is not None
            and evidence in ("ble_charging", "measured_power")
        ):
            self.cable, self.cable_at, self.cable_evidence = "connected", confirmed, evidence

    def saved(self):
        return {
            "home": self.home,
            "away": self.away,
            "probe_owned": self.probe_owned,
            "cable": self.cable,
            "cable_confirmed_at": self.cable_at.isoformat() if self.cable_at else None,
            "cable_evidence": self.cable_evidence,
            "consumed_trip_km": self._consumed_trip_km,
        }

    @property
    def ride_qualified(self):
        return not self._trip_consumed and self.ride_distance_m >= self.min_ride_distance_m - 1e-6

    def _reset_ride(self):
        self._consumed_trip_km = self._latest_trip_km
        self._trip_consumed = True
        self._trip_base_km = self._consumed_trip_km or 0.0

    def _observe_trip(self, now, o):
        # Only source packet timestamps establish freshness. Re-publishing a
        # retained trip in HA cannot manufacture arrival evidence.
        new_connection = o.live and not self._trip_live
        self._trip_live = o.live
        self.ride_distance_m = 0.0
        if (
            not o.live
            or o.trip_km is None
            or o.trip_report is None
            or not 0 <= o.trip_km <= 655.35
            or not timedelta(0) <= now - o.trip_report <= timedelta(seconds=30)
        ):
            return
        self._latest_trip_km = o.trip_km
        if o.trip_km == 0 or (new_connection and o.trip_km < self._trip_base_km):
            self._trip_base_km = 0.0
            self._trip_consumed = False
        elif new_connection and o.trip_km != self._consumed_trip_km:
            self._trip_consumed = False
        elif o.speed is not None and o.speed >= 2 and self._trip_consumed:
            # Further movement must add a new minimum distance, not reuse the
            # already-checked trip for a short move within the shed.
            self._trip_base_km = self._consumed_trip_km or 0.0
            self._trip_consumed = False
        self.ride_distance_m = max(0.0, o.trip_km - self._trip_base_km) * 1000

    def update(self, now: datetime, o: BikeObservation):
        self._observe_trip(now, o)
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
                    if self.ride_qualified:
                        self.arrived_at = now
                        self._reset_ride()
                    self.home, self.away = True, False
                    self.state, self.reason = "home_on", "arrival_settled"
            elif not moving and o.speed is not None and self.ride_qualified:
                # A fresh trip proves riding even when only the last metres
                # were in home BLE coverage. Phone GPS is not a cable gate.
                self.state, self.reason = "arriving", "fresh_trip_stopping"
                if self.still_since and now - self.still_since >= timedelta(seconds=30):
                    self.arrived_at = now
                    self.home, self.away = True, False
                    self.departure_pending = False
                    self.state, self.reason = "home_on", "fresh_trip_arrival"
                    self._reset_ride()
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
            if self.ride_qualified:
                self.arrived_at = now
                self._reset_ride()
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
        if moving:
            self.cable, self.cable_evidence, self.cable_at = "unknown", "bike_moving", None
        elif o.charging is True or power_proof:
            self.cable, self.cable_at = "connected", now
            self.cable_evidence = "ble_charging" if o.charging is True else "measured_power"
        elif self.state == "departing":
            self.cable, self.cable_evidence = "unknown", "bike_moving"

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
                or now - self.probe_started >= timedelta(minutes=5)
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
            "ride_distance_m": round(self.ride_distance_m, 1),
            "ride_evidence": "bluetooth_trip",
            "ride_qualified": self.ride_qualified,
            "minimum_ride_distance_m": self.min_ride_distance_m,
            "probe_active": self.probe_owned,
            "probe_result": self.probe_result,
        }
