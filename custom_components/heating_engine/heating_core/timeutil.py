"""Wall-clock helpers.

Two rules keep clock changes honest:

1. Every boundary (a block start, an arrival, a holiday edge) is built from a
   local date plus an HH:MM wall time in the configured zone, never by adding
   hours to an earlier instant. So 06:30 is 06:30 on both clock-change days.
2. Every comparison and sort happens on UTC instants. Python compares two
   aware datetimes that share one tzinfo object by their naive wall time, which
   is wrong for the repeated hour in autumn. Converting to UTC first avoids it.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo

from .model import DAYS

UTC = timezone.utc


def parse_hhmm(value: str) -> tuple[int, int]:
    """'HH:MM' -> (hour, minute). '24:00' is allowed as an end-of-day marker."""
    parts = str(value).split(":")
    if len(parts) != 2:
        raise ValueError(f"not a time: {value!r}")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= m < 60) or not (0 <= h <= 24) or (h == 24 and m != 0):
        raise ValueError(f"not a time: {value!r}")
    return h, m


def minutes_of(value: str) -> int:
    h, m = parse_hhmm(value)
    return h * 60 + m


def local_at(d: date, hhmm: str, tz: tzinfo) -> datetime:
    """Aware local datetime for wall time `hhmm` on local date `d`."""
    h, m = parse_hhmm(hhmm)
    if h == 24:
        return datetime.combine(d + timedelta(days=1), time(0, 0), tzinfo=tz)
    return datetime.combine(d, time(h, m), tzinfo=tz)


def local_midnight(d: date, tz: tzinfo) -> datetime:
    return datetime.combine(d, time(0, 0), tzinfo=tz)


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime; the engine only accepts aware datetimes")
    return dt.astimezone(UTC)


def to_local(dt: datetime, tz: tzinfo) -> datetime:
    return dt.astimezone(tz)


def local_date(dt: datetime, tz: tzinfo) -> date:
    return dt.astimezone(tz).date()


def day_key(d: date) -> str:
    """Local date -> 'mon'..'sun'."""
    return DAYS[d.weekday()]


def date_range(start: date, end_inclusive: date):
    d = start
    while d <= end_inclusive:
        yield d
        d += timedelta(days=1)


def format_temp(temp: float) -> str:
    if float(temp).is_integer():
        return f"{int(temp)}°"
    return f"{temp:g}°"


def format_time_until(ref: datetime, until: datetime, tz: tzinfo) -> str:
    """Time-grained 'until' for texts like 'Warm until 21:00'.

    Same local day -> '21:00'; the next day -> '06:30 tomorrow';
    within a week -> 'Fri 17:00'; further out -> 'Fri 27 Nov'.
    """
    r = ref.astimezone(tz)
    u = until.astimezone(tz)
    days = (u.date() - r.date()).days
    hhmm = u.strftime("%H:%M")
    if days == 0:
        return hhmm
    if days == 1:
        return f"{hhmm} tomorrow"
    if days < 7:
        return f"{u.strftime('%a')} {hhmm}"
    return f"{u.strftime('%a')} {u.day} {u.strftime('%b')}"


def format_day_until(ref: datetime, until: datetime, tz: tzinfo) -> str:
    """Day-grained 'until' for texts like 'Empty until Friday'.

    Same local day -> '15:00'; next day -> 'tomorrow'; within a week ->
    'Friday'; further out -> 'Sat 20 Feb'.
    """
    r = ref.astimezone(tz)
    u = until.astimezone(tz)
    days = (u.date() - r.date()).days
    if days == 0:
        return u.strftime("%H:%M")
    if days == 1:
        return "tomorrow"
    if days < 7:
        return u.strftime("%A")
    return f"{u.strftime('%a')} {u.day} {u.strftime('%b')}"


def join_names(names: list[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]
