"""Live Activities and notifications for the Charge Remote app, sent from here.

The app registers its push tokens with `register_push`: one for the phone's own
notifications, one that lets a Live Activity be started by a push, and one per running
Live Activity. When a vehicle starts charging, this starts its Live Activity on the
phone, keeps it current while charging, and ends it when charging stops, also while
the app is closed. The phone is reached through Apple's push service (APNs) with the
app developer's key, set once with `set_push_credentials` and kept in Home Assistant.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

HOSTS = {False: "https://api.push.apple.com", True: "https://api.sandbox.push.apple.com"}
# ActivityKit decodes dates in a Live Activity's content as seconds since 2001.
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)
# Statuses during which a vehicle's Live Activity shows.
ACTIVE = ("charging", "charging_overtime", "starting_charge", "awaiting_soc_confirmation")
# A Live Activity is updated at least this often while charging, and at once on a change.
REFRESH = 300
# A Live Activity is only started once energy flows: not for a plug switched on for a
# moment to see whether anything is on it, nor for a charger that doesn't start.
STARTS = ("charging", "charging_overtime")
# Passed through for a moment when charging stops: what follows (target reached, the next
# block) decides what the Live Activity shows last, so it is neither updated nor ended.
SETTLING = ("stopping_charge",)
# An ended Live Activity stays on the Lock Screen this long with its final state.
DISMISS_AFTER = 5 * 60
FLOWING_KW = 0.005
# A charging block this close with nothing plugged in gets a notification.
UNPLUGGED_WARNING = 15 * 60
# A charger or plug that can't be reached for this long gets a notification.
UNREACHABLE_AFTER = 10 * 60
# A target out of reach is only told once it has been so for this long: right after a
# restart, or while prices load, a plan can briefly come up short.
SHORTFALL_AFTER = 10 * 60
# No notifications this soon after Home Assistant started: its first plans are partial.
QUIET_AFTER_START = 5 * 60
# Apple's signed token lasts an hour; a new one is made well before.
TOKEN_LIFETIME = 50 * 60

TEXTS = {
    "nl": {
        "charging": "Laden",
        "charging_until": "Laadt tot {time}",
        "starting_charge": "Laden starten…",
        "stopping_charge": "Pauzeren…",
        "target_reached": "Doel bereikt",
        "charging_overtime": "Rondt af na de deadline",
        "awaiting_soc_confirmation": "Bijna klaar",
        "waiting_for_car": "Wacht op de auto",
        "waiting_for_scooter": "Wacht op de scooter",
        "waiting_for_bike": "Wacht op de e-bike",
        "waiting_for_power": "Wacht op beschikbare stroom",
        "control_error": "Laadpaal reageert niet",
        "control_error_plug": "Stekker reageert niet",
        "next_charge": "Volgende laadbeurt {range}",
        "started_title": "{name} laadt",
        "started_body": "Tot {target}%, nu {soc}%",
        "done_title": "{name} is klaar",
        "done_body": "{soc}% geladen",
        "problem_title": "{name}: laden lukt niet",
        "unplugged_title": "{name} is niet aangesloten",
        "away_title": "{name} is niet thuis",
        "unplugged_body": "Laden begint om {time}",
        "plugged_title": "{name} aangesloten",
        "plugged_deadline": "Klaar {when} · {target}%",
        "plugged_cheap": "Laadt tot {target}%, alleen onder {price}",
        "plugged_none": "Laadt tot {target}%; nog geen klaar-om-tijd",
        "shortfall_title": "{name} haalt {target}% niet",
        "shortfall_body": "Niet vóór {when}. Laad nu of pas het doel aan.",
        "interrupted_title": "{name} losgekoppeld",
        "interrupted_body": "Gestopt bij {soc}%, doel was {target}%",
        "unreachable_title": "{name}: {device} niet bereikbaar",
        "unreachable_body": "Het laden wacht tot hij terug is.",
        "charger": "laadpaal",
        "plug": "stekker",
        "today": "vandaag",
        "tomorrow": "morgen",
        "days": ["ma", "di", "wo", "do", "vr", "za", "zo"],
    },
    "en": {
        "charging": "Charging",
        "charging_until": "Charging until {time}",
        "starting_charge": "Starting to charge…",
        "stopping_charge": "Pausing…",
        "target_reached": "Target reached",
        "charging_overtime": "Finishing after the deadline",
        "awaiting_soc_confirmation": "Almost there",
        "waiting_for_car": "Waiting for the car",
        "waiting_for_scooter": "Waiting for the scooter",
        "waiting_for_bike": "Waiting for the e-bike",
        "waiting_for_power": "Waiting for available household power",
        "control_error": "Charger not responding",
        "control_error_plug": "Plug not responding",
        "next_charge": "Next charge {range}",
        "started_title": "{name} is charging",
        "started_body": "To {target}%, now {soc}%",
        "done_title": "{name} is done",
        "done_body": "{soc}% charged",
        "problem_title": "{name}: charging isn't working",
        "unplugged_title": "{name} isn't plugged in",
        "away_title": "{name} isn't home",
        "unplugged_body": "Charging starts at {time}",
        "plugged_title": "{name} plugged in",
        "plugged_deadline": "Ready {when} · {target}%",
        "plugged_cheap": "Charging to {target}%, only below {price}",
        "plugged_none": "Charging to {target}%; no ready-by time yet",
        "shortfall_title": "{name} won't reach {target}%",
        "shortfall_body": "Not before {when}. Charge now or change the target.",
        "interrupted_title": "{name} unplugged",
        "interrupted_body": "Stopped at {soc}%, the target was {target}%",
        "unreachable_title": "{name}: {device} not reachable",
        "unreachable_body": "Charging waits until it is back.",
        "charger": "charger",
        "plug": "plug",
        "today": "today",
        "tomorrow": "tomorrow",
        "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    },
}


def apple_time(moment: datetime | None) -> float | None:
    return None if moment is None else (moment - APPLE_EPOCH).total_seconds()


def _texts(language: str | None) -> dict[str, str]:
    return TEXTS.get((language or "en")[:2], TEXTS["en"])


def _when(moment: datetime, texts: dict, now: datetime) -> str:
    """ "today 07:30", "tomorrow 07:30" or "Tue 07:30", in Home Assistant's time."""
    zone = dt_util.get_default_time_zone()
    local, today = moment.astimezone(zone), now.astimezone(zone).date()
    days = (local.date() - today).days
    day = (
        texts["today"]
        if days == 0
        else texts["tomorrow"]
        if days == 1
        else texts["days"][local.weekday()]
    )
    return f"{day} {local.strftime('%H:%M')}"


