"""Data shapes for the config document, runtime state and plan output.

The config document is one JSON object owned by the engine (plan section 7).
`Config.from_dict` is forgiving about missing optional keys so that an older
document still loads; `validate_config` is where strictness lives.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
PERSON_KINDS = ("rota", "always", "calendar")
PATTERN_LENGTH = 14
DEFAULT_ARRIVE = "15:00"
DEFAULT_LEAVE = "09:00"
DEFAULT_RETURN_PREHEAT_HOURS = 12.0
DEFAULT_HOLIDAY_PREHEAT_HOURS = 6.0


@dataclass(frozen=True)
class Block:
    """One warm block inside a day: [start, end) in local wall-clock HH:MM."""

    start: str
    end: str
    temp: float

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "temp": self.temp}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Block:
        return cls(start=str(d["start"]), end=str(d["end"]), temp=float(d["temp"]))


@dataclass(frozen=True)
class Temps:
    comfort: float
    eco: float
    empty: float

    def to_dict(self) -> dict[str, Any]:
        return {"comfort": self.comfort, "eco": self.eco, "empty": self.empty}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Temps:
        return cls(comfort=float(d["comfort"]), eco=float(d["eco"]), empty=float(d["empty"]))


@dataclass
class Person:
    id: str
    name: str
    kind: str  # one of PERSON_KINDS
    pattern: list[int] | None = None  # rota only: 14 values, index 0 = Monday of Week A
    arrive: str = DEFAULT_ARRIVE  # rota/always: time on night D the room becomes occupied
    leave: str = DEFAULT_LEAVE  # rota/always: time on day D+1 the room is vacated
    calendar: str | None = None  # calendar only: the calendar entity id
    event_means: str = "away"  # calendar only: what an event on it means
    return_preheat_hours: float = DEFAULT_RETURN_PREHEAT_HOURS  # calendar only

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"id": self.id, "name": self.name, "kind": self.kind}
        if self.kind == "rota":
            d["pattern"] = list(self.pattern or [])
        if self.kind in ("rota", "always"):
            d["arrive"] = self.arrive
            d["leave"] = self.leave
        if self.kind == "calendar":
            d["calendar"] = self.calendar
            d["event_means"] = self.event_means
            d["return_preheat_hours"] = self.return_preheat_hours
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Person:
        return cls(
            id=str(d["id"]),
            name=str(d.get("name", d["id"])),
            kind=str(d.get("kind", "rota")),
            pattern=[int(x) for x in d["pattern"]] if d.get("pattern") is not None else None,
            arrive=str(d.get("arrive", DEFAULT_ARRIVE)),
            leave=str(d.get("leave", DEFAULT_LEAVE)),
            calendar=d.get("calendar"),
            event_means=str(d.get("event_means", "away")),
            return_preheat_hours=float(d.get("return_preheat_hours", DEFAULT_RETURN_PREHEAT_HOURS)),
        )


@dataclass
class Room:
    id: str
    name: str
    climate: str  # the climate entity the engine writes to
    temps: Temps
    schedule: dict[str, list[Block]] = field(default_factory=dict)
    occupants: list[str] = field(default_factory=list)  # [] means always in use
    watch: list[str] = field(default_factory=list)  # other climate entities to watch, never written
    temp_sensor: str | None = None
    presence_sensor: str | None = None
    control: bool = False  # False = observe only: log what would be written, write nothing

    def blocks_for(self, day: str) -> list[Block]:
        return self.schedule.get(day, [])

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "climate": self.climate,
            "watch": list(self.watch),
            "temp_sensor": self.temp_sensor,
            "presence_sensor": self.presence_sensor,
            "occupants": list(self.occupants),
            "temps": self.temps.to_dict(),
            "schedule": {day: [b.to_dict() for b in self.schedule.get(day, [])] for day in DAYS},
            "control": self.control,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Room:
        sched_in = d.get("schedule") or {}
        schedule = {day: [Block.from_dict(b) for b in sched_in.get(day, [])] for day in DAYS}
        return cls(
            id=str(d["id"]),
            name=str(d.get("name", d["id"])),
            climate=str(d.get("climate", "")),
            temps=Temps.from_dict(d["temps"]),
            schedule=schedule,
            occupants=[str(x) for x in d.get("occupants", [])],
            watch=[str(x) for x in d.get("watch", [])],
            temp_sensor=d.get("temp_sensor"),
            presence_sensor=d.get("presence_sensor"),
            control=bool(d.get("control", False)),
        )


@dataclass(frozen=True)
class ExceptionNight:
    """A one-off change to whether a person sleeps here on one night."""

    person: str
    night: date
    here: bool

    def to_dict(self) -> dict[str, Any]:
        return {"person": self.person, "night": self.night.isoformat(), "here": self.here}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExceptionNight:
        return cls(person=str(d["person"]), night=date.fromisoformat(str(d["night"])), here=bool(d["here"]))


@dataclass(frozen=True)
class Holiday:
    """Whole house at Empty from local midnight on `start` until `end` (the return
    date, exclusive) minus `preheat_hours`."""

    start: date
    end: date
    preheat_hours: float = DEFAULT_HOLIDAY_PREHEAT_HOURS

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(), "preheat_hours": self.preheat_hours}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Holiday:
        return cls(
            start=date.fromisoformat(str(d["start"])),
            end=date.fromisoformat(str(d["end"])),
            preheat_hours=float(d.get("preheat_hours", DEFAULT_HOLIDAY_PREHEAT_HOURS)),
        )


@dataclass
class Config:
    version: int
    rev: int
    rota_anchor: date  # the Monday that starts a Week A
    people: list[Person] = field(default_factory=list)
    rooms: list[Room] = field(default_factory=list)
    exceptions: list[ExceptionNight] = field(default_factory=list)
    holidays: list[Holiday] = field(default_factory=list)

    def person(self, person_id: str) -> Person | None:
        for p in self.people:
            if p.id == person_id:
                return p
        return None

    def room(self, room_id: str) -> Room | None:
        for r in self.rooms:
            if r.id == room_id:
                return r
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "rev": self.rev,
            "rota_anchor": self.rota_anchor.isoformat(),
            "people": [p.to_dict() for p in self.people],
            "rooms": [r.to_dict() for r in self.rooms],
            "exceptions": [e.to_dict() for e in self.exceptions],
            "holidays": [h.to_dict() for h in self.holidays],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Config:
        return cls(
            version=int(d.get("version", 1)),
            rev=int(d.get("rev", 0)),
            rota_anchor=date.fromisoformat(str(d["rota_anchor"])),
            people=[Person.from_dict(p) for p in d.get("people", [])],
            rooms=[Room.from_dict(r) for r in d.get("rooms", [])],
            exceptions=[ExceptionNight.from_dict(e) for e in d.get("exceptions", [])],
            holidays=[Holiday.from_dict(h) for h in d.get("holidays", [])],
        )


# ---- runtime state (not part of the config document, never part of `rev`) ----


@dataclass(frozen=True)
class Boost:
    room: str
    temp: float
    until: datetime  # aware
    start: datetime | None = None  # aware; None means "since before the window"


@dataclass(frozen=True)
class ManualOverride:
    """A setpoint the engine did not write (plan rule 2). Holds until the next
    schedule change after `since`; the resolver works that moment out."""

    room: str
    temp: float
    since: datetime  # aware


@dataclass(frozen=True)
class CalendarEvent:
    """An event on a person's calendar, already fetched by the caller. Aware
    datetimes; an all-day event is local midnight to local midnight."""

    start: datetime
    end: datetime


@dataclass
class EngineState:
    boosts: dict[str, Boost] = field(default_factory=dict)  # by room id
    overrides: dict[str, ManualOverride] = field(default_factory=dict)  # by room id


# ---- plan output ----


@dataclass(frozen=True)
class Segment:
    """One stretch of time in one room with a single target and reason.

    `start`/`end` are clipped to the requested window. `until` is the real end of
    this stretch, which may lie beyond the window; None means it was not found
    within the resolver's look-ahead.
    """

    room: str
    start: datetime
    end: datetime
    temp: float
    rule: int
    reason_code: str
    reason_text: str
    until: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "room": self.room,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "temp": self.temp,
            "rule": self.rule,
            "reason_code": self.reason_code,
            "reason_text": self.reason_text,
            "until": self.until.isoformat() if self.until else None,
        }
