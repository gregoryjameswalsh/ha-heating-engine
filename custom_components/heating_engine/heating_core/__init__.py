"""Pure heating logic. No Home Assistant imports anywhere in this package.

Everything here is deterministic given its inputs: the config document, the
runtime state (boosts, manual overrides), calendar events already fetched by the
caller, and aware datetimes. The Home Assistant shell feeds it and acts on it.
"""
from .model import (
    Block,
    Boost,
    CalendarEvent,
    Config,
    EngineState,
    ExceptionNight,
    Holiday,
    ManualOverride,
    Person,
    Room,
    Segment,
    Temps,
)
from .occupancy import person_here_tonight
from .resolve import override_end, resolve_now, resolve_plan
from .validate import ValidationError, validate_config

API_VERSION = 1

__all__ = [
    "API_VERSION",
    "Block",
    "Boost",
    "CalendarEvent",
    "Config",
    "EngineState",
    "ExceptionNight",
    "Holiday",
    "ManualOverride",
    "Person",
    "Room",
    "Segment",
    "Temps",
    "ValidationError",
    "override_end",
    "person_here_tonight",
    "resolve_now",
    "resolve_plan",
    "validate_config",
]