def _percent(value) -> str:
    return f"{value:.0f}" if isinstance(value, int | float) else "–"


def _window(data: dict[str, Any], now: datetime) -> tuple[datetime, datetime] | None:
    """The current or next block of published charging slots, adjacent ones joined."""
    slots = sorted(
        (
            (dt_util.parse_datetime(slot["start"]), dt_util.parse_datetime(slot["end"]))
            for slot in data.get("slots") or []
            if not slot.get("estimated")
        ),
        key=lambda pair: pair[0],
    )
    block = None
    for start, end in slots:
        if end is None or start is None or end <= now:
            continue
        if block is None:
            block = [start, end]
        elif start <= block[1]:
            block[1] = max(block[1], end)
        else:
            break
    return tuple(block) if block else None


def headline(data: dict[str, Any], language: str | None, now: datetime, kind: str) -> str:
    """The plan in one line, as the app words it (kind: car, scooter or bike)."""
    texts = _texts(language)
    status = data.get("status") or ""
    zone = dt_util.get_default_time_zone()
    window = _window(data, now)
    if status == "charging":
        if window and window[0] <= now:
            return texts["charging_until"].format(time=window[1].astimezone(zone).strftime("%H:%M"))
        return texts["charging"]
    if status == "waiting_for_car":
        return texts[
            {"car": "waiting_for_car", "scooter": "waiting_for_scooter"}.get(
                kind, "waiting_for_bike"
            )
        ]
    if status == "control_error":
        return texts["control_error" if kind == "car" else "control_error_plug"]
    if status in texts:
        return texts[status]
    if window:
        start, end = (moment.astimezone(zone).strftime("%H:%M") for moment in window)
        return texts["next_charge"].format(range=f"{start}⁠–⁠{end}")
    return status.replace("_", " ").capitalize()


