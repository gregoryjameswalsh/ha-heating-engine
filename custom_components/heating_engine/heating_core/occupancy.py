"""From "nights" to occupied time.

A person who sleeps here on night D occupies their room from `arrive` on day D
until `leave` on day D+1. Consecutive nights merge into one stay. A calendar
person is present except during an away event, which ends `return_preheat_hours`
early so the room is warm when they walk in.

Intervals are (start, end) pairs of UTC-aware datetimes, half-open [start, end).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone, tzinfo

from .model import CalendarEvent, Config, Person, Room
from .rota import SOURCE_CALENDAR, sleeps_here
from .timeutil import date_range, local_at, local_date, to_utc

Interval = tuple[datetime, datetime]

FAR_PAST = datetime(1970, 1, 1, tzinfo=timezone.utc)
FAR_FUTURE = datetime(2200, 1, 1, tzinfo=timezone.utc)
ALWAYS: list[Interval] = [(FAR_PAST, FAR_FUTURE)]


def merge_intervals(intervals: list[Interval]) -> list[Interval]:
    """Sort and merge overlapping or touching intervals. Drops empty ones."""
    out: list[Interval] = []
    for s, e in sorted((s, e) for s, e in intervals if e > s):
        if out and s <= out[-1][1]:
            if e > out[-1][1]:
                out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return out


def complement(intervals: list[Interval]) -> list[Interval]:
    """Everything between FAR_PAST and FAR_FUTURE not covered by `intervals`."""
    out: list[Interval] = []
    cursor = FAR_PAST
    for s, e in merge_intervals(intervals):
        if s > cursor:
            out.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < FAR_FUTURE:
        out.append((cursor, FAR_FUTURE))
    return out


def union(groups: list[list[Interval]]) -> list[Interval]:
    flat: list[Interval] = []
    for g in groups:
        flat.extend(g)
    return merge_intervals(flat)


def contains(intervals: list[Interval], t: datetime) -> bool:
    for s, e in intervals:
        if s <= t < e:
            return True
        if s > t:
            break
    return False


def edges_between(intervals: list[Interval], lo: datetime, hi: datetime) -> list[datetime]:
    """Every start or end strictly inside (lo, hi)."""
    out: list[datetime] = []
    for s, e in intervals:
        for x in (s, e):
            if lo < x < hi:
                out.append(x)
    return out


def next_edge(intervals: list[Interval], t: datetime) -> datetime | None:
    """The first start or end strictly after `t`, or None."""
    best: datetime | None = None
    for s, e in intervals:
        for x in (s, e):
            if x > t and (best is None or x < best):
                best = x
    return best


def away_intervals_for(person: Person, events: list[CalendarEvent]) -> list[Interval]:
    """Away stretches from a calendar person's events, each shortened by the
    pre-heat so the room warms before they return."""
    if person.event_means != "away":
        return []
    preheat = timedelta(hours=max(0.0, person.return_preheat_hours))
    out: list[Interval] = []
    for ev in events:
        s = to_utc(ev.start)
        e = to_utc(ev.end) - preheat
        if e > s:
            out.append((s, e))
    return merge_intervals(out)


def person_present_intervals(
    config: Config,
    person: Person,
    window_start: datetime,
    window_end: datetime,
    tz: tzinfo,
    calendar_events: dict[str, list[CalendarEvent]],
) -> list[Interval]:
    """When `person` is in the house, covering at least [window_start, window_end)."""
    if person.kind == "calendar":
        events = calendar_events.get(person.calendar or "", [])
        return complement(away_intervals_for(person, events))

    # rota / always: a run of consecutive "here" nights is ONE stay, from the
    # arrival on its first night to the departure on the morning after its last.
    # So someone here Monday and Tuesday nights is also here on Tuesday daytime;
    # the daytime gap only appears before a night they are away.
    first = local_date(window_start, tz) - timedelta(days=2)
    last = local_date(window_end, tz) + timedelta(days=1)
    stays: list[Interval] = []
    run_start: date | None = None
    prev_night: date | None = None

    def close_run(first_night: date, last_night: date) -> None:
        s = to_utc(local_at(first_night, person.arrive, tz))
        e = to_utc(local_at(last_night + timedelta(days=1), person.leave, tz))
        if e > s:
            stays.append((s, e))

    for night in date_range(first, last):
        here, _ = sleeps_here(config, person, night)
        if here and run_start is None:
            run_start = night
        elif not here and run_start is not None:
            close_run(run_start, prev_night)  # type: ignore[arg-type]
            run_start = None
        prev_night = night
    if run_start is not None:
        close_run(run_start, last)
    return merge_intervals(stays)


def room_occupied_intervals(
    config: Config,
    room: Room,
    window_start: datetime,
    window_end: datetime,
    tz: tzinfo,
    calendar_events: dict[str, list[CalendarEvent]],
) -> list[Interval]:
    """When the room counts as in use: any occupant present, or always if none listed."""
    if not room.occupants:
        return list(ALWAYS)
    groups: list[list[Interval]] = []
    for pid in room.occupants:
        person = config.person(pid)
        if person is None:
            continue  # validate_config rejects this; skip rather than crash
        groups.append(person_present_intervals(config, person, window_start, window_end, tz, calendar_events))
    return union(groups)


def person_here_tonight(
    config: Config,
    person: Person,
    night: date,
    tz: tzinfo,
    calendar_events: dict[str, list[CalendarEvent]],
    max_lookahead_nights: int = 60,
) -> tuple[bool, str, date | None]:
    """For the per-person 'here' sensor: (here tonight, source, first night that
    differs). Source is 'rota', 'always', 'exception' or 'calendar'."""
    if person.kind == "calendar":
        events = calendar_events.get(person.calendar or "", [])
        away = away_intervals_for(person, events)

        def here_on(d: date) -> bool:
            probe = to_utc(local_at(d, "22:00", tz))
            return not contains(away, probe)

        here = here_on(night)
        until: date | None = None
        for i in range(1, max_lookahead_nights + 1):
            d = night + timedelta(days=i)
            if here_on(d) != here:
                until = d
                break
        return here, SOURCE_CALENDAR, until

    here, source = sleeps_here(config, person, night)
    until = None
    for i in range(1, max_lookahead_nights + 1):
        d = night + timedelta(days=i)
        h, _ = sleeps_here(config, person, d)
        if h != here:
            until = d
            break
    return here, source, until
