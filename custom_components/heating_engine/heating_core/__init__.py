"""Pure heating logic. No Home Assistant imports anywhere in this package.

Everything here is deterministic given its inputs: the config document, the
runtime state (boosts, holds, manual overrides), calendar events already fetched by the
caller, and aware datetimes. The Home Assistant shell feeds it and acts on it.
"""
from .model import (
    DEFAULT_FROST_TEMP,
    HOLD_KINDS,
    Block,
    Boost,
    CalendarEvent,
    Config,
    EngineState,
    ExceptionNight,
    Hold,
    Holiday,
    ManualOverride,
    Person,
    Room,
    Segment,
    Temps,
)
from .occupancy import person_here_tonight
from .persist import state_from_dict, state_to_dict
from .resolve import next_schedule_change, override_end, resolve_now, resolve_plan
from .validate import ValidationError, validate_config

API_VERSION = 1

__all__ = [
    "API_VERSION",
    "DEFAULT_FROST_TEMP",
    "HOLD_KINDS",
    "Block",
    "Boost",
    "CalendarEvent",
    "Config",
    "EngineState",
    "ExceptionNight",
    "Hold",
    "Holiday",
    "ManualOverride",
    "Person",
    "Room",
    "Segment",
    "Temps",
    "ValidationError",
    "next_schedule_change",
    "override_end",
    "person_here_tonight",
    "resolve_now",
    "resolve_plan",
    "state_from_dict",
    "state_to_dict",
    "validate_config",
]