def content_state(
    data: dict[str, Any], entry_id: str, language: str | None, power_kw: float, kind: str
) -> dict[str, Any]:
    """The Live Activity's content, with the app's ContentState field names."""
    now = dt_util.utcnow()
    window = _window(data, now)
    status = data.get("status")
    charging = status in ("charging", "charging_overtime") and power_kw > 0
    cheap = data.get("planning_method") in ("cheap_only", "cheap_after_deadline")
    deadline = dt_util.parse_datetime(data["deadline"]) if data.get("deadline") else None
    state = {
        "headline": headline(data, language, now, kind),
        "soc": data.get("estimated_soc", data.get("measured_soc")),
        "target": data.get("target_percentage"),
        "powerKw": round(power_kw, 3) if charging else None,
        "charging": charging,
        "windowStart": apple_time(window[0]) if window else None,
        "windowEnd": apple_time(window[1]) if window else None,
        "readyBy": None if cheap else apple_time(deadline),
        "cheapPrice": data.get("cheap_price_eur_kwh") if cheap else None,
        "costToGo": data.get("estimated_cost_eur"),
        "updated": apple_time(now),
        "vehicleEntryID": entry_id,
    }
    return {key: value for key, value in state.items() if value is not None}


def _fingerprint(state: dict[str, Any]) -> tuple:
    """What a change worth an immediate update is made of."""
    soc = state.get("soc")
    return (
        state.get("headline"),
        state.get("charging"),
        int(soc) if isinstance(soc, int | float) else None,
        state.get("target"),
        state.get("windowStart"),
        state.get("windowEnd"),
    )


def _keep_activity_start(state: dict[str, Any], memo: dict[str, Any]) -> None:
    """Keep a running Live Activity's original charging-period start.

    The planner clips the current price slot to ``now`` every time it replans. Without
    remembering the first value, ``windowStart`` consequently moves forward on every
    push and the app repeatedly shows a new "starts in 0:00" countdown.
    """
    start = memo.get("activity_start")
    if start is None:
        # Immediate charging has no planned window; its first update time is still a
        # useful, stable start for the Live Activity.
        start = state.get("windowStart", state["updated"])
        memo["activity_start"] = start
    state["windowStart"] = start


