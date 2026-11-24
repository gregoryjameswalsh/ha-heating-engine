"""Constants for the Heating Engine integration."""
from datetime import timedelta

DOMAIN = "heating_engine"
API_VERSION = 1

STORAGE_VERSION = 1
STORAGE_KEY_CONFIG = f"{DOMAIN}.config"
STORAGE_KEY_STATE = f"{DOMAIN}.state"

EVALUATE_EVERY = timedelta(minutes=1)
CALENDAR_REFRESH = timedelta(minutes=5)
CALENDAR_LOOKBACK = timedelta(days=2)
CALENDAR_LOOKAHEAD = timedelta(days=20)
HEARTBEAT_STALE_AFTER = timedelta(minutes=5)
MAX_PLAN_DAYS = 15
DEFAULT_BOOST_MINUTES = 60
LONG_BOOST_NOTICE_AFTER = timedelta(hours=3)

SIGNAL_UPDATED = f"{DOMAIN}_updated"  # after every evaluation: entities re-read the engine
SIGNAL_CONFIG_CHANGED = f"{DOMAIN}_config_changed"  # rooms/people may have been added or removed

SERVICE_BOOST = "boost"
SERVICE_CANCEL_BOOST = "cancel_boost"
SERVICE_HOLD = "hold"
SERVICE_CANCEL_HOLD = "cancel_hold"
SERVICE_SET_EXCEPTION = "set_exception"
SERVICE_CLEAR_EXCEPTION = "clear_exception"

WS_GET_CONFIG = f"{DOMAIN}/get_config"
WS_SET_CONFIG = f"{DOMAIN}/set_config"
WS_PLAN = f"{DOMAIN}/plan"
WS_LOG = f"{DOMAIN}/log"

MODE_OBSERVE = "observe"
MODE_CONTROL = "control"

PROBLEM_UNAVAILABLE = "unavailable"
PROBLEM_NOT_RESPONDING = "not_responding"
PROBLEM_CALENDAR = "calendar"
PROBLEM_LONG_BOOST = "long_boost"
PROBLEM_EVALUATION = "evaluation"
