"""Config validation. The whole document is accepted or rejected (plan section
7 and 8.4). Messages are plain English for the editor to show as they are."""
from __future__ import annotations

from datetime import date
from typing import Any

from .model import DAYS, PATTERN_LENGTH, PERSON_KINDS, Config
from .timeutil import minutes_of, parse_hhmm

TEMP_MIN = 5.0
TEMP_MAX = 30.0
TEMP_STEP = 0.5
TIME_STEP_MINUTES = 15


class ValidationError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _is_step(value: float, step: float) -> bool:
    q = value / step
    return abs(q - round(q)) < 1e-9


def check_temp(errors: list[str], where: str, value: float) -> None:
    """Append a message unless `value` is a half degree inside the allowed range."""
    if not (TEMP_MIN <= value <= TEMP_MAX):
        errors.append(f"{where}: {value:g} is outside {TEMP_MIN:g} to {TEMP_MAX:g}")
    elif not _is_step(value, TEMP_STEP):
        errors.append(f"{where}: {value:g} is not a half degree")


def _check_time(errors: list[str], where: str, value: str, allow_24: bool) -> bool:
    try:
        h, m = parse_hhmm(value)
    except (ValueError, TypeError):
        errors.append(f"{where}: {value!r} is not a time like 07:30")
        return False
    if h == 24 and not allow_24:
        errors.append(f"{where}: 24:00 is not allowed here")
        return False
    if m % TIME_STEP_MINUTES != 0:
        errors.append(f"{where}: {value} is not on a quarter hour")
        return False
    return True


def validate_config(config: Config) -> list[str]:
    """Return every problem found, as plain sentences. Empty list means valid."""
    errors: list[str] = []

    if config.version != 1:
        errors.append(f"Config version {config.version} is not supported")
    if config.rev < 0:
        errors.append("rev must be zero or more")
    if config.rota_anchor.weekday() != 0:
        errors.append(f"The rota anchor {config.rota_anchor.isoformat()} is not a Monday")

    # ---- people ----
    seen_people: set[str] = set()
    for p in config.people:
        who = f"Person {p.name or p.id}"
        if not p.id or not p.id.strip():
            errors.append("A person has no id")
            continue
        if p.id in seen_people:
            errors.append(f"{who}: the id {p.id!r} is used twice")
        seen_people.add(p.id)
        if not p.name or not p.name.strip():
            errors.append(f"Person {p.id}: needs a name")
        if p.kind not in PERSON_KINDS:
            errors.append(f"{who}: kind {p.kind!r} must be one of {', '.join(PERSON_KINDS)}")
            continue
        if p.kind == "rota":
            if p.pattern is None or len(p.pattern) != PATTERN_LENGTH:
                errors.append(f"{who}: the pattern needs exactly {PATTERN_LENGTH} nights")
            elif any(v not in (0, 1) for v in p.pattern):
                errors.append(f"{who}: each night in the pattern must be 0 or 1")
        if p.kind in ("rota", "always"):
            _check_time(errors, f"{who}: arrive", p.arrive, allow_24=False)
            _check_time(errors, f"{who}: leave", p.leave, allow_24=False)
        if p.kind == "calendar":
            if not p.calendar:
                errors.append(f"{who}: needs a calendar entity")
            if p.event_means != "away":
                errors.append(f"{who}: event_means must be 'away'")
            if p.return_preheat_hours < 0:
                errors.append(f"{who}: return pre-heat hours cannot be negative")

    # ---- rooms ----
    seen_rooms: set[str] = set()
    seen_climates: set[str] = set()
    for r in config.rooms:
        where = f"Room {r.name or r.id}"
        if not r.id or not r.id.strip():
            errors.append("A room has no id")
            continue
        if r.id in seen_rooms:
            errors.append(f"{where}: the id {r.id!r} is used twice")
        seen_rooms.add(r.id)
        if not r.name or not r.name.strip():
            errors.append(f"Room {r.id}: needs a name")
        if not r.climate or not r.climate.strip():
            errors.append(f"{where}: needs the climate entity to write to")
        elif r.climate in seen_climates:
            errors.append(f"{where}: climate entity {r.climate} is already used by another room")
        else:
            seen_climates.add(r.climate)
        if r.climate and r.climate in r.watch:
            errors.append(f"{where}: {r.climate} is the entity written to; it is watched already, do not list it under watch")
        for pid in r.occupants:
            if pid not in seen_people:
                errors.append(f"{where}: occupant {pid!r} is not a known person")
        if len(set(r.occupants)) != len(r.occupants):
            errors.append(f"{where}: an occupant is listed twice")
        check_temp(errors, f"{where}: comfort", r.temps.comfort)
        check_temp(errors, f"{where}: eco", r.temps.eco)
        check_temp(errors, f"{where}: empty", r.temps.empty)
        check_temp(errors, f"{where}: frost", r.temps.frost)
        for day in DAYS:
            blocks = r.blocks_for(day)
            prev_end: int | None = None
            for i, b in enumerate(sorted(blocks, key=lambda x: _safe_minutes(x.start))):
                label = f"{where}: {day} block {i + 1}"
                ok = _check_time(errors, f"{label} start", b.start, allow_24=False)
                ok = _check_time(errors, f"{label} end", b.end, allow_24=True) and ok
                check_temp(errors, f"{label} temp", b.temp)
                if not ok:
                    continue
                s, e = minutes_of(b.start), minutes_of(b.end)
                if e <= s:
                    errors.append(f"{label}: ends at or before it starts, and a block may not cross midnight")
                    continue
                if prev_end is not None and s < prev_end:
                    errors.append(f"{label}: overlaps the block before it")
                prev_end = max(prev_end or 0, e)
        for day in r.schedule:
            if day not in DAYS:
                errors.append(f"{where}: {day!r} is not a day of the week")

    # ---- exceptions ----
    seen_exc: set[tuple[str, date]] = set()
    for e in config.exceptions:
        p = config.person(e.person)
        if p is None:
            errors.append(f"Exception for {e.person!r} on {e.night.isoformat()}: not a known person")
            continue
        if p.kind == "calendar":
            errors.append(f"Exception for {p.name} on {e.night.isoformat()}: {p.name} follows a calendar, edit the calendar instead")
        key = (e.person, e.night)
        if key in seen_exc:
            errors.append(f"Exception for {p.name} on {e.night.isoformat()}: listed twice")
        seen_exc.add(key)

    # ---- holidays ----
    for h in config.holidays:
        if h.end <= h.start:
            errors.append(f"Holiday from {h.start.isoformat()}: the return date must be after the start")
        if h.preheat_hours < 0:
            errors.append(f"Holiday from {h.start.isoformat()}: pre-heat hours cannot be negative")

    return errors


def _safe_minutes(value: str) -> int:
    try:
        return minutes_of(value)
    except (ValueError, TypeError):
        return 0


def parse_and_validate(raw: dict[str, Any]) -> Config:
    """Build a Config from a raw document and validate it, raising
    ValidationError with every message rather than just the first."""
    try:
        cfg = Config.from_dict(raw)
    except (KeyError, ValueError, TypeError) as exc:
        raise ValidationError([f"The config is missing or has a bad field: {exc}"]) from exc
    errors = validate_config(cfg)
    if errors:
        raise ValidationError(errors)
    return cfg