class PushHub:
    """The phones that registered, Apple's key, and what each phone was last sent."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store: Store = Store(hass, 1, f"{DOMAIN}.push")
        self.credentials: dict[str, Any] = {}
        self.devices: dict[str, dict[str, Any]] = {}
        # (device, entry): what was sent last.
        self._memo: dict[tuple[str, str], dict[str, Any]] = {}
        self._token: tuple[str, float] | None = None
        self._client = None
        # The last few pushes and what Apple answered, for push_status.
        self.recent: list[dict[str, Any]] = []
        # Plan updates come in quick succession; one at a time, so a start is sent once.
        self._lock = asyncio.Lock()
        self._loaded_at = time.time()

    async def async_load(self) -> None:
        saved = await self.store.async_load() or {}
        self.credentials = dict(saved.get("credentials") or {})
        self.devices = dict(saved.get("devices") or {})

    @property
    def configured(self) -> bool:
        return all(self.credentials.get(key) for key in ("key_id", "team_id", "bundle_id", "key"))

    def _save(self) -> None:
        self.store.async_delay_save(
            lambda: {"credentials": self.credentials, "devices": self.devices}, 2
        )

    async def async_set_credentials(self, key_id, team_id, bundle_id, key, sandbox=False) -> None:
        self.credentials = {
            "key_id": key_id.strip(),
            "team_id": team_id.strip(),
            "bundle_id": bundle_id.strip(),
            "key": key.strip(),
            "sandbox": bool(sandbox),
        }
        self._token = None
        self._save()

    def register(self, device_id: str, update: dict[str, Any]) -> None:
        """A phone's tokens and wishes; given fields replace, missing ones are kept."""
        device = self.devices.setdefault(device_id, {"activities": {}})
        for key in ("device_token", "start_token", "language", "alerts", "alert_types", "entries"):
            if key in update:
                device[key] = update[key]
        for entry_id, token in (update.get("activities") or {}).items():
            if token:
                device.setdefault("activities", {})[entry_id] = token
            else:
                device.setdefault("activities", {}).pop(entry_id, None)
        device["registered_at"] = dt_util.utcnow().isoformat()
        self._save()

    def status(self) -> dict[str, Any]:
        """For push_status: whether the key signs, the phones and their tokens, and the
        last pushes with Apple's answers. Never the key or full tokens."""
        signs, error = False, None
        if self.configured:
            try:
                self._token = None
                self._jwt()
                signs = True
            except Exception as err:  # noqa: BLE001 - reported, not raised
                error = f"{type(err).__name__}: {err}"
        return {
            "configured": self.configured,
            "key_signs": signs,
            "key_error": error,
            "key_id": self.credentials.get("key_id"),
            "team_id": self.credentials.get("team_id"),
            "bundle_id": self.credentials.get("bundle_id"),
            "sandbox": bool(self.credentials.get("sandbox")),
            "devices": [
                {
                    "device": device_id[:8],
                    "registered_at": device.get("registered_at"),
                    "language": device.get("language"),
                    "notifications": bool(device.get("alerts")),
                    "notification_token": bool(device.get("device_token")),
                    "live_activity_start_token": bool(device.get("start_token")),
                    "live_activities": sorted((device.get("activities") or {}).keys()),
                    "vehicles": device.get("entries"),
                }
                for device_id, device in self.devices.items()
            ],
            "recent_pushes": self.recent,
        }

    def _jwt(self) -> str:
        import jwt

        now = time.time()
        if self._token and now - self._token[1] < TOKEN_LIFETIME:
            return self._token[0]
        token = jwt.encode(
            {"iss": self.credentials["team_id"], "iat": int(now)},
            self.credentials["key"],
            algorithm="ES256",
            headers={"kid": self.credentials["key_id"]},
        )
        self._token = (token, now)
        return token

    async def _send(self, token: str, push_type: str, payload: dict, priority: int = 10) -> int:
        """Sends one push; the HTTP status (200 sent, 410 the token is gone)."""
        if self._client is None:
            self._client = http2_client(self.hass)
        topic = self.credentials["bundle_id"]
        if push_type == "liveactivity":
            topic += ".push-type.liveactivity"
        response = await self._client.post(
            f"{HOSTS[bool(self.credentials.get('sandbox'))]}/3/device/{token}",
            json=payload,
            headers={
                "authorization": f"bearer {self._jwt()}",
                "apns-topic": topic,
                "apns-push-type": push_type,
                "apns-priority": str(priority),
            },
            timeout=20,
        )
        if response.status_code != 200:
            _LOGGER.warning("Push not sent (%s): %s", response.status_code, response.text)
        self.recent = [
            {
                "at": dt_util.utcnow().isoformat(),
                "type": push_type,
                "event": (payload.get("aps") or {}).get("event") or payload.get("kind"),
                "status": response.status_code,
                "reason": None if response.status_code == 200 else response.text[:200],
            },
            *self.recent,
        ][:10]
        return response.status_code

    async def async_vehicle_update(
        self, entry_id: str, name: str, data: dict[str, Any], power_kw: float, kind: str
    ) -> None:
        """After every plan update: start, update or end each phone's Live Activity, and
        send a notification when charging starts, finishes or fails."""
        if not self.configured:
            return
        async with self._lock:
            await self._vehicle_update(entry_id, name, data, power_kw, kind)

    async def _vehicle_update(self, entry_id, name, data, power_kw, kind) -> None:
        status = data.get("status") or ""
        active = status in ACTIVE
        now = time.time()
        changed = False
        for device_id, device in list(self.devices.items()):
            entries = device.get("entries")
            if entries is not None and entry_id not in entries:
                continue
            language = device.get("language")
            memo = self._memo.setdefault((device_id, entry_id), {"first": True})
            if status in SETTLING and memo.get("active"):
                continue
            state = content_state(data, entry_id, language, power_kw, kind)
            token = (device.get("activities") or {}).get(entry_id)
            starts_now = (
                status in STARTS
                and power_kw >= FLOWING_KW
                and device.get("start_token")
                and not memo.get("started")
            )
            if active and (token or memo.get("started") or starts_now):
                _keep_activity_start(state, memo)
            try:
                if active and token:
                    fingerprint = _fingerprint(state)
                    if fingerprint != memo.get("fingerprint") or now - memo.get("at", 0) >= REFRESH:
                        result = await self._send(
                            token,
                            "liveactivity",
                            {
                                "aps": {
                                    "timestamp": int(now),
                                    "event": "update",
                                    "content-state": state,
                                    "stale-date": int(now + 20 * 60),
                                }
                            },
                            # A change is shown at once, also on a locked phone (the app has
                            # Apple's frequent-updates budget); the five-minute refresh can wait.
                            10 if fingerprint != memo.get("fingerprint") else 5,
                        )
                        memo.update(fingerprint=fingerprint, at=now)
                        changed |= self._forget(result, device, entry_id)
                elif starts_now:
                    # Once per charge: also when the phone never reports the new activity's
                    # token, a second start would only add a second Live Activity.
                    memo.update(started=True, fingerprint=_fingerprint(state), at=now)
                    texts = _texts(language)
                    result = await self._send(
                        device["start_token"],
                        "liveactivity",
                        {
                            "aps": {
                                "timestamp": int(now),
                                "event": "start",
                                "content-state": state,
                                "attributes-type": "ChargeActivityAttributes",
                                "attributes": {
                                    "vehicle": entry_id,
                                    "name": name,
                                    "scooter": kind != "car",
                                },
                                "alert": {
                                    "title": texts["started_title"].format(name=name),
                                    "body": state["headline"],
                                },
                                "stale-date": int(now + 20 * 60),
                            }
                        },
                    )
                    if result in (400, 410):
                        device.pop("start_token", None)
                        changed = True
                elif not active and token and memo.get("active"):
                    result = await self._send(
                        token,
                        "liveactivity",
                        {
                            "aps": {
                                "timestamp": int(now),
                                "event": "end",
                                "content-state": state,
                                "dismissal-date": int(now + DISMISS_AFTER),
                            }
                        },
                    )
                    device.get("activities", {}).pop(entry_id, None)
                    changed = True
                await self._notify(device, memo, status, name, data, language, entry_id, kind)
            except Exception as err:  # noqa: BLE001 - a phone that can't be reached never stops charging
                _LOGGER.debug("Push to %s failed: %s", device_id, err)
            if not active:
                memo["started"] = False  # the next charge may start one again
                memo.pop("activity_start", None)
            memo["active"], memo["status"], memo["first"] = active, status, False
        if changed:
            self._save()

    def _wants(self, device: dict, kind: str) -> bool:
        """Whether the phone wants this kind of notification (all, when it didn't say)."""
        types = device.get("alert_types")
        return types is None or kind in types

    async def _alert(self, device, kind, alert, entry_id, category=None) -> None:
        aps: dict[str, Any] = {"alert": alert, "sound": "default"}
        if category:
            aps["category"] = category  # the app shows its buttons for it
        result = await self._send(
            device["device_token"], "alert", {"aps": aps, "entry_id": entry_id, "kind": kind}
        )
        if result in (400, 410):
            device.pop("device_token", None)
            self._save()

    async def _notify(self, device, memo, status, name, data, language, entry_id, kind) -> None:
        """Notifications on a change: plugged in (with the plan and buttons to change it),
        charging started, finished, failing, interrupted, not reachable, a target that
        won't be reached, and once before a charging block with nothing plugged in."""
        now = dt_util.utcnow()
        connected = data.get("car_connected")
        was_connected = memo.get("connected")
        was_active = memo.get("active")
        previous = memo.get("status")
        # A charger that doesn't answer, or can't be reached: only told once it lasts. The
        # Wallbox cloud drops out for seconds now and then, and that is no problem.
        unreachable = data.get("error_code") == "input_unavailable" or status == "control_error"
        if unreachable:
            memo.setdefault("unreachable_since", now)
        else:
            memo.pop("unreachable_since", None)
            memo.pop("unreachable_told", None)
        memo["connected"] = connected
        if status in ACTIVE:
            memo.pop("done_told", None)  # a new charge may finish again
        # Out of reach for real: the time is too short, or prices are known to the ready-by
        # time and still don't cover it. Not while prices or the forecast are missing.
        short = status == "insufficient_time" or (
            (data.get("shortfall_kwh") or 0) > 0.1 and data.get("coverage_complete") is True
        )
        if short:
            memo.setdefault("short_since", now)
        else:
            memo.pop("short_since", None)
        if (
            not device.get("alerts")
            or not device.get("device_token")
            or memo.get("first")
            or time.time() - self._loaded_at < QUIET_AFTER_START
        ):
            return
        texts = _texts(language)
        soc, target = (
            _percent(data.get("estimated_soc", data.get("measured_soc"))),
            _percent(data.get("target_percentage")),
        )
        deadline = dt_util.parse_datetime(data["deadline"]) if data.get("deadline") else None
        cheap = data.get("planning_method") in ("cheap_only", "cheap_after_deadline")
        if self._wants(device, "unplugged"):
            await self._warn_unplugged(device, memo, name, data, language, entry_id)
        if connected is True and was_connected is False and self._wants(device, "plugged_in"):
            if cheap and data.get("cheap_price_eur_kwh") is not None:
                body = texts["plugged_cheap"].format(
                    target=target, price=f"€{data['cheap_price_eur_kwh']:.2f}".replace(".", ",")
                )
            elif deadline and deadline > now:
                body = texts["plugged_deadline"].format(
                    when=_when(deadline, texts, now), target=target
                )
            else:
                body = texts["plugged_none"].format(target=target)
            await self._alert(
                device,
                "plugged_in",
                {"title": texts["plugged_title"].format(name=name), "body": body},
                entry_id,
                "PLAN",
            )
        if (
            was_active
            and connected is False
            and was_connected is not False
            and status != "target_reached"
            and self._wants(device, "interrupted")
        ):
            await self._alert(
                device,
                "interrupted",
                {
                    "title": texts["interrupted_title"].format(name=name),
                    "body": texts["interrupted_body"].format(soc=soc, target=target),
                },
                entry_id,
            )
        short_since = memo.get("short_since")
        if (
            short_since
            and (now - short_since).total_seconds() >= SHORTFALL_AFTER
            and deadline
            and connected is not False
            and memo.get("short_for") != data["deadline"]
            and self._wants(device, "shortfall")
        ):
            memo["short_for"] = data["deadline"]
            await self._alert(
                device,
                "shortfall",
                {
                    "title": texts["shortfall_title"].format(name=name, target=target),
                    "body": texts["shortfall_body"].format(when=_when(deadline, texts, now)),
                },
                entry_id,
                "PLAN",
            )
        since = memo.get("unreachable_since")
        if (
            since
            and (now - since).total_seconds() >= UNREACHABLE_AFTER
            and not memo.get("unreachable_told")
            and self._wants(device, "problem")
        ):
            memo["unreachable_told"] = True
            await self._alert(
                device,
                "problem",
                {
                    "title": texts["unreachable_title"].format(
                        name=name, device=texts["charger" if kind == "car" else "plug"]
                    ),
                    "body": texts["unreachable_body"],
                },
                entry_id,
            )
        if status == previous:
            return
        if status == "charging" and previous not in ACTIVE and self._wants(device, "started"):
            if device.get("start_token"):
                return  # the Live Activity's start already says so, with its own alert
            alert = {
                "title": texts["started_title"].format(name=name),
                "body": texts["started_body"].format(target=target, soc=soc),
            }
            await self._alert(device, "started", alert, entry_id)
        elif (
            status == "target_reached" and not memo.get("done_told") and self._wants(device, "done")
        ):
            # Once per charge: the plan returns to "target reached" after every blip.
            memo["done_told"] = True
            alert = {
                "title": texts["done_title"].format(name=name),
                "body": texts["done_body"].format(soc=soc),
            }
            await self._alert(device, "done", alert, entry_id)

    async def _warn_unplugged(self, device, memo, name, data, language, entry_id) -> None:
        now = dt_util.utcnow()
        window = _window(data, now)
        if not data.get("enabled") or window is None or window[0] <= now:
            return
        if (window[0] - now).total_seconds() > UNPLUGGED_WARNING:
            return
        away = (data.get("bike") or {}).get("state") in ("away", "departing")
        if data.get("car_connected") is not False and not away:
            return
        if memo.get("warned") == window[0].isoformat():
            return
        memo["warned"] = window[0].isoformat()
        texts = _texts(language)
        start = window[0].astimezone(dt_util.get_default_time_zone()).strftime("%H:%M")
        alert = {
            "title": texts["away_title" if away else "unplugged_title"].format(name=name),
            "body": texts["unplugged_body"].format(time=start),
        }
        await self._alert(device, "unplugged", alert, entry_id)

    @staticmethod
    def _forget(result: int, device: dict, entry_id: str) -> bool:
        """A Live Activity the phone no longer has: stop sending to it."""
        if result in (400, 410):
            device.get("activities", {}).pop(entry_id, None)
            return True
        return False


def http2_client(hass: HomeAssistant):
    """An HTTP/2 client with an SSL context of its own. Turning HTTP/2 on with the shared
    HTTP/1.1 context (http2=True) makes httpx advertise HTTP/2 on the context every other
    integration's aiohttp connections use, after which their requests fail with "Server
    disconnected" or "Bad status line"."""
    from homeassistant.helpers.httpx_client import create_async_httpx_client
    from homeassistant.util.ssl import SSL_ALPN_HTTP11_HTTP2

    return create_async_httpx_client(hass, alpn_protocols=SSL_ALPN_HTTP11_HTTP2)


def push_hub(hass: HomeAssistant) -> PushHub:
    hub = hass.data.get(f"{DOMAIN}_push")
    if hub is None:
        hub = hass.data[f"{DOMAIN}_push"] = PushHub(hass)
    return hub
