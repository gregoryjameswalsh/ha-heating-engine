"""REST views, for clients that cannot open a Home Assistant websocket.

Same contract as the websocket commands, under /api/heating_engine/*, behind
Home Assistant's normal bearer-token auth. Any authenticated user may read and
write; the household decides who gets a token.

  GET  /api/heating_engine/state            everything a status page needs in one call
  GET  /api/heating_engine/config           {config, rev, api_version}
  POST /api/heating_engine/config           {config, base_rev} -> {rev}; 409 rev_conflict, 400 invalid_config
  GET  /api/heating_engine/plan?start=&end= {segments}
  GET  /api/heating_engine/log              would-writes, writes, unexpected changes

Boosts and exceptions use the ordinary service endpoints:
  POST /api/services/heating_engine/boost  etc.
"""
from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus
from typing import Any

from aiohttp import web
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import API_VERSION, DOMAIN, MAX_PLAN_DAYS
from .engine import HeatingEngine, RevConflict, RoomStatus
from .heating_core.validate import ValidationError


def _engine(request: web.Request) -> HeatingEngine | None:
    hass: HomeAssistant = request.app[KEY_HASS]
    return hass.data.get(DOMAIN, {}).get("engine")


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def room_status_dict(status: RoomStatus) -> dict[str, Any]:
    return {
        "room": status.room_id,
        "room_name": status.room_name,
        "climate_entity": status.climate,
        "mode": status.mode,
        "target": status.target,
        "rule": status.rule,
        "reason_code": status.reason_code,
        "reason_text": status.reason_text,
        "next_change": _iso(status.until),
        "confirmed": status.confirmed,
        "available": status.available,
        "current_setpoint": status.current_setpoint,
        "current_mode": status.current_mode,
        "current_temperature": status.current_temperature,
        "would_write": status.would_write,
        "pending_write": status.pending_write,
        "last_write": _iso(status.last_write),
        "problem": status.problem,
        "boost_until": _iso(status.boost_until),
    }


class _View(HomeAssistantView):
    requires_auth = True

    def _engine_or_error(self, request: web.Request) -> HeatingEngine | web.Response:
        engine = _engine(request)
        if engine is None:
            return self.json_message("The heating engine is not loaded", HTTPStatus.SERVICE_UNAVAILABLE, message_code="not_loaded")
        return engine


class StateView(_View):
    url = "/api/heating_engine/state"
    name = "api:heating_engine:state"

    async def get(self, request: web.Request) -> web.Response:
        engine = self._engine_or_error(request)
        if isinstance(engine, web.Response):
            return engine
        now = dt_util.utcnow()
        rooms = []
        for r in engine.config.rooms:
            status = engine.rooms.get(r.id)
            d = room_status_dict(status) if status else {"room": r.id, "room_name": r.name, "climate_entity": r.climate, "mode": "control" if r.control else "observe"}
            d["temps"] = r.temps.to_dict()
            d["occupants"] = list(r.occupants)
            d["control"] = r.control
            rooms.append(d)
        people = []
        for p in engine.config.people:
            info = engine.people_here.get(p.id)
            people.append(
                {
                    "id": p.id,
                    "name": p.name,
                    "kind": p.kind,
                    "here": info[0] if info else None,
                    "source": info[1] if info else None,
                    "until": info[2].isoformat() if info and info[2] else None,
                }
            )
        return self.json(
            {
                "api_version": API_VERSION,
                "now": now.isoformat(),
                "config_rev": engine.config.rev,
                "healthy": engine.healthy,
                "health": engine.health_attributes(),
                "rooms": rooms,
                "people": people,
                "boosts": [
                    {"room": b.room, "temp": b.temp, "until": b.until.isoformat(), "start": _iso(b.start)}
                    for b in engine.state.boosts.values()
                ],
                "exceptions": [e.to_dict() for e in engine.config.exceptions],
            }
        )


class ConfigView(_View):
    url = "/api/heating_engine/config"
    name = "api:heating_engine:config"

    async def get(self, request: web.Request) -> web.Response:
        engine = self._engine_or_error(request)
        if isinstance(engine, web.Response):
            return engine
        return self.json({"config": engine.config.to_dict(), "rev": engine.config.rev, "api_version": API_VERSION})

    async def post(self, request: web.Request) -> web.Response:
        engine = self._engine_or_error(request)
        if isinstance(engine, web.Response):
            return engine
        try:
            body = await request.json()
            config = body["config"]
            base_rev = int(body["base_rev"])
        except (ValueError, KeyError, TypeError):
            return self.json_message("Body must be JSON with config and base_rev", HTTPStatus.BAD_REQUEST, message_code="bad_request")
        try:
            rev = await engine.async_set_config(config, base_rev)
        except RevConflict as exc:
            return self.json_message(
                f"Someone else saved first (the config is now rev {exc.current_rev}). Reload and try again.",
                HTTPStatus.CONFLICT,
                message_code="rev_conflict",
            )
        except ValidationError as exc:
            return self.json_message("; ".join(exc.errors), HTTPStatus.BAD_REQUEST, message_code="invalid_config")
        return self.json({"rev": rev})


class PlanView(_View):
    url = "/api/heating_engine/plan"
    name = "api:heating_engine:plan"

    async def get(self, request: web.Request) -> web.Response:
        engine = self._engine_or_error(request)
        if isinstance(engine, web.Response):
            return engine
        start = dt_util.parse_datetime(request.query.get("start", ""))
        end = dt_util.parse_datetime(request.query.get("end", ""))
        if start is None or end is None:
            return self.json_message("start and end must be ISO 8601 datetimes", HTTPStatus.BAD_REQUEST, message_code="invalid_format")
        tz = dt_util.get_default_time_zone()
        if start.tzinfo is None:
            start = start.replace(tzinfo=tz)
        if end.tzinfo is None:
            end = end.replace(tzinfo=tz)
        if end <= start:
            return self.json_message("end must be after start", HTTPStatus.BAD_REQUEST, message_code="invalid_format")
        if end - start > timedelta(days=MAX_PLAN_DAYS):
            return self.json_message(f"a plan may cover at most {MAX_PLAN_DAYS} days", HTTPStatus.BAD_REQUEST, message_code="invalid_format")
        try:
            segments = engine.plan(start, end)
        except Exception as exc:  # noqa: BLE001
            return self.json_message(f"{type(exc).__name__}: {exc}", HTTPStatus.INTERNAL_SERVER_ERROR, message_code="plan_failed")
        return self.json({"segments": [s.to_dict() for s in segments], "api_version": API_VERSION})


class LogView(_View):
    url = "/api/heating_engine/log"
    name = "api:heating_engine:log"

    async def get(self, request: web.Request) -> web.Response:
        engine = self._engine_or_error(request)
        if isinstance(engine, web.Response):
            return engine
        return self.json(engine.recent_log())


def async_register_views(hass: HomeAssistant) -> None:
    data = hass.data.setdefault(DOMAIN, {})
    if data.get("http_registered"):
        return
    for view in (StateView(), ConfigView(), PlanView(), LogView()):
        hass.http.register_view(view)
    data["http_registered"] = True
