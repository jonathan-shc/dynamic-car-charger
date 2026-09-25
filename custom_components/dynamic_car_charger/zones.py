"""Bidding zones the price forecast can learn: market, holidays and weather.

No Home Assistant dependencies. Market prices come from Energy-Charts for the
zone's code. The weather points are fixed places whose wind, sun and
temperature drive that market; they are not the user's location.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from functools import cache
from zoneinfo import ZoneInfo

WEATHER_POINTS = {
    "nl_offshore": (53.5, 4.5),
    "nl_onshore": (52.9, 5.9),
    "de_north": (54.0, 9.0),
    "de_central": (51.0, 10.0),
    "de_south": (48.5, 11.0),
    "de_baltic": (54.5, 13.0),
    "be_offshore": (51.6, 2.8),
    "be_inland": (50.6, 4.7),
    "fr_north": (49.8, 2.5),
    "fr_west": (47.5, -2.5),
    "fr_central": (46.5, 2.5),
    "fr_south": (43.8, 4.5),
    "at_east": (48.0, 16.5),
    "at_west": (47.3, 11.5),
    "ch_plateau": (47.0, 7.5),
    "ch_alps": (46.5, 9.0),
    "pl_north": (54.3, 18.0),
    "pl_central": (52.0, 19.5),
    "pl_south": (50.3, 19.0),
    "dk_west": (56.0, 8.5),
    "dk_offshore": (55.5, 7.5),
    "dk_east": (55.5, 12.0),
    "se_south": (55.8, 13.5),
    "se_central": (59.3, 15.0),
    "se_north": (63.0, 17.0),
    "fi_south": (60.8, 24.5),
    "fi_west": (63.5, 22.5),
    "fi_north": (66.0, 26.0),
}


def easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def _national(fixed: tuple[tuple[int, int], ...], after_easter: tuple[int, ...]):
    """Holidays on fixed dates plus days relative to Easter Sunday."""

    def days(year: int) -> set[date]:
        sunday = easter(year)
        return {date(year, month, day) for month, day in fixed} | {
            sunday + timedelta(days=offset) for offset in after_easter
        }

    return days


def _midsummer_eve(year: int) -> date:
    """The Friday between 19 and 25 June."""
    day = date(year, 6, 19)
    return day + timedelta(days=(4 - day.weekday()) % 7)


def dutch_holidays(year: int) -> set[date]:
    kings_day = date(year, 4, 27)
    if kings_day.weekday() == 6:
        kings_day = date(year, 4, 26)
    return _national(((1, 1), (12, 25), (12, 26)), (1, 39, 50))(year) | {kings_day}


def _nordic(fixed: tuple[tuple[int, int], ...], after_easter: tuple[int, ...]):
    base = _national(fixed, after_easter)
    return lambda year: base(year) | {_midsummer_eve(year)}


# Good Friday -2, Easter Monday +1, Ascension +39, Whit Monday +50, Corpus Christi +60.
HOLIDAYS: dict[str, Callable[[int], set[date]]] = {
    "NL": dutch_holidays,
    "BE": _national(((1, 1), (5, 1), (7, 21), (8, 15), (11, 1), (11, 11), (12, 25)), (1, 39, 50)),
    "DE": _national(((1, 1), (5, 1), (10, 3), (12, 25), (12, 26)), (-2, 1, 39, 50)),
    "FR": _national(
        ((1, 1), (5, 1), (5, 8), (7, 14), (8, 15), (11, 1), (11, 11), (12, 25)), (1, 39, 50)
    ),
    "AT": _national(
        ((1, 1), (1, 6), (5, 1), (8, 15), (10, 26), (11, 1), (12, 8), (12, 25), (12, 26)),
        (1, 39, 50, 60),
    ),
    "CH": _national(((1, 1), (8, 1), (12, 25), (12, 26)), (-2, 1, 39, 50)),
    "PL": _national(
        ((1, 1), (1, 6), (5, 1), (5, 3), (8, 15), (11, 1), (11, 11), (12, 24), (12, 25), (12, 26)),
        (1, 60),
    ),
    "DK": _national(((1, 1), (12, 24), (12, 25), (12, 26)), (-3, -2, 1, 39, 50)),
    "SE": _nordic(
        ((1, 1), (1, 6), (5, 1), (6, 6), (12, 24), (12, 25), (12, 26), (12, 31)), (-2, 1, 39)
    ),
    "FI": _nordic(((1, 1), (1, 6), (5, 1), (12, 6), (12, 24), (12, 25), (12, 26)), (-2, 1, 39)),
}


@dataclass(frozen=True)
class Zone:
    """A day-ahead bidding zone and the inputs that explain its prices."""

    code: str
    country: str
    time_zone: str
    wind: tuple[str, ...]
    solar: tuple[str, ...]
    temperature: tuple[str, ...]

    @property
    def points(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.wind + self.solar + self.temperature))

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.time_zone)

    def is_day_off(self, day: date) -> bool:
        """Weekends and public holidays behave alike on the power market."""
        return day.weekday() >= 5 or day in _holidays(self.country, day.year)


@cache
def _holidays(country: str, year: int) -> frozenset[date]:
    return frozenset(HOLIDAYS[country](year))


def _zone(code, country, time_zone, points, temperature=None) -> Zone:
    return Zone(code, country, time_zone, points, points, temperature or points[:2])


CET = "Europe/Amsterdam"  # the market day of every zone below except Finland
ZONES = {
    # The Dutch model was chosen with the backtest in tools/backtest.
    "NL": Zone(
        "NL",
        "NL",
        CET,
        wind=("nl_offshore", "nl_onshore", "de_north", "de_central"),
        solar=("nl_onshore", "de_central", "de_north"),
        temperature=("nl_onshore", "de_central"),
    ),
    "BE": _zone("BE", "BE", CET, ("be_offshore", "be_inland", "fr_north", "de_central")),
    "DE-LU": _zone("DE-LU", "DE", CET, ("de_north", "de_central", "de_south", "de_baltic")),
    "FR": _zone("FR", "FR", CET, ("fr_central", "fr_north", "fr_west", "fr_south")),
    "AT": _zone("AT", "AT", CET, ("at_east", "at_west", "de_south", "de_central")),
    "CH": _zone("CH", "CH", CET, ("ch_plateau", "ch_alps", "de_south", "fr_central")),
    "PL": _zone("PL", "PL", CET, ("pl_central", "pl_north", "pl_south", "de_baltic")),
    "DK1": _zone("DK1", "DK", CET, ("dk_west", "dk_offshore", "de_north", "se_south")),
    "DK2": _zone("DK2", "DK", CET, ("dk_east", "se_south", "de_baltic", "dk_west")),
    "SE3": _zone("SE3", "SE", CET, ("se_central", "se_north", "se_south", "fi_south")),
    "SE4": _zone("SE4", "SE", CET, ("se_south", "dk_east", "de_baltic", "se_central")),
    "FI": _zone("FI", "FI", "Europe/Helsinki", ("fi_south", "fi_west", "fi_north", "se_central")),
}
DEFAULT_ZONE = "NL"


def zone(code: str | None) -> Zone:
    """The zone for a code in any case, as stored in the options ("de-lu"); NL if unknown."""
    return ZONES.get((code or DEFAULT_ZONE).upper(), ZONES[DEFAULT_ZONE])
