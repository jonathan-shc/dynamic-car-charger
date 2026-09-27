"""Keep the car's drives: its positions while driving, cut into trips and kept for good.

The car's device tracker is followed. A step of more than 40 m starts a drive (a parked
car's GPS wanders less); a drive ends when the car hasn't moved for ten minutes, and keeps
the positions of the two minutes after its last step, so the route ends where the car
parked. Each drive is kept with its start, end, distance and route, the route simplified
to a few metres so a drive costs a few kilobytes, and with how much of the battery it
used, from the car's battery percentage. The Home Assistant zones it started and
ended in are looked up when the drives are asked for, so zones added or moved later name
earlier drives too. The trips have their own storage file, saved
only when a drive ends, so the frequently saved charging state stays small.

On every start the recorder's positions since the last kept drive are read, which adds
drives from before the integration kept them and finishes one cut off by a restart; the
battery use of drives the recorder still has is filled in the same way.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from functools import partial
from typing import Any

from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Not moving for this long ends a drive; a shorter stop is part of it.
PAUSE = timedelta(minutes=10)
# Positions this long after the last step still belong to the drive: slowing down,
# parking, and where the car stands.
SETTLE = timedelta(minutes=2)
# A step longer than this counts as moving.
STEP_METRES = 40.0
# Shorter drives (moving the car on the drive, a GPS jump) aren't kept.
MIN_KM = 0.5
# The route is kept to within this many metres of the positions.
SIMPLIFY_METRES = 8.0
# How far back the recorder is read at start; it usually keeps ten days.
RECORDER_DAYS = 10
# Drives kept by an older way of cutting them are rebuilt from the recorder once.
VERSION = 2

# The battery percentage after a drive: the middle reading of these minutes after it, as
# it wavers by a few tenths while the car settles.
SOC_SETTLE = timedelta(minutes=5)
# Battery readings kept for the drive under way: enough for a long one.
SOC_MEMORY = timedelta(hours=6)

Point = tuple[datetime, float, float]  # time, latitude, longitude
Reading = tuple[datetime, float]  # time, battery percentage


def metres(a: Point, b: Point) -> float:
    """Distance between two positions on the earth."""
    lat1, lat2 = math.radians(a[1]), math.radians(b[1])
    d_lat, d_lon = lat2 - lat1, math.radians(b[2] - a[2])
    h = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    return 2 * 6_371_000 * math.asin(min(1.0, math.sqrt(h)))


def split(points: list[Point]) -> list[list[Point]]:
    """The drives in a series of positions, each at least half a kilometre long: from the
    position before the first step of more than 40 m to the last such step with no pause
    between steps, every position in between (slow traffic too), and the positions of
    the two minutes after it."""
    moves: list[list[int]] = []  # first and last index of each run of steps
    for index in range(1, len(points)):
        if metres(points[index - 1], points[index]) <= STEP_METRES:
            continue
        if moves and points[index - 1][0] - points[moves[-1][1]][0] <= PAUSE:
            moves[-1][1] = index
        else:
            moves.append([index - 1, index])
    trips = []
    for first, last in moves:
        end = last
        while end + 1 < len(points) and points[end + 1][0] - points[last][0] <= SETTLE:
            end += 1
        trips.append(points[first : end + 1])
    return [trip for trip in trips if length_km(trip) >= MIN_KM]


def length_km(points: list[Point]) -> float:
    return sum(metres(a, b) for a, b in zip(points, points[1:], strict=False)) / 1000


def simplify(points: list[Point], tolerance: float = SIMPLIFY_METRES) -> list[Point]:
    """Fewer positions for the same line (Ramer-Douglas-Peucker): a straight road needs
    two, a bend a few."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        farthest, distance = None, tolerance
        for index in range(first + 1, last):
            off = _off_line(points[index], points[first], points[last])
            if off > distance:
                farthest, distance = index, off
        if farthest is not None:
            keep[farthest] = True
            stack += [(first, farthest), (farthest, last)]
    return [point for point, kept in zip(points, keep, strict=True) if kept]


def _off_line(point: Point, start: Point, end: Point) -> float:
    """How far a position is from the straight line between two others, in metres."""
    # Flat within a drive: metres east and north of the start.
    scale = math.cos(math.radians(start[1]))

    def xy(p: Point) -> tuple[float, float]:
        return ((p[2] - start[2]) * 111_320 * scale, (p[1] - start[1]) * 110_540)

    (px, py), (ex, ey) = xy(point), xy(end)
    length = math.hypot(ex, ey)
    if length == 0:
        return math.hypot(px, py)
    t = max(0.0, min(1.0, (px * ex + py * ey) / length**2))
    return math.hypot(px - t * ex, py - t * ey)


def record(points: list[Point]) -> dict[str, Any]:
    """A drive as kept: the route as [latitude, longitude, seconds after the start]."""
    start = points[0][0]
    return {
        "started": start.isoformat(),
        "ended": points[-1][0].isoformat(),
        "distance_km": round(length_km(points), 2),
        "route": [
            [round(lat, 5), round(lon, 5), round((time - start).total_seconds())]
            for time, lat, lon in simplify(points)
        ],
    }


