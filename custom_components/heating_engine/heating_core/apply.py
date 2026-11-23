"""Applying setpoints honestly (plan section 8.3), without Home Assistant.

The shell asks three questions and this module answers them:

1. `needs_write`: does the valve differ from what we want? Temperature is
   compared with a tolerance; the mode must be `heat`.
2. `ApplyTracker.observe`: after a write, has the valve reported the new value?
   Confirmed within the timeout, or retried, or failed after the last attempt.
   A command that was sent is not a command that worked.
3. `ApplyTracker.classify_change`: when an entity's setpoint or mode changes,
   was that us, or someone at the radiator / another app? Only a change we did
   not make is a manual change (rule 2) and counts on the health sensor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

HEAT = "heat"

TOLERANCE = 0.05  # degrees; setpoints are half-degree steps
CONFIRM_TIMEOUT = timedelta(minutes=2)
MAX_ATTEMPTS = 3
EXPECT_WINDOW = timedelta(minutes=5)  # how long a value we wrote still counts as ours

# observe() outcomes
NONE = "none"  # nothing pending for this entity
WAITING = "waiting"  # written, not yet reported back, still inside the timeout
CONFIRMED = "confirmed"  # the entity reports what we wrote
RETRY = "retry"  # timed out, and an attempt is left: caller should write again
FAILED = "failed"  # timed out on the last attempt: caller should raise a notice

# classify_change() outcomes
OWN = "own"  # a value this tracker wrote recently
MANUAL_SETPOINT = "manual_setpoint"  # a setpoint we did not write
MODE_CHANGE = "mode_change"  # the mode changed and we did not write it
NO_CHANGE = "no_change"  # nothing relevant changed


def same_temp(a: float | None, b: float | None, tolerance: float = TOLERANCE) -> bool:
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tolerance


def needs_write(target_temp: float, current_setpoint: float | None, current_mode: str | None, tolerance: float = TOLERANCE) -> bool:
    """True when the valve's setpoint or mode differs from what the engine wants."""
    if not same_temp(target_temp, current_setpoint, tolerance):
        return True
    return current_mode != HEAT


@dataclass
class PendingWrite:
    entity_id: str
    room: str
    temp: float
    sent_at: datetime
    attempts: int = 1


@dataclass
class _Expected:
    temp: float
    until: datetime


