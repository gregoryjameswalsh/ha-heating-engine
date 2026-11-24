"""Runtime state to and from a JSON-friendly dict.

Boosts, holds and manual overrides survive a restart. The shell stores the
result of `state_to_dict` and feeds `state_from_dict` on start. An entry that
cannot be read (a hand-edited file, an older shape) is skipped, never fatal.
Nothing here is part of the config document or its `rev`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .model import HOLD_KINDS, Boost, EngineState, Hold, ManualOverride


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _float_or_none(value: Any) -> float | None:
    return float(value) if value is not None else None


def state_to_dict(state: EngineState) -> dict[str, Any]:
    return {
        "boosts": [
            {"room": b.room, "temp": b.temp, "until": b.until.isoformat(), "start": _iso(b.start), "restore_temp": b.restore_temp}
            for b in state.boosts.values()
        ],
        "holds": [
            {"room": h.room, "kind": h.kind, "temp": h.temp, "until": _iso(h.until), "set_at": h.set_at.isoformat(), "restore_temp": h.restore_temp}
            for h in state.holds.values()
        ],
        "overrides": [{"room": o.room, "temp": o.temp, "since": o.since.isoformat()} for o in state.overrides.values()],
    }


def state_from_dict(raw: dict[str, Any] | None) -> EngineState:
    state = EngineState()
    raw = raw or {}
    for b in raw.get("boosts", []):
        try:
            state.boosts[b["room"]] = Boost(
                room=b["room"],
                temp=float(b["temp"]),
                until=datetime.fromisoformat(b["until"]),
                start=datetime.fromisoformat(b["start"]) if b.get("start") else None,
                restore_temp=_float_or_none(b.get("restore_temp")),
            )
        except (KeyError, ValueError, TypeError):
            continue
    for h in raw.get("holds", []):
        try:
            if h["kind"] not in HOLD_KINDS:
                continue
            state.holds[h["room"]] = Hold(
                room=h["room"],
                kind=h["kind"],
                temp=_float_or_none(h.get("temp")),
                until=datetime.fromisoformat(h["until"]) if h.get("until") else None,
                set_at=datetime.fromisoformat(h["set_at"]),
                restore_temp=_float_or_none(h.get("restore_temp")),
            )
        except (KeyError, ValueError, TypeError):
            continue
    for o in raw.get("overrides", []):
        try:
            state.overrides[o["room"]] = ManualOverride(room=o["room"], temp=float(o["temp"]), since=datetime.fromisoformat(o["since"]))
        except (KeyError, ValueError, TypeError):
            continue
    return state