def battery_used(started: datetime, ended: datetime, readings: list[Reading]) -> float | None:
    """How many percent of the battery a drive used: the reading at its start less the
    middle reading of the minutes after it. None when that isn't known, or the battery went
    up (charged on the way)."""
    before = [soc for time, soc in readings if time <= started]
    after = sorted(soc for time, soc in readings if ended <= time <= ended + SOC_SETTLE)
    if not after:
        after = [soc for time, soc in readings if started < time <= ended][-1:]
    if not before or not after:
        return None
    used = before[-1] - after[len(after) // 2]
    # A tenth or two either way is the reading wavering.
    return round(max(0.0, used), 1) if used > -0.5 else None


def reading(state: State | None) -> Reading | None:
    if state is None:
        return None
    try:
        return (state.last_updated, float(state.state))
    except ValueError:
        return None


def with_zones(trip: dict[str, Any], zones: list[dict[str, Any]]) -> dict[str, Any]:
    """A kept drive with the zones it started and ended in, as they are now: a zone added
    or moved later counts for earlier drives too."""
    named = {key: value for key, value in trip.items() if not key.startswith(("from_", "to_"))}
    route = trip.get("route") or []
    for key, row in (("from", route[:1]), ("to", route[-1:])):
        if row and (zone := zone_at((None, row[0][0], row[0][1]), zones)):
            named[f"{key}_zone"], named[f"{key}_name"] = zone["entity_id"], zone["name"]
    return named


def zone_at(point: Point, zones: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The smallest zone the position is in."""
    inside = [
        zone
        for zone in zones
        if metres(point, (point[0], zone["latitude"], zone["longitude"])) <= zone["radius"]
    ]
    return min(inside, key=lambda zone: zone["radius"], default=None)


def zones_of(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Home Assistant's zones, passive ones included: they name places too."""
    zones = []
    for state in hass.states.async_all("zone"):
        attributes = state.attributes
        lat, lon = attributes.get("latitude"), attributes.get("longitude")
        if not isinstance(lat, int | float) or not isinstance(lon, int | float):
            continue
        zones.append(
            {
                "entity_id": state.entity_id,
                "name": attributes.get("friendly_name") or state.entity_id,
                "latitude": float(lat),
                "longitude": float(lon),
                "radius": float(attributes.get("radius") or 100),
            }
        )
    return zones


def position(state: State | None) -> Point | None:
    if state is None:
        return None
    lat, lon = state.attributes.get("latitude"), state.attributes.get("longitude")
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        return None
    return (state.last_updated, float(lat), float(lon))


class TripRecorder:
    """Follow one car's device tracker and keep its drives."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, entity_id: str, soc_entity: str | None = None
    ) -> None:
        self.hass = hass
        self.entity_id = entity_id
        self.soc_entity = soc_entity
        self.store: Store = Store(hass, 1, f"{DOMAIN}.{entry_id}.trips")
        # Every drive, oldest first.
        self.trips: list[dict[str, Any]] = []
        # The drive under way: every position since the car started moving.
        self._drive: list[Point] = []
        self._moved_at: datetime | None = None
        self._last: Point | None = None
        # Recent battery readings, oldest first.
        self._readings: list[Reading] = []
        self._rebuilt = False
        self._unsubs: list = []

    async def async_start(self) -> None:
        saved = await self.store.async_load() or {}
        self.trips = list(saved.get("trips") or [])
        self._rebuilt = bool(saved) and saved.get("version", 1) < VERSION
        if self._rebuilt:
            # Cut the old way: the drives the recorder still has are cut again.
            since = dt_util.utcnow() - timedelta(days=RECORDER_DAYS)
            self.trips = [
                trip for trip in self.trips if dt_util.parse_datetime(trip["started"]) < since
            ]
        self._unsubs = [
            async_track_state_change_event(self.hass, [self.entity_id], self._changed),
            async_track_time_interval(self.hass, self._tick, timedelta(minutes=1)),
        ]
        if self.soc_entity:
            self._unsubs.append(
                async_track_state_change_event(self.hass, [self.soc_entity], self._soc_changed)
            )
        self.hass.async_create_task(
            self._async_import_recorded(), f"{DOMAIN} import recorded trips"
        )

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []

    @callback
    def _changed(self, event: Event) -> None:
        point = position(event.data.get("new_state"))
        if point is not None:
            self.add(point)

    @callback
    def _soc_changed(self, event: Event) -> None:
        if (found := reading(event.data.get("new_state"))) is not None:
            self.add_reading(found)

    def add_reading(self, found: Reading) -> None:
        self._readings.append(found)
        self._forget_readings(found[0])

    def _forget_readings(self, now: datetime) -> None:
        """Drop readings older than the memory, but the last one before it: the battery as
        it was when a drive started."""
        cut = now - SOC_MEMORY
        while len(self._readings) > 1 and self._readings[1][0] <= cut:
            self._readings.pop(0)

    async def _tick(self, now: datetime) -> None:
        self.finish_if_parked(now)

    def add(self, point: Point) -> None:
        """A new position: a step of more than 40 m starts a drive or keeps it going."""
        moved = self._last is not None and metres(self._last, point) > STEP_METRES
        if self._drive:
            self._drive.append(point)
        elif moved:
            self._drive = [self._last, point]
        if moved:
            self._moved_at = point[0]
        self._last = point

    def finish_if_parked(self, now: datetime) -> None:
        """Keep the drive under way once the car hasn't moved for ten minutes."""
        if not self._drive or self._moved_at is None or now - self._moved_at < PAUSE:
            return
        drive, self._drive = self._drive, []
        if self.keep(split(drive)):
            self._save()

    def keep(self, drives: list[list[Point]]) -> int:
        """Add drives that start after the last one kept; returns how many."""
        last_end = dt_util.parse_datetime(self.trips[-1]["ended"]) if self.trips else None
        added = 0
        for drive in drives:
            if last_end is not None and drive[0][0] <= last_end:
                continue
            kept = record(drive)
            used = battery_used(drive[0][0], drive[-1][0], self._readings)
            if used is not None:
                kept["battery_used"] = used
            self.trips.append(kept)
            last_end = drive[-1][0]
            added += 1
        return added

    def import_states(self, states: list[State], now: datetime) -> int:
        """Drives from recorded states of the tracker. One still under way (moved within
        the last ten minutes) isn't kept yet: the live positions carry it on."""
        points = sorted(point for point in map(position, states) if point is not None)
        drives = split(points)
        if drives and now - drives[-1][-1][0] < PAUSE:
            under_way = drives.pop()
            if not self._drive:
                self._drive = under_way + [point for point in points if point[0] > under_way[-1][0]]
                self._moved_at = under_way[-1][0]
        if points and self._last is None:
            self._last = points[-1]
        return self.keep(drives)

    def fill_battery_used(self, readings: list[Reading]) -> int:
        """Battery use for kept drives without it, from recorded readings; returns how many."""
        filled = 0
        for trip in self.trips:
            if "battery_used" in trip:
                continue
            started = dt_util.parse_datetime(trip["started"])
            ended = dt_util.parse_datetime(trip["ended"])
            if not readings or readings[0][0] > started:
                continue  # from before the recorder's readings
            if (used := battery_used(started, ended, readings)) is not None:
                trip["battery_used"] = used
                filled += 1
        return filled

    def response(
        self,
        limit: int,
        capacity_kwh: float | None = None,
        efficiency: float = 1.0,
        price: float | None = None,
        currency: str | None = None,
    ) -> dict[str, Any]:
        """For the get_trips action: the last drives, newest first, with their zones and,
        where the battery use is known, the energy from the battery and what charging it
        back costs at the price paid on average."""
        zones = zones_of(self.hass)
        trips = []
        for trip in reversed(self.trips[-limit:]):
            named = with_zones(trip, zones)
            if capacity_kwh and (used := trip.get("battery_used")) is not None:
                named["energy_kwh"] = round(used / 100 * capacity_kwh, 2)
                if price is not None:
                    named["cost"] = round(named["energy_kwh"] / efficiency * price, 2)
                    named["currency"] = currency
            trips.append(named)
        return {"trips": trips, "total": len(self.trips)}

    def _save(self) -> None:
        self.store.async_delay_save(lambda: {"version": VERSION, "trips": self.trips}, 1)

    async def _async_import_recorded(self) -> None:
        if "recorder" not in self.hass.config.components:
            return
        from homeassistant.components.recorder import get_instance, history

        start = dt_util.utcnow() - timedelta(days=RECORDER_DAYS)
        if self.trips:
            start = max(start, dt_util.parse_datetime(self.trips[-1]["ended"]))
        try:
            found = await get_instance(self.hass).async_add_executor_job(
                partial(
                    history.state_changes_during_period,
                    self.hass,
                    start,
                    entity_id=self.entity_id,
                    include_start_time_state=True,
                )
            )
        except Exception:  # noqa: BLE001 - earlier drives are a bonus; never block charging
            _LOGGER.warning("Could not read earlier drives from the recorder")
            return
        # Battery readings: taken first, so drives imported now have them too.
        readings: list[Reading] = []
        if self.soc_entity:
            try:
                recorded = await get_instance(self.hass).async_add_executor_job(
                    partial(
                        history.state_changes_during_period,
                        self.hass,
                        dt_util.utcnow() - timedelta(days=RECORDER_DAYS),
                        entity_id=self.soc_entity,
                        include_start_time_state=True,
                    )
                )
            except Exception:  # noqa: BLE001 - battery use is a bonus too
                _LOGGER.warning("Could not read the battery's history from the recorder")
            else:
                readings = sorted(
                    found
                    for found in map(reading, recorded.get(self.soc_entity, []))
                    if found is not None
                )
        self._readings = sorted(readings + self._readings)
        added = self.import_states(found.get(self.entity_id, []), dt_util.utcnow())
        filled = self.fill_battery_used(self._readings)
        self._forget_readings(dt_util.utcnow())
        if added or filled or self._rebuilt:
            self._save()
            _LOGGER.info("Kept %d drives from the recorder", added)