@dataclass
class ApplyTracker:
    confirm_timeout: timedelta = CONFIRM_TIMEOUT
    max_attempts: int = MAX_ATTEMPTS
    expect_window: timedelta = EXPECT_WINDOW
    tolerance: float = TOLERANCE
    pending: dict[str, PendingWrite] = field(default_factory=dict)
    _expected: dict[str, list[_Expected]] = field(default_factory=dict)

    # ---- writes ----

    def record_write(self, entity_id: str, room: str, temp: float, now: datetime) -> PendingWrite:
        """Call right after sending a write. A repeat for the same entity is a retry."""
        prev = self.pending.get(entity_id)
        if prev is not None and same_temp(prev.temp, temp, self.tolerance):
            prev.sent_at = now
            prev.attempts += 1
            pw = prev
        else:
            pw = PendingWrite(entity_id=entity_id, room=room, temp=temp, sent_at=now)
            self.pending[entity_id] = pw
        self._expected.setdefault(entity_id, []).append(_Expected(temp=temp, until=now + self.expect_window))
        return pw

    def expect(self, entity_id: str, temp: float, now: datetime) -> None:
        """Treat `temp` as ours on `entity_id` for the expect window without a
        pending write. For a watched second device that follows the one we wrote."""
        self._expected.setdefault(entity_id, []).append(_Expected(temp=temp, until=now + self.expect_window))

    def observe(self, entity_id: str, now: datetime, setpoint: float | None, mode: str | None) -> str:
        """Read-back. Returns NONE, WAITING, CONFIRMED, RETRY or FAILED."""
        pw = self.pending.get(entity_id)
        if pw is None:
            return NONE
        if same_temp(pw.temp, setpoint, self.tolerance) and mode == HEAT:
            del self.pending[entity_id]
            return CONFIRMED
        if now - pw.sent_at < self.confirm_timeout:
            return WAITING
        if pw.attempts < self.max_attempts:
            return RETRY  # caller writes again and calls record_write, which bumps attempts
        del self.pending[entity_id]
        return FAILED

    def is_pending(self, entity_id: str) -> bool:
        return entity_id in self.pending

    def forget(self, entity_id: str) -> None:
        self.pending.pop(entity_id, None)
        self._expected.pop(entity_id, None)

    # ---- changes we see on the entity ----

    def _prune_expected(self, entity_id: str, now: datetime) -> list[_Expected]:
        live = [e for e in self._expected.get(entity_id, []) if e.until > now]
        if live:
            self._expected[entity_id] = live
        else:
            self._expected.pop(entity_id, None)
        return live

    def classify_change(
        self,
        entity_id: str,
        now: datetime,
        old_setpoint: float | None,
        new_setpoint: float | None,
        old_mode: str | None,
        new_mode: str | None,
    ) -> str:
        """Decide what a state change on `entity_id` was.

        Setpoint changes are judged first. A new setpoint that matches a value
        we wrote inside the expect window is OWN. Any other new setpoint is
        MANUAL_SETPOINT. If only the mode changed: OWN when we have a live
        expectation for the entity (we send `heat` with every write), otherwise
        MODE_CHANGE. Unavailable states arrive as None and are not changes.
        """
        setpoint_changed = (old_setpoint is None) != (new_setpoint is None) or not same_temp(old_setpoint, new_setpoint, self.tolerance)
        mode_changed = old_mode != new_mode
        live = self._prune_expected(entity_id, now)

        if setpoint_changed and new_setpoint is not None:
            if any(same_temp(e.temp, new_setpoint, self.tolerance) for e in live):
                return OWN
            if old_setpoint is None:
                return NO_CHANGE  # came back from unavailable; nothing to judge
            return MANUAL_SETPOINT

        if mode_changed and new_mode is not None and old_mode is not None:
            if new_mode == HEAT and live:
                return OWN
            return MODE_CHANGE

        return NO_CHANGE


@dataclass
class ChangeLogEntry:
    when: datetime
    room: str
    entity_id: str
    kind: str  # MANUAL_SETPOINT or MODE_CHANGE
    old: str
    new: str

    def to_dict(self) -> dict[str, str]:
        return {"when": self.when.isoformat(), "room": self.room, "entity_id": self.entity_id, "kind": self.kind, "old": self.old, "new": self.new}


@dataclass
class ChangeLog:
    """Every setpoint or mode change the engine did not make. Bounded."""

    limit: int = 200
    entries: list[ChangeLogEntry] = field(default_factory=list)

    def add(self, entry: ChangeLogEntry) -> None:
        self.entries.append(entry)
        if len(self.entries) > self.limit:
            del self.entries[: len(self.entries) - self.limit]

    def count_since(self, since: datetime) -> int:
        return sum(1 for e in self.entries if e.when >= since)

    def to_list(self) -> list[dict[str, str]]:
        return [e.to_dict() for e in self.entries]

    @classmethod
    def from_list(cls, raw: list[dict[str, str]], limit: int = 200) -> "ChangeLog":
        log = cls(limit=limit)
        for d in raw[-limit:]:
            try:
                log.entries.append(
                    ChangeLogEntry(
                        when=datetime.fromisoformat(d["when"]),
                        room=str(d.get("room", "")),
                        entity_id=str(d.get("entity_id", "")),
                        kind=str(d.get("kind", "")),
                        old=str(d.get("old", "")),
                        new=str(d.get("new", "")),
                    )
                )
            except (KeyError, ValueError, TypeError):
                continue
        return log
