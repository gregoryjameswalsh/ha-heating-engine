"""An invented household for the tests. Nothing here describes a real house.

Rota anchor: Monday 5 January 2026 starts a Week A.

  Person        Week A nights               Week B nights
  Pip           Mon Tue Wed Fri Sat Sun     Wed Thu
  Quinn         Mon Wed Fri Sat Sun         none
  Rowan & Sky   Tue Wed Fri Sat Sun         none
  Teddy         every night                 every night
  Gran & Gramps follow calendar.studio (an event there means away)

Expected room occupancy (the table the engine is tested against):

  Room        Week A                       Week B
  Blue Room   every night except Thu       Wed, Thu
  Green Room  Tue Wed Fri Sat Sun          empty all week
  Attic       every night                  every night
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from heating_core import Block, Config, Holiday, Person, Room, Temps

TZ = ZoneInfo("Europe/London")
ANCHOR = date(2026, 1, 5)  # a Monday

WEEK_A_MON = date(2026, 1, 5)
WEEK_B_MON = date(2026, 1, 12)

WEEKDAY_BLOCKS = [Block("06:30", "08:00", 19.0), Block("17:00", "21:00", 19.0)]
WEEKEND_BLOCKS = [Block("08:00", "21:00", 19.0)]


def L(y: int, m: int, d: int, hh: int = 0, mm: int = 0) -> datetime:
    """Aware local datetime in the test zone."""
    return datetime(y, m, d, hh, mm, tzinfo=TZ)


def U(y: int, m: int, d: int, hh: int = 0, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def standard_schedule() -> dict[str, list[Block]]:
    return {
        "mon": list(WEEKDAY_BLOCKS),
        "tue": list(WEEKDAY_BLOCKS),
        "wed": list(WEEKDAY_BLOCKS),
        "thu": list(WEEKDAY_BLOCKS),
        "fri": list(WEEKDAY_BLOCKS),
        "sat": list(WEEKEND_BLOCKS),
        "sun": list(WEEKEND_BLOCKS),
    }


def make_config(holidays: list[Holiday] | None = None) -> Config:
    people = [
        Person(id="pip", name="Pip", kind="rota", pattern=[1, 1, 1, 0, 1, 1, 1, 0, 0, 1, 1, 0, 0, 0]),
        Person(id="quinn", name="Quinn", kind="rota", pattern=[1, 0, 1, 0, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0]),
        Person(id="rowan_sky", name="Rowan & Sky", kind="rota", pattern=[0, 1, 1, 0, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0]),
        Person(id="teddy", name="Teddy", kind="always"),
        Person(id="visitors", name="Gran & Gramps", kind="calendar", calendar="calendar.studio", return_preheat_hours=12),
    ]
    kids = Temps(comfort=19.0, eco=16.0, empty=14.0)
    adults = Temps(comfort=18.0, eco=15.0, empty=12.0)
    rooms = [
        Room(id="blue_room", name="Blue Room", climate="climate.blue_room_valve", temps=kids, schedule=standard_schedule(), occupants=["pip", "quinn"]),
        Room(id="green_room", name="Green Room", climate="climate.green_room_valve", temps=kids, schedule=standard_schedule(), occupants=["rowan_sky"]),
        Room(id="attic", name="Attic", climate="climate.attic_valve", temps=kids, schedule=standard_schedule(), occupants=["teddy"]),
        Room(id="big_bedroom", name="Big Bedroom", climate="climate.big_bedroom_valve", temps=adults, schedule={"mon": [Block("21:00", "23:00", 18.0)]}, occupants=[]),
        Room(id="kitchen", name="Kitchen", climate="climate.kitchen_stat", temps=adults, schedule=standard_schedule(), occupants=[]),
        Room(id="studio_bedroom", name="Studio Bedroom", climate="climate.studio_bedroom_valve", temps=adults, schedule=standard_schedule(), occupants=["visitors"]),
        Room(
            id="studio_lounge",
            name="Studio Lounge",
            climate="climate.studio_lounge_stat",
            watch=["climate.studio_lounge_valve"],
            temps=adults,
            schedule=standard_schedule(),
            occupants=["visitors"],
        ),
    ]
    return Config(version=1, rev=1, rota_anchor=ANCHOR, people=people, rooms=rooms, holidays=list(holidays or []))


def room(cfg: Config, room_id: str) -> Room:
    r = cfg.room(room_id)
    assert r is not None
    return r


def person(cfg: Config, person_id: str) -> Person:
    p = cfg.person(person_id)
    assert p is not None
    return p
