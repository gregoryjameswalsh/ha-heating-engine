"""The resolver: plan section 8.1 and 8.2.

For each room, the first rule that applies wins:

  1  active boost                       -> boost temp
  2  manual change at the valve / app   -> that temp, until the next schedule change
  4  holiday                            -> empty
  5  occupants away                     -> empty
  7  inside a warm block                -> block temp
  9  otherwise                          -> eco

Rules 3, 6 and 8 are Phase 3 and are not here. `resolve_plan` is a pure
function of (config, window, calendar events, runtime state). The same function
drives the live setpoint and the look-ahead, and it is the only place reason
text is written, so every surface describes the same moment the same way.
"""
from __future__ import annotations

from datetime import datetime, timedelta, tzinfo

from .model import Block, CalendarEvent, Config, EngineState, ManualOverride, Room, Segment
from .occupancy import (
    Interval,
    contains,
    edges_between,
    merge_intervals,
    room_occupied_intervals,
)
from .timeutil import (
    date_range,
    day_key,
    format_day_until,
    format_temp,
    format_time_until,
    join_names,
    local_at,
    local_date,
    local_midnight,
    to_utc,
)

RULE_BOOST = 1
RULE_MANUAL = 2
RULE_HOLIDAY = 4
RULE_AWAY = 5
RULE_WARM = 7
RULE_ECO = 9

CODE_BOOST = "boost"
CODE_MANUAL = "manual"
CODE_HOLIDAY = "holiday"
CODE_AWAY = "away"
CODE_WARM = "warm"
CODE_ECO = "eco"

# How far past the requested window the resolver looks so that an 'until' can be
# found for a stretch that runs off the end of the window.
LOOKAHEAD = timedelta(days=15)


class _Piece:
    """A candidate answer at one instant, before merging."""

    __slots__ = ("temp", "rule", "code", "until", "extra")

    def __init__(self, temp: float, rule: int, code: str, until: datetime | None, extra: float | None = None):
        self.temp = temp
        self.rule = rule
        self.code = code
        self.until = until  # UTC, or None when it runs past the look-ahead
        self.extra = extra  # manual: the schedule's own temp at `since`, for up/down wording

    def same_as(self, other: "_Piece") -> bool:
        return self.rule == other.rule and self.code == other.code and self.temp == other.temp


def holiday_intervals(config: Config, tz: tzinfo) -> list[Interval]:
    out: list[Interval] = []
    for h in config.holidays:
        s = to_utc(local_midnight(h.start, tz))
        e = to_utc(local_midnight(h.end, tz)) - timedelta(hours=max(0.0, h.preheat_hours))
        if e > s:
            out.append((s, e))
    return merge_intervals(out)


def block_at(room: Room, t: datetime, tz: tzinfo) -> tuple[Block, datetime, datetime] | None:
    """The warm block covering UTC instant `t`, with its UTC bounds. Blocks never
    cross midnight (validated), so only t's own local date needs checking."""
    d = local_date(t, tz)
    for b in room.blocks_for(day_key(d)):
        s = to_utc(local_at(d, b.start, tz))
        e = to_utc(local_at(d, b.end, tz))
        if s <= t < e:
            return b, s, e
    return None


def _base_boundaries(room: Room, lo: datetime, hi: datetime, tz: tzinfo, occ: list[Interval], hol: list[Interval]) -> list[datetime]:
    """Every instant in (lo, hi) at which rules 4, 5, 7 or 9 could change."""
    pts: set[datetime] = set()
    for d in date_range(local_date(lo, tz) - timedelta(days=1), local_date(hi, tz) + timedelta(days=1)):
        for b in room.blocks_for(day_key(d)):
            for x in (to_utc(local_at(d, b.start, tz)), to_utc(local_at(d, b.end, tz))):
                if lo < x < hi:
                    pts.add(x)
    pts.update(edges_between(occ, lo, hi))
    pts.update(edges_between(hol, lo, hi))
    return sorted(pts)


