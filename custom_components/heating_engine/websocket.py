"""Websocket commands (plan section 8.5) plus one read-only extra, `heating_engine/log`,
which returns the would-write log, the writes sent, every change the engine
did not make and every request accepted. That is how the observe phase is read
without grepping log files."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import API_VERSION, DOMAIN, MAX_PLAN_DAYS, WS_GET_CONFIG, WS_LOG, WS_PLAN, WS_SET_CONFIG
from .engine import HeatingEngine, RevConflict
from .heating_core.validate import ValidationError


def _engine(hass: HomeAssistant) -> HeatingEngine | None:
    return hass.data.get(DOMAIN, {}).get("engine")


@websocket_api.websocket_command({vol.Required("type"): WS_GET_CONFIG})
@callback
def ws_get_config(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    engine = _engine(hass)
    if engine is None:
        connection.send_error(msg["id"], "not_loaded", "The heating engine is not loaded")
        return
    connection.send_result(msg["id"], {"config": engine.config.to_dict(), "rev": engine.config.rev, "api_version": API_VERSION})


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_SET_CONFIG,
        vol.Required("config"): dict,
        vol.Required("base_rev"): vol.Coerce(int),
        vol.Optional("source"): vol.All(str, vol.Match(r"^[a-z_]{1,32}$")),  # who is asking, for the activity log
    }
)
@websocket_api.async_response
async def ws_set_config(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    engine = _engine(hass)
    if engine is None:
        connection.send_error(msg["id"], "not_loaded", "The heating engine is not loaded")
        return
    user = connection.user.name if connection.user is not None else None
    try:
        rev = await engine.async_set_config(msg["config"], msg["base_rev"], source=msg.get("source"), user=user)
    except RevConflict as exc:
        connection.send_error(msg["id"], "rev_conflict", f"Someone else saved first (the config is now rev {exc.current_rev}). Reload and try again.")
        return
    except ValidationError as exc:
        connection.send_error(msg["id"], "invalid_config", "; ".join(exc.errors))
        return
    connection.send_result(msg["id"], {"rev": rev})


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_PLAN,
        vol.Required("start"): str,
        vol.Required("end"): str,
    }
)
@callback
def ws_plan(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    engine = _engine(hass)
    if engine is None:
        connection.send_error(msg["id"], "not_loaded", "The heating engine is not loaded")
        return
    start = dt_util.parse_datetime(msg["start"])
    end = dt_util.parse_datetime(msg["end"])
    if start is None or end is None:
        connection.send_error(msg["id"], "invalid_format", "start and end must be ISO 8601 datetimes")
        return
    tz = dt_util.get_default_time_zone()
    if start.tzinfo is None:
        start = start.replace(tzinfo=tz)
    if end.tzinfo is None:
        end = end.replace(tzinfo=tz)
    if end <= start:
        connection.send_error(msg["id"], "invalid_format", "end must be after start")
        return
    if end - start > timedelta(days=MAX_PLAN_DAYS):
        connection.send_error(msg["id"], "invalid_format", f"a plan may cover at most {MAX_PLAN_DAYS} days")
        return
    try:
        segments = engine.plan(start, end)
    except Exception as exc:  # noqa: BLE001
        connection.send_error(msg["id"], "plan_failed", f"{type(exc).__name__}: {exc}")
        return
    connection.send_result(msg["id"], {"segments": [s.to_dict() for s in segments], "api_version": API_VERSION})


@websocket_api.websocket_command({vol.Required("type"): WS_LOG})
@callback
def ws_log(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    engine = _engine(hass)
    if engine is None:
        connection.send_error(msg["id"], "not_loaded", "The heating engine is not loaded")
        return
    connection.send_result(msg["id"], engine.recent_log())


@callback
def async_register(hass: HomeAssistant) -> None:
    data = hass.data.setdefault(DOMAIN, {})
    if data.get("ws_registered"):
        return
    for cmd in (ws_get_config, ws_set_config, ws_plan, ws_log):
        websocket_api.async_register_command(hass, cmd)
    data["ws_registered"] = True
