"""The fortnightly rota: who sleeps here on which night.

"Here on Monday" means sleeping here Monday night. A pattern is 14 values,
index 0 = the Monday of a Week A, and `rota_anchor` is a Monday that starts a
Week A. Exceptions flip a single night and win over the pattern.
"""
from __future__ import annotations

from datetime import date

from .model import PATTERN_LENGTH, Config, ExceptionNight, Person

SOURCE_EXCEPTION = "exception"
SOURCE_ROTA = "rota"
SOURCE_ALWAYS = "always"
SOURCE_CALENDAR = "calendar"


def pattern_index(anchor: date, night: date) -> int:
    """Index into the 14-night pattern for `night`. Works before the anchor too."""
    return (night - anchor).days % PATTERN_LENGTH


def week_label(anchor: date, night: date) -> str:
    """'A' or 'B' for the week `night` falls in."""
    return "A" if pattern_index(anchor, night) < 7 else "B"


def exception_for(config: Config, person_id: str, night: date) -> ExceptionNight | None:
    for e in config.exceptions:
        if e.person == person_id and e.night == night:
            return e
    return None


def sleeps_here(config: Config, person: Person, night: date) -> tuple[bool, str]:
    """Does `person` sleep here on `night`? Returns (here, source).

    Only for rota and always people. Calendar people are driven by intervals
    from their calendar; see `occupancy.person_present_intervals`.
    """
    exc = exception_for(config, person.id, night)
    if exc is not None:
        return exc.here, SOURCE_EXCEPTION
    if person.kind == "always":
        return True, SOURCE_ALWAYS
    if person.kind == "rota":
        pattern = person.pattern or []
        if len(pattern) != PATTERN_LENGTH:
            # validate_config rejects this; be safe rather than crash the loop
            return False, SOURCE_ROTA
        return bool(pattern[pattern_index(config.rota_anchor, night)]), SOURCE_ROTA
    raise ValueError(f"sleeps_here does not apply to a {person.kind!r} person")
