"""Pure heating logic. No Home Assistant imports anywhere in this package.

Everything here is deterministic given its inputs: the config document, the
runtime state (boosts, holds, manual overrides), calendar events already fetched by the
caller, and aware datetimes. The Home Assistant shell feeds it and acts on it.
"""
from .activity import (
    CONFIG_DETAIL,
    REQUEST_ACTIONS,
    WRITE_CAUSES,
    Request,
    RequestLog,
    boost_detail,
    cancel_boost_detail,
    cancel_hold_detail,
    check_source,
    clear_exception_detail,
    exception_detail,
    hold_detail,
    write_cause,
)
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
from .learning import SOURCE_DEVICE, SOURCE_SENSOR, Episode, Learning, Sample
from .occupancy import person_here_tonight
from .persist import logs_from_dict, logs_to_dict, state_from_dict, state_to_dict
from .resolve import PLAN_GRACE, matches_plan, next_schedule_change, override_end, resolve_now, resolve_plan
from .validate import ValidationError, validate_config

API_VERSION = 1
# What this engine can do beyond api_version 1's first shape, for a page that
# must work against older engines too. All additive; api_version stays 1.
#   holds     the hold / cancel_hold services and views, `hold` in room state
#   activity  `source` on requests, `requests` in the log, `cause` on writes
#   learning  `learning` in room state, GET learning, `outside_sensor` in the config
FEATURES = ("holds", "activity", "learning")

__all__ = [
    "API_VERSION",
    "SOURCE_DEVICE",
    "SOURCE_SENSOR",
    "Episode",
    "Learning",
    "Sample",
    "CONFIG_DETAIL",
    "FEATURES",
    "REQUEST_ACTIONS",
    "WRITE_CAUSES",
    "Request",
    "RequestLog",
    "boost_detail",
    "cancel_boost_detail",
    "cancel_hold_detail",
    "check_source",
    "clear_exception_detail",
    "exception_detail",
    "hold_detail",
    "logs_from_dict",
    "logs_to_dict",
    "write_cause",
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
    "PLAN_GRACE",
    "Person",
    "Room",
    "Segment",
    "Temps",
    "ValidationError",
    "matches_plan",
    "next_schedule_change",
    "override_end",
    "person_here_tonight",
    "resolve_now",
    "resolve_plan",
    "state_from_dict",
    "state_to_dict",
    "validate_config",
]
