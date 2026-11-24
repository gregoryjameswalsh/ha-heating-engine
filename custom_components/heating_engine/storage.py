"""Persistence through Home Assistant's Store helper.

Two documents under .storage:

- heating_engine.config  the config document (plan section 7), with `rev`
- heating_engine.state   runtime state: boosts, holds, manual overrides, the
                         last calendar read, the log of changes the engine
                         did not make and the three activity logs (would-writes,
                         writes, requests). Never part of `rev`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY_CONFIG, STORAGE_KEY_STATE, STORAGE_VERSION
from .heating_core import CalendarEvent, Config, EngineState, RequestLog, logs_from_dict, logs_to_dict, state_from_dict, state_to_dict
from .heating_core.apply import ChangeLog


@dataclass
class StoredState:
    """Everything the state document holds, as the engine keeps it in memory."""

    state: EngineState = field(default_factory=EngineState)
    changes: ChangeLog = field(default_factory=ChangeLog)
    calendars: dict[str, list[CalendarEvent]] = field(default_factory=dict)
    would_write: list[dict[str, Any]] = field(default_factory=list)
    writes: list[dict[str, Any]] = field(default_factory=list)
    requests: RequestLog = field(default_factory=RequestLog)


def default_config(today: date) -> Config:
    """An empty but valid document: the most recent Monday as the anchor."""
    monday = today - timedelta(days=today.weekday())
    return Config(version=1, rev=0, rota_anchor=monday)


class ConfigStore:
    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY_CONFIG)

    async def load(self, today: date) -> Config:
        raw = await self._store.async_load()
        if not raw:
            return default_config(today)
        return Config.from_dict(raw)

    async def save(self, config: Config) -> None:
        await self._store.async_save(config.to_dict())


class StateStore:
    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY_STATE)

    async def load(self) -> StoredState:
        raw = await self._store.async_load() or {}
        state = state_from_dict(raw)
        calendars: dict[str, list[CalendarEvent]] = {}
        for cal, events in (raw.get("calendars") or {}).items():
            out: list[CalendarEvent] = []
            for ev in events:
                try:
                    out.append(CalendarEvent(start=datetime.fromisoformat(ev["start"]), end=datetime.fromisoformat(ev["end"])))
                except (KeyError, ValueError, TypeError):
                    continue
            calendars[str(cal)] = out
        log = ChangeLog.from_list(raw.get("changes", []))
        would_write, writes, requests = logs_from_dict(raw)
        return StoredState(state=state, changes=log, calendars=calendars, would_write=would_write, writes=writes, requests=requests)

    async def save(self, stored: StoredState) -> None:
        doc = state_to_dict(stored.state)  # boosts, holds, overrides
        # the last successful calendar read, so a restart keeps the last known
        # away state instead of guessing "home" until the first read succeeds
        doc["calendars"] = {cal: [{"start": e.start.isoformat(), "end": e.end.isoformat()} for e in events] for cal, events in stored.calendars.items()}
        doc["changes"] = stored.changes.to_list()
        doc.update(logs_to_dict(stored.would_write, stored.writes, stored.requests))
        await self._store.async_save(doc)