def _base_at(room: Room, t: datetime, tz: tzinfo, occ: list[Interval], hol: list[Interval]) -> tuple[float, int, str]:
    if contains(hol, t):
        return room.temps.empty, RULE_HOLIDAY, CODE_HOLIDAY
    if not contains(occ, t):
        return room.temps.empty, RULE_AWAY, CODE_AWAY
    hit = block_at(room, t, tz)
    if hit is not None:
        return hit[0].temp, RULE_WARM, CODE_WARM
    return room.temps.eco, RULE_ECO, CODE_ECO


def _base_segments(room: Room, lo: datetime, hi: datetime, tz: tzinfo, occ: list[Interval], hol: list[Interval]) -> list[tuple[datetime, datetime, float, int, str]]:
    """Rules 4/5/7/9 only, over [lo, hi), merged where consecutive answers agree."""
    pts = [lo] + _base_boundaries(room, lo, hi, tz, occ, hol) + [hi]
    out: list[tuple[datetime, datetime, float, int, str]] = []
    for s, e in zip(pts, pts[1:]):
        if e <= s:
            continue
        temp, rule, code = _base_at(room, s, tz, occ, hol)
        if out and out[-1][3] == rule and out[-1][2] == temp and out[-1][1] == s:
            prev = out[-1]
            out[-1] = (prev[0], e, prev[2], prev[3], prev[4])
        else:
            out.append((s, e, temp, rule, code))
    return out


def override_end(
    config: Config,
    room: Room,
    override: ManualOverride,
    calendar_events: dict[str, list[CalendarEvent]],
    tz: tzinfo,
) -> tuple[datetime, float]:
    """When a manual override (rule 2) stops: the end of the schedule stretch
    that was in force at `since`. Also returns that stretch's own temp so the
    reason text can say turned up or down. UTC."""
    since = to_utc(override.since)
    hi = since + LOOKAHEAD
    occ = room_occupied_intervals(config, room, since, hi, tz, calendar_events)
    hol = holiday_intervals(config, tz)
    for s, e, temp, _rule, _code in _base_segments(room, since, hi, tz, occ, hol):
        if s <= since < e:
            return e, temp
    return hi, room.temps.eco  # unreachable in practice: the first segment starts at `since`


def _reason_text(config: Config, room: Room, piece: _Piece, seg_start: datetime, tz: tzinfo) -> str:
    until = piece.until
    if piece.code == CODE_BOOST:
        # a boost always has a known end
        return f"Boosted to {format_temp(piece.temp)} until {format_time_until(seg_start, until, tz)}" if until else f"Boosted to {format_temp(piece.temp)}"
    if piece.code == CODE_MANUAL:
        base = piece.extra
        if base is None or piece.temp == base:
            verb = "Changed"
        elif piece.temp > base:
            verb = "Turned up"
        else:
            verb = "Turned down"
        if until:
            return f"{verb} at the radiator. Back to schedule at {format_time_until(seg_start, until, tz)}"
        return f"{verb} at the radiator"
    if piece.code == CODE_HOLIDAY:
        return f"Holiday until {format_day_until(seg_start, until, tz)}" if until else "Holiday"
    if piece.code == CODE_AWAY:
        names = [p.name for p in (config.person(pid) for pid in room.occupants) if p is not None]
        plural = len(names) > 1 or any("&" in n for n in names)
        who = f"{join_names(names)} {'are' if plural else 'is'} away" if names else "Nobody is using this room"
        if until:
            return f"Empty until {format_day_until(seg_start, until, tz)}. {who}"
        return f"Empty. {who}"
    if piece.code == CODE_WARM:
        return f"Warm until {format_time_until(seg_start, until, tz)}" if until else "Warm"
    return f"Eco until {format_time_until(seg_start, until, tz)}" if until else "Eco"


