"""The activity log: every request the engine accepted, in its own words.

A request is a boost, a hold, an exception or a config save, from a service
call, a REST view or the websocket. Each one is logged whether or not it leads
to a write, with who asked: `source`, a short tag the caller sends (the heating
page sends `heating_page`), and `user`, the Home Assistant user's name when the
call carried one. The dashboard reads it to say "from the phone".

Also here: the vocabulary for what caused a write (`cause`), and the detail
text for each kind of request, so that the words are tested.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, tzinfo
from typing import Any

from .timeutil import format_temp, format_time_until
from .validate import ValidationError

ACTION_BOOST = "boost"
ACTION_CANCEL_BOOST = "cancel_boost"
ACTION_HOLD = "hold"
ACTION_CANCEL_HOLD = "cancel_hold"
ACTION_SET_EXCEPTION = "set_exception"
ACTION_CLEAR_EXCEPTION = "clear_exception"
ACTION_CONFIG = "config"
REQUEST_ACTIONS = (
    ACTION_BOOST,
    ACTION_CANCEL_BOOST,
    ACTION_HOLD,
    ACTION_CANCEL_HOLD,
    ACTION_SET_EXCEPTION,
    ACTION_CLEAR_EXCEPTION,
    ACTION_CONFIG,
)

# what caused a write
CAUSE_SCHEDULE = "schedule"
CAUSE_BOOST = "boost"
CAUSE_HOLD = "hold"
CAUSE_MANUAL = "manual"  # re-asserting a change made at the radiator
CAUSE_RESTORE = "restore"  # putting an observing room's setpoint back after a boost or hold
WRITE_CAUSES = (CAUSE_SCHEDULE, CAUSE_BOOST, CAUSE_HOLD, CAUSE_MANUAL, CAUSE_RESTORE)

SOURCE_MAX_LENGTH = 32
LOG_LIMIT = 200


def check_source(value: Any) -> str | None:
    """A caller's `source` tag: None, or 1 to 32 characters of a-z and _."""
    if value is None:
        return None
    if isinstance(value, str) and 1 <= len(value) <= SOURCE_MAX_LENGTH and all(c == "_" or "a" <= c <= "z" for c in value):
        return value
    raise ValidationError([f"source must be 1 to {SOURCE_MAX_LENGTH} characters of a-z or _"])


def write_cause(reason_code: str | None) -> str:
    """The `cause` of a write made to match the resolved target: the target's
    reason code when it is a boost, a hold or a manual change, else `schedule`."""
    if reason_code in (CAUSE_BOOST, CAUSE_HOLD, CAUSE_MANUAL):
        return reason_code
    return CAUSE_SCHEDULE


# ---- the log ----


@dataclass(frozen=True)
class Request:
    """One accepted request. `room` or `person` says what it was about; a config
    save has neither. `detail` is plain words in the engine's own voice."""

    when: datetime  # aware
    action: str  # one of REQUEST_ACTIONS
    detail: str
    room: str | None = None
    person: str | None = None
    source: str | None = None
    user: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "when": self.when.isoformat(),
            "room": self.room,
            "person": self.person,
            "action": self.action,
            "detail": self.detail,
            "source": self.source,
            "user": self.user,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Request:
        """Raises KeyError, ValueError or TypeError for an entry that cannot be read."""
        action = str(d["action"])
        if action not in REQUEST_ACTIONS:
            raise ValueError(f"unknown action {action!r}")
        return cls(
            when=datetime.fromisoformat(str(d["when"])),
            action=action,
            detail=str(d.get("detail") or ""),
            room=_str_or_none(d.get("room")),
            person=_str_or_none(d.get("person")),
            source=_str_or_none(d.get("source")),
            user=_str_or_none(d.get("user")),
        )


def _str_or_none(value: Any) -> str | None:
    return str(value) if value is not None else None


@dataclass
class RequestLog:
    """Every accepted request, oldest first. Bounded."""

    limit: int = LOG_LIMIT
    entries: list[Request] = field(default_factory=list)

    def add(self, entry: Request) -> None:
        self.entries.append(entry)
        if len(self.entries) > self.limit:
            del self.entries[: len(self.entries) - self.limit]

    def to_list(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self.entries]

    @classmethod
    def from_list(cls, raw: Any, limit: int = LOG_LIMIT) -> RequestLog:
        log = cls(limit=limit)
        if not isinstance(raw, list):
            return log
        for d in raw:
            if not isinstance(d, dict):
                continue
            try:
                log.entries.append(Request.from_dict(d))
            except (KeyError, ValueError, TypeError):
                continue
        del log.entries[:-limit]  # the newest `limit` readable entries
        return log


# ---- detail text ----

CONFIG_DETAIL = "Schedule or settings changed"
BACK_TO_SCHEDULE = "Back to the schedule"


def boost_detail(temp: float, minutes: int) -> str:
    return f"Boost to {format_temp(temp)} for {int(minutes)} min"


def cancel_boost_detail(hold_remains: bool) -> str:
    return "Boost cancelled, hold resumes" if hold_remains else BACK_TO_SCHEDULE


def hold_detail(kind: str, target: float, until: datetime | None, now: datetime, tz: tzinfo) -> str:
    when = format_time_until(now, until, tz) if until is not None else "turned back on"
    if kind == "skip":
        return f"Skip until {when}"
    if kind == "eco":
        return f"Hold at Eco until {when}"
    if kind == "frost":
        return f"Frost protection until {when}"
    return f"Set to {format_temp(target)} until {when}"


def cancel_hold_detail(boost_running: bool) -> str:
    return "Hold cancelled, boost still running" if boost_running else BACK_TO_SCHEDULE


def format_night(night: date) -> str:
    """'Wed 26 Nov'."""
    return f"{night.strftime('%a')} {night.day} {night.strftime('%b')}"


def exception_detail(here: bool, night: date) -> str:
    return f"{'Here' if here else 'Away'} on {format_night(night)}"


def clear_exception_detail(night: date) -> str:
    return f"Back to the rota on {format_night(night)}"
