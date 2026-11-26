"""The learning record: what each room's temperature does when its setpoint moves.

It acts on nothing. The engine feeds one `Sample` per room and per reading source
every time it evaluates; this module notices the start and end of a warm-up or a
cool-down, measures it, and keeps the result. Nothing here changes a target, a
block or a write. Later features (warm-by times, coast to finish) read the
summaries; this release only records and shows them.

Definitions (they match the investigation the record was designed from):

- A **warm-up** opens when the setpoint rises (or heating comes on) to at least
  `HEAT_MIN_GAP` above the reading. It closes when the reading is within
  `REACH_WITHIN` of the setpoint, when the setpoint or mode changes, or after
  `HEAT_MAX`. Its **lag** is the time until the reading has risen `LAG_RISE`; its
  **rate** is a least-squares slope from the lag point to the reach point, over
  at most `HEAT_RATE_SPAN`.
- A **cool-down** opens when the setpoint drops to at least `COOL_MIN_GAP` below
  the reading, or heating goes off. The first `COOL_SKIP` is skipped (that is the
  radiator going cold beside a valve's own sensor); the rate is the slope from
  then until `COOL_END`, stopping where the reading comes within `REACH_WITHIN` of
  the new setpoint. `k` is that rate over (inside minus outside), the Newton
  cooling constant, when the outside temperature is known.

An episode that is open when the engine restarts, or whose readings go missing
for more than `MAX_GAP`, is dropped, never guessed. An episode with no measurable
rate is not kept. Each episode remembers the entity it was read from, and a
summary only counts episodes from the source's current entity, so a room whose
sensor changes starts a fresh count without losing the old episodes (they age out
under the bound).

Readings are kept per source: `device` is the climate entity's own
`current_temperature` (for a radiator valve, the air beside the radiator), and
`sensor` is the room's `temp_sensor` when one is set. Recording both side by side
is what shows how far a valve's reading is from the room's.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from typing import Any

HEAT_MODE = "heat"
SOURCE_DEVICE = "device"
SOURCE_SENSOR = "sensor"
SOURCES = (SOURCE_DEVICE, SOURCE_SENSOR)
SOURCE_LABELS = {
    SOURCE_DEVICE: "The heating device's own reading",
    SOURCE_SENSOR: "The room sensor",
}

KIND_HEAT = "heat"
KIND_COOL = "cool"
KINDS = (KIND_HEAT, KIND_COOL)

END_REACHED = "reached"  # the reading came within REACH_WITHIN of the setpoint
END_CHANGED = "changed"  # the setpoint or mode changed
END_TIMEOUT = "timeout"  # HEAT_MAX or COOL_END passed
ENDS = (END_REACHED, END_CHANGED, END_TIMEOUT)

HEAT_MIN_GAP = 1.0  # degrees between setpoint and reading to count as a warm-up
COOL_MIN_GAP = 1.5  # degrees between reading and setpoint to count as a cool-down
REACH_WITHIN = 0.3
LAG_RISE = 0.3
HEAT_MAX = timedelta(hours=4)
HEAT_RATE_SPAN = timedelta(minutes=90)
COOL_SKIP = timedelta(minutes=45)
COOL_END = timedelta(minutes=165)
MAX_GAP = timedelta(minutes=10)  # readings missing for longer drop the open episode
MIN_RATE_POINTS = 3
MIN_HEAT_RATE_SPAN = timedelta(minutes=5)
MIN_COOL_RATE_SPAN = timedelta(minutes=30)
MIN_K_DIFFERENCE = 3.0  # inside minus outside below this gives no k (too noisy)
MIN_SAMPLES = 10  # an episode with fewer samples is kept but not counted
ENOUGH_EPISODES = 10  # a summary needs this many before it is "enough"
EPISODE_LIMIT = 60  # kept per room, per source, per kind
SETPOINT_EPSILON = 0.05

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass(frozen=True)
class Sample:
    """One reading of one room from one source, at one moment.

    `reading` is the temperature from the source; `setpoint` and `mode` are the
    climate entity's (the same for every source of a room). `outside` is the
    outside temperature when the engine has one, and `rooms_calling` how many
    rooms were asking for heat at that moment (the boiler is shared, so a busy
    house warms each room more slowly). Any of them may be None.
    """

    when: datetime  # aware
    reading: float | None
    setpoint: float | None
    mode: str | None
    outside: float | None = None
    rooms_calling: int | None = None


@dataclass(frozen=True)
class Episode:
    """A closed warm-up or cool-down."""

    kind: str  # KIND_HEAT or KIND_COOL
    entity: str  # where the readings came from
    started: datetime
    ended: datetime
    from_temp: float  # the reading when it opened
    target: float | None  # the setpoint it was heading for; None when heating went off
    outside: float | None  # outside temperature when it opened
    rooms_calling: int | None
    samples: int
    rate: float  # degrees per hour; negative while cooling
    end: str  # one of ENDS
    lag_min: float | None = None  # warm-up only
    reach_min: float | None = None  # warm-up only: None when the setpoint was not reached
    k: float | None = None  # cool-down only

    @property
    def usable(self) -> bool:
        return self.samples >= MIN_SAMPLES

    @property
    def reached(self) -> bool:
        return self.end == END_REACHED

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "entity": self.entity,
            "started": self.started.isoformat(),
            "ended": self.ended.isoformat(),
            "from": self.from_temp,
            "target": self.target,
            "outside": self.outside,
            "rooms_calling": self.rooms_calling,
            "samples": self.samples,
            "rate": self.rate,
            "end": self.end,
            "lag_min": self.lag_min,
            "reach_min": self.reach_min,
            "k": self.k,
            "usable": self.usable,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Episode:
        kind = str(d["kind"])
        end = str(d["end"])
        if kind not in KINDS or end not in ENDS:
            raise ValueError(f"unknown episode kind or end: {kind} {end}")
        return cls(
            kind=kind,
            entity=str(d["entity"]),
            started=datetime.fromisoformat(str(d["started"])),
            ended=datetime.fromisoformat(str(d["ended"])),
            from_temp=float(d["from"]),
            target=_float_or_none(d.get("target")),
            outside=_float_or_none(d.get("outside")),
            rooms_calling=int(d["rooms_calling"]) if d.get("rooms_calling") is not None else None,
            samples=int(d["samples"]),
            rate=float(d["rate"]),
            end=end,
            lag_min=_float_or_none(d.get("lag_min")),
            reach_min=_float_or_none(d.get("reach_min")),
            k=_float_or_none(d.get("k")),
        )


def _float_or_none(value: Any) -> float | None:
    return float(value) if value is not None else None


def _same_setpoint(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) < SETPOINT_EPSILON


def _minutes(delta: timedelta) -> float:
    return round(delta.total_seconds() / 60, 1)


def _slope_per_hour(points: list[tuple[datetime, float]]) -> float | None:
    """Least-squares slope of reading against time, in degrees per hour."""
    if len(points) < 2:
        return None
    t0 = points[0][0]
    xs = [(t - t0).total_seconds() / 3600 for t, _ in points]
    ys = [r for _, r in points]
    xm = sum(xs) / len(xs)
    ym = sum(ys) / len(ys)
    sxx = sum((x - xm) ** 2 for x in xs)
    if sxx <= 0:
        return None
    return sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / sxx


@dataclass
class _Open:
    kind: str
    entity: str
    started: datetime
    target: float | None
    outside: float | None
    rooms_calling: int | None
    points: list[tuple[datetime, float]] = field(default_factory=list)


class _Tracker:
    """Follows one room through one source: the episode in progress and the last
    setpoint seen. Lives in memory only, so a restart drops what was open."""

    def __init__(self, entity: str) -> None:
        self.entity = entity
        self.open: _Open | None = None
        self.prev_setpoint: float | None = None
        self.prev_mode: str | None = None
        self.last_good: datetime | None = None

    def feed(self, s: Sample) -> Episode | None:
        good = s.reading is not None and s.mode is not None and (s.setpoint is not None or s.mode != HEAT_MODE)
        if not good:
            if self.open is not None and self.last_good is not None and s.when - self.last_good > MAX_GAP:
                self.open = None
            return None
        assert s.reading is not None  # for the type checker; `good` says so
        if self.last_good is not None and s.when - self.last_good > MAX_GAP:
            # readings went missing: what was open is not trusted, and a setpoint
            # compared across the gap could open an episode at the wrong moment
            self.open = None
            self.prev_setpoint = None
            self.prev_mode = None
        self.last_good = s.when

        changed = self.prev_mode is not None and (
            s.mode != self.prev_mode or (s.mode == HEAT_MODE and not _same_setpoint(s.setpoint, self.prev_setpoint))
        )
        closed: Episode | None = None
        if self.open is not None:
            if changed:
                closed = self._close(s.when, END_CHANGED)
            else:
                self.open.points.append((s.when, s.reading))
                closed = self._check_end(s)
        if changed:
            self._maybe_open(s)
        self.prev_setpoint = s.setpoint
        self.prev_mode = s.mode
        return closed

    def _maybe_open(self, s: Sample) -> None:
        assert s.reading is not None
        prev_sp, prev_mode = self.prev_setpoint, self.prev_mode
        if s.mode == HEAT_MODE and s.setpoint is not None:
            rose = prev_mode != HEAT_MODE or prev_sp is None or s.setpoint > prev_sp
            if rose and s.setpoint - s.reading >= HEAT_MIN_GAP:
                self._start(KIND_HEAT, s, s.setpoint)
            elif not rose and s.reading - s.setpoint >= COOL_MIN_GAP:
                self._start(KIND_COOL, s, s.setpoint)
        elif prev_mode == HEAT_MODE and s.mode != HEAT_MODE:
            self._start(KIND_COOL, s, None)  # heating switched off: no floor to stop at

    def _start(self, kind: str, s: Sample, target: float | None) -> None:
        assert s.reading is not None
        self.open = _Open(
            kind=kind,
            entity=self.entity,
            started=s.when,
            target=target,
            outside=s.outside,
            rooms_calling=s.rooms_calling,
            points=[(s.when, s.reading)],
        )

    def _check_end(self, s: Sample) -> Episode | None:
        op = self.open
        assert op is not None and s.reading is not None
        elapsed = s.when - op.started
        if op.kind == KIND_HEAT:
            if op.target is not None and s.reading >= op.target - REACH_WITHIN:
                return self._close(s.when, END_REACHED)
            if elapsed >= HEAT_MAX:
                return self._close(s.when, END_TIMEOUT)
        else:
            if op.target is not None and s.reading <= op.target + REACH_WITHIN:
                return self._close(s.when, END_REACHED)
            if elapsed >= COOL_END:
                return self._close(s.when, END_TIMEOUT)
        return None

    def _close(self, when: datetime, end: str) -> Episode | None:
        op = self.open
        self.open = None
        if op is None:
            return None
        return measure_heat(op, when, end) if op.kind == KIND_HEAT else measure_cool(op, when, end)


def measure_heat(op: _Open, ended: datetime, end: str) -> Episode | None:
    """Lag, time to reach and rate of a closed warm-up; None when no rate can be measured."""
    points = op.points
    from_temp = points[0][1]
    lag_at = next((t for t, r in points if r >= from_temp + LAG_RISE), None)
    reach_at = None
    if op.target is not None:
        reach_at = next((t for t, r in points if r >= op.target - REACH_WITHIN), None)
    if lag_at is None:
        return None
    stop = lag_at + HEAT_RATE_SPAN
    if reach_at is not None and reach_at < stop:
        stop = reach_at
    window = [(t, r) for t, r in points if lag_at <= t <= stop]
    if len(window) < MIN_RATE_POINTS or window[-1][0] - window[0][0] < MIN_HEAT_RATE_SPAN:
        return None
    rate = _slope_per_hour(window)
    if rate is None:
        return None
    return Episode(
        kind=KIND_HEAT,
        entity=op.entity,
        started=op.started,
        ended=ended,
        from_temp=from_temp,
        target=op.target,
        outside=op.outside,
        rooms_calling=op.rooms_calling,
        samples=len(points),
        rate=round(rate, 2),
        end=end,
        lag_min=_minutes(lag_at - op.started),
        reach_min=_minutes(reach_at - op.started) if reach_at is not None else None,
    )


def measure_cool(op: _Open, ended: datetime, end: str) -> Episode | None:
    """Rate (and k, with an outside temperature) of a closed cool-down; None when
    no rate can be measured."""
    points = op.points
    from_temp = points[0][1]
    first = op.started + COOL_SKIP
    last = op.started + COOL_END
    window: list[tuple[datetime, float]] = []
    for t, r in points:
        if t < first:
            continue
        if t > last:
            break
        window.append((t, r))
        if op.target is not None and r <= op.target + REACH_WITHIN:
            break
    if len(window) < MIN_RATE_POINTS or window[-1][0] - window[0][0] < MIN_COOL_RATE_SPAN:
        return None
    rate = _slope_per_hour(window)
    if rate is None:
        return None
    k = None
    if op.outside is not None:
        inside = sum(r for _, r in window) / len(window)
        if inside - op.outside >= MIN_K_DIFFERENCE:
            k = round(-rate / (inside - op.outside), 4)
    return Episode(
        kind=KIND_COOL,
        entity=op.entity,
        started=op.started,
        ended=ended,
        from_temp=from_temp,
        target=op.target,
        outside=op.outside,
        rooms_calling=op.rooms_calling,
        samples=len(points),
        rate=round(rate, 2),
        end=end,
        k=k,
    )


# ---- summaries ----


def _quantile(values: list[float], q: float) -> float:
    """Linear-interpolated quantile of an already sorted, non-empty list."""
    pos = (len(values) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return values[lo]
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def _median(values: list[float], digits: int = 2) -> float | None:
    return round(_quantile(sorted(values), 0.5), digits) if values else None


def _day(dt: datetime, tz: tzinfo) -> str:
    local = dt.astimezone(tz)
    return f"{local.day} {_MONTHS[local.month - 1]}"


def _span_text(first: datetime, last: datetime, tz: tzinfo) -> str:
    a, b = _day(first, tz), _day(last, tz)
    return a if a == b else f"{a} to {b}"


def heat_rate_words(rate: float) -> str:
    """'about 1° every 95 minutes' for a slow room, 'about 7.5° an hour' for a fast one."""
    if rate <= 0:
        return "no measurable warming"
    if rate < 2:
        minutes = 60 / rate
        if minutes >= 120:
            hours = round(minutes / 30) / 2
            return f"about 1° every {hours:g} hours"
        return f"about 1° every {int(round(minutes / 5) * 5)} minutes"
    return f"about {round(rate * 2) / 2:g}° an hour"


def cool_rate_words(rate: float) -> str:
    loss = -rate
    if loss < 0.05:
        return "barely cools"
    return f"loses about {max(0.1, round(loss, 1)):g}° an hour"


def summarize(episodes: list[Episode], kind: str, tz: tzinfo) -> dict[str, Any]:
    """Counts, median and quartiles, the date range and plain words, for one kind
    of episode from one source. Only usable episodes are counted."""
    usable = sorted((e for e in episodes if e.kind == kind and e.usable), key=lambda e: e.started)
    n = len(usable)
    noun = "warm-ups" if kind == KIND_HEAT else "cool-downs"
    out: dict[str, Any] = {
        "count": n,
        "needed": ENOUGH_EPISODES,
        "enough": n >= ENOUGH_EPISODES,
        "rate": None,
        "first": None,
        "last": None,
    }
    if kind == KIND_HEAT:
        out["lag_min"] = None
        out["reach_share"] = None
    else:
        out["k"] = None
    if n == 0:
        out["text"] = f"No {noun} recorded yet."
        return out
    rates = sorted(e.rate for e in usable)
    out["rate"] = {"median": round(_quantile(rates, 0.5), 2), "q1": round(_quantile(rates, 0.25), 2), "q3": round(_quantile(rates, 0.75), 2)}
    out["first"] = usable[0].started.isoformat()
    out["last"] = usable[-1].started.isoformat()
    if kind == KIND_HEAT:
        out["lag_min"] = _median([e.lag_min for e in usable if e.lag_min is not None])
        out["reach_share"] = round(sum(1 for e in usable if e.reached) / n, 2)
    else:
        out["k"] = _median([e.k for e in usable if e.k is not None], 4)
    if n < ENOUGH_EPISODES:
        out["text"] = f"Not enough {noun} yet ({n} of {ENOUGH_EPISODES})."
        return out
    median = out["rate"]["median"]
    span = _span_text(usable[0].started, usable[-1].started, tz)
    if kind == KIND_HEAT:
        out["text"] = f"Warms {heat_rate_words(median)}, from {n} {noun} ({span})."
    else:
        out["text"] = f"{cool_rate_words(median).capitalize()} once the heating drops, from {n} {noun} ({span})."
    return out


# ---- the record for the whole house ----


class Learning:
    """Every room's episodes, per source, plus the trackers following each room.

    Episodes are kept per room, per source, per kind, the newest `limit`. The
    trackers (what is open right now) are never stored.
    """

    def __init__(self, limit: int = EPISODE_LIMIT) -> None:
        self.limit = limit
        self.episodes: dict[str, dict[str, list[Episode]]] = {}  # room -> source -> episodes, oldest first
        self._trackers: dict[tuple[str, str], _Tracker] = {}

    def feed(self, room: str, source: str, entity: str, sample: Sample) -> Episode | None:
        """Feed one sample; returns an episode when one has just closed and been kept."""
        if source not in SOURCES:
            raise ValueError(f"unknown source {source!r}")
        key = (room, source)
        tracker = self._trackers.get(key)
        if tracker is None or tracker.entity != entity:
            tracker = _Tracker(entity)  # a new or changed source starts with nothing open
            self._trackers[key] = tracker
        episode = tracker.feed(sample)
        if episode is not None:
            self._add(room, source, episode)
        return episode

    def forget_source(self, room: str, source: str) -> None:
        """The source has gone (a temp_sensor removed): drop what is open; episodes stay."""
        self._trackers.pop((room, source), None)

    def _add(self, room: str, source: str, episode: Episode) -> None:
        bucket = self.episodes.setdefault(room, {}).setdefault(source, [])
        bucket.append(episode)
        for kind in KINDS:
            of_kind = [e for e in bucket if e.kind == kind]
            if len(of_kind) > self.limit:
                drop = set(id(e) for e in of_kind[: len(of_kind) - self.limit])
                bucket[:] = [e for e in bucket if id(e) not in drop]

    def prune(self, room_ids: set[str]) -> bool:
        """Drop rooms that are no longer in the config. True when anything went."""
        gone = [r for r in self.episodes if r not in room_ids]
        for r in gone:
            del self.episodes[r]
        for key in [k for k in self._trackers if k[0] not in room_ids]:
            del self._trackers[key]
        return bool(gone)

    def room_summary(self, room: str, entities: dict[str, str], tz: tzinfo) -> dict[str, Any]:
        """The `learning` block of a room in GET state: one entry per current source.

        `entities` maps each source the room has now to its entity id. Only
        episodes read from that entity are counted."""
        sources: dict[str, Any] = {}
        for source in SOURCES:
            entity = entities.get(source)
            if not entity:
                continue
            eps = [e for e in self.episodes.get(room, {}).get(source, []) if e.entity == entity]
            sources[source] = {
                "entity": entity,
                "label": SOURCE_LABELS[source],
                "heat": summarize(eps, KIND_HEAT, tz),
                "cool": summarize(eps, KIND_COOL, tz),
            }
        open_now = {
            source: tracker.open.kind
            for (r, source), tracker in self._trackers.items()
            if r == room and tracker.open is not None and source in sources
        }
        return {"sources": sources, "open": open_now}

    def to_dict(self) -> dict[str, Any]:
        return {
            "rooms": {
                room: {source: [e.to_dict() for e in eps] for source, eps in by_source.items()}
                for room, by_source in self.episodes.items()
            }
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None, limit: int = EPISODE_LIMIT) -> Learning:
        """Read a stored record; an entry that cannot be read is skipped, never fatal."""
        learning = cls(limit=limit)
        rooms = (raw or {}).get("rooms") if isinstance(raw, dict) else None
        if not isinstance(rooms, dict):
            return learning
        for room, by_source in rooms.items():
            if not isinstance(by_source, dict):
                continue
            for source, eps in by_source.items():
                if source not in SOURCES or not isinstance(eps, list):
                    continue
                for d in eps:
                    try:
                        learning._add(str(room), source, Episode.from_dict(d))
                    except (KeyError, ValueError, TypeError):
                        continue
        return learning