def _resolve_room(
    config: Config,
    room: Room,
    ws: datetime,
    we: datetime,
    tz: tzinfo,
    calendar_events: dict[str, list[CalendarEvent]],
    state: EngineState,
) -> list[Segment]:
    far = we + LOOKAHEAD
    hol = holiday_intervals(config, tz)

    boost = state.boosts.get(room.id)
    override = state.overrides.get(room.id)

    lo = ws
    if override is not None:
        lo = min(lo, to_utc(override.since))
    if boost is not None and boost.start is not None:
        lo = min(lo, to_utc(boost.start))

    occ = room_occupied_intervals(config, room, lo, far, tz, calendar_events)
    base = _base_segments(room, lo, far, tz, occ, hol)

    # rule 2 window: from `since` to the end of the schedule stretch in force then
    ov_iv: tuple[datetime, datetime] | None = None
    ov_base_temp: float | None = None
    if override is not None:
        since = to_utc(override.since)
        for s, e, temp, _r, _c in base:
            if s <= since < e:
                ov_iv = (since, e)
                ov_base_temp = temp
                break

    # rule 1 window
    bo_iv: tuple[datetime, datetime] | None = None
    if boost is not None:
        bs = to_utc(boost.start) if boost.start is not None else lo
        be = to_utc(boost.until)
        if be > bs:
            bo_iv = (bs, be)

    pts: set[datetime] = {ws, far}
    for s, e, _t, _r, _c in base:
        for x in (s, e):
            if ws < x < far:
                pts.add(x)
    for iv in (ov_iv, bo_iv):
        if iv is not None:
            for x in iv:
                if ws < x < far:
                    pts.add(x)
    ordered = sorted(pts)

    def piece_at(t: datetime) -> _Piece:
        if bo_iv is not None and bo_iv[0] <= t < bo_iv[1]:
            return _Piece(boost.temp, RULE_BOOST, CODE_BOOST, bo_iv[1])  # type: ignore[union-attr]
        if ov_iv is not None and ov_iv[0] <= t < ov_iv[1]:
            return _Piece(override.temp, RULE_MANUAL, CODE_MANUAL, ov_iv[1], ov_base_temp)  # type: ignore[union-attr]
        for s, e, temp, rule, code in base:
            if s <= t < e:
                return _Piece(temp, rule, code, None if e >= far else e)
        return _Piece(room.temps.eco, RULE_ECO, CODE_ECO, None)

    merged: list[tuple[datetime, datetime, _Piece]] = []
    for s, e in zip(ordered, ordered[1:]):
        if e <= s:
            continue
        p = piece_at(s)
        if merged and merged[-1][2].same_as(p) and merged[-1][1] == s:
            ps, _pe, pp = merged[-1]
            merged[-1] = (ps, e, pp)
        else:
            merged.append((s, e, p))

    out: list[Segment] = []
    for s, e, p in merged:
        if s >= we:
            break
        true_end: datetime | None = None if e >= far else e
        if p.code in (CODE_BOOST, CODE_MANUAL):
            true_end = p.until  # these two always know their own end
        text = _reason_text(config, room, _Piece(p.temp, p.rule, p.code, true_end, p.extra), s, tz)
        out.append(
            Segment(
                room=room.id,
                start=s.astimezone(tz),
                end=min(e, we).astimezone(tz),
                temp=p.temp,
                rule=p.rule,
                reason_code=p.code,
                reason_text=text,
                until=true_end.astimezone(tz) if true_end else None,
            )
        )
    return out


def resolve_plan(
    config: Config,
    start: datetime,
    end: datetime,
    calendar_events: dict[str, list[CalendarEvent]] | None = None,
    state: EngineState | None = None,
) -> list[Segment]:
    """Segments for every room over [start, end). `start` and `end` must be
    aware; the engine's zone is `start.tzinfo`. Rooms come back in config order,
    each room's segments contiguous and in time order."""
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be aware datetimes")
    tz = start.tzinfo
    ws, we = to_utc(start), to_utc(end)
    if we <= ws:
        raise ValueError("end must be after start")
    events = calendar_events or {}
    st = state or EngineState()
    out: list[Segment] = []
    for room in config.rooms:
        out.extend(_resolve_room(config, room, ws, we, tz, events, st))
    return out


def resolve_now(
    config: Config,
    now: datetime,
    calendar_events: dict[str, list[CalendarEvent]] | None = None,
    state: EngineState | None = None,
) -> dict[str, Segment]:
    """The segment in force right now for each room, keyed by room id."""
    segs = resolve_plan(config, now, now + timedelta(minutes=1), calendar_events, state)
    out: dict[str, Segment] = {}
    for s in segs:
        if s.room not in out:
            out[s.room] = s
    return out
