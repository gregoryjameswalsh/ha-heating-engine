"""Persistence through Home Assistant's Store helper.

Two documents under .storage:

- heating_engine.config  the config document (plan section 7), with `rev`
- heating_engine.state   runtime state: boosts, manual overrides, the log of
                         changes the engine did not make. Never part of `rev`.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY_CONFIG, STORAGE_KEY_STATE, STORAGE_VERSION
from .heating_core import Boost, Config, EngineState, ManualOverride
from .heating_core.apply import ChangeLog


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

    async def load(self) -> tuple[EngineState, ChangeLog]:
        raw = await self._store.async_load() or {}
        state = EngineState()
        for b in raw.get("boosts", []):
            try:
                state.boosts[b["room"]] = Boost(
                    room=b["room"],
                    temp=float(b["temp"]),
                    until=datetime.fromisoformat(b["until"]),
                    start=datetime.fromisoformat(b["start"]) if b.get("start") else None,
                )
            except (KeyError, ValueError, TypeError):
                continue
        for o in raw.get("overrides", []):
            try:
                state.overrides[o["room"]] = ManualOverride(room=o["room"], temp=float(o["temp"]), since=datetime.fromisoformat(o["since"]))
            except (KeyError, ValueError, TypeError):
                continue
        log = ChangeLog.from_list(raw.get("changes", []))
        return state, log

    async def save(self, state: EngineState, log: ChangeLog) -> None:
        await self._store.async_save(
            {
                "boosts": [
                    {"room": b.room, "temp": b.temp, "until": b.until.isoformat(), "start": b.start.isoformat() if b.start else None}
                    for b in state.boosts.values()
                ],
                "overrides": [{"room": o.room, "temp": o.temp, "since": o.since.isoformat()} for o in state.overrides.values()],
                "changes": log.to_list(),
            }
        )
