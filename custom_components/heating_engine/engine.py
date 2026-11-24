"""The engine: one evaluation loop, the apply path, change detection, health.

Everything that decides a temperature lives in `heating_core`. This file only
feeds it (config, runtime state, calendar events, the clock) and acts on what it
says: write to the valve, read back, notice what it did not do itself, and say
loudly when something is wrong.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_ACTION,
    ATTR_HVAC_MODE,
    ATTR_TEMPERATURE,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_TEMPERATURE,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    API_VERSION,
    CALENDAR_LOOKAHEAD,
    CALENDAR_LOOKBACK,
    CALENDAR_REFRESH,
    DEFAULT_BOOST_MINUTES,
    EVALUATE_EVERY,
    HEARTBEAT_STALE_AFTER,
    LONG_BOOST_NOTICE_AFTER,
    MAX_PLAN_DAYS,
    MODE_CONTROL,
    MODE_OBSERVE,
    PROBLEM_CALENDAR,
    PROBLEM_EVALUATION,
    PROBLEM_LONG_BOOST,
    PROBLEM_NOT_RESPONDING,
    PROBLEM_UNAVAILABLE,
    SIGNAL_CONFIG_CHANGED,
    SIGNAL_UPDATED,
)
from .heating_core import (
    CONFIG_DETAIL,
    HOLD_KINDS,
    Boost,
    CalendarEvent,
    Config,
    EngineState,
    ExceptionNight,
    Hold,
    ManualOverride,
    Request,
    RequestLog,
    Segment,
    boost_detail,
    cancel_boost_detail,
    cancel_hold_detail,
    clear_exception_detail,
    exception_detail,
    hold_detail,
    next_schedule_change,
    override_end,
    person_here_tonight,
    resolve_now,
    resolve_plan,
    write_cause,
)
from .calendar_read import read_calendar
from .heating_core.activity import (
    ACTION_BOOST,
    ACTION_CANCEL_BOOST,
    ACTION_CANCEL_HOLD,
    ACTION_CLEAR_EXCEPTION,
    ACTION_CONFIG,
    ACTION_HOLD,
    ACTION_SET_EXCEPTION,
    CAUSE_BOOST,
    CAUSE_HOLD,
    CAUSE_RESTORE,
)
from .heating_core.apply import (
    FAILED,
    HEAT,
    MANUAL_SETPOINT,
    MODE_CHANGE,
    OWN,
    RETRY,
    WAITING,
    ApplyTracker,
    ChangeLog,
    ChangeLogEntry,
    needs_write,
)
from .heating_core.validate import ValidationError, check_temp, parse_and_validate
from .storage import ConfigStore, StateStore, StoredState

_LOGGER = logging.getLogger(__name__)

LOG_LIMIT = 200


class RevConflict(Exception):
    def __init__(self, current_rev: int) -> None:
        super().__init__(f"config has moved on to rev {current_rev}")
        self.current_rev = current_rev


class UnknownRoom(Exception):
    pass


class UnknownPerson(Exception):
    pass


@dataclass
class RoomStatus:
    """What the per-room sensor shows. Rebuilt on every evaluation."""

    room_id: str
    room_name: str
    climate: str
    mode: str = MODE_OBSERVE
    target: float | None = None
    reason_code: str | None = None
    reason_text: str | None = None
    rule: int | None = None
    until: datetime | None = None
    confirmed: bool = False
    available: bool = False
    current_setpoint: float | None = None
    current_mode: str | None = None
    current_temperature: float | None = None
    would_write: bool = False
    pending_write: bool = False
    last_write: datetime | None = None
    problem: str | None = None
    boost_until: datetime | None = None
    hvac_action: str | None = None  # the climate entity's own hvac_action, verbatim
    hold: Hold | None = None


@dataclass
class Problem:
    kind: str
    room: str | None
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "room": self.room, "text": self.text}


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _setpoint_and_mode(state: State | None) -> tuple[bool, float | None, str | None]:
    """(available, setpoint, hvac mode) from a climate entity's state."""
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return False, None, None
    return True, _float_or_none(state.attributes.get(ATTR_TEMPERATURE)), state.state


def _hvac_action(state: State | None) -> str | None:
    """The climate entity's hvac_action (heating, idle, off...), passed through as
    it is; None when the entity does not report one."""
    if state is None:
        return None
    value = state.attributes.get(ATTR_HVAC_ACTION)
    return str(value) if value is not None else None


class HeatingEngine:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self._config_store = ConfigStore(hass)
        self._state_store = StateStore(hass)
        self.config: Config
        self.state = EngineState()
        self.changes = ChangeLog(limit=LOG_LIMIT)
        self.tracker = ApplyTracker()
        self.rooms: dict[str, RoomStatus] = {}
        self.people_here: dict[str, tuple[bool, str, date | None]] = {}
        self.calendar_events: dict[str, list[CalendarEvent]] = {}
        self.calendar_ok: bool | None = None  # None = nothing fetched yet
        self.calendar_error: str | None = None
        self._calendar_fetched_at: datetime | None = None
        self._calendars_unavailable: set[str] = set()  # warned about once, until they come back
        self.last_evaluated: datetime | None = None
        self.started_at: datetime | None = None
        self.last_error: str | None = None
        self.consecutive_errors = 0
        self.evaluations = 0
        self.write_failures = 0
        self.writes_sent = 0
        self.problems: list[Problem] = []
        self.would_write_log: list[dict[str, Any]] = []
        self.write_log: list[dict[str, Any]] = []
        self.requests = RequestLog(limit=LOG_LIMIT)  # every accepted request, with who asked
        self._last_would: dict[str, tuple[float, str | None]] = {}
        # room id -> (setpoint to put back once a boost or hold on an observing room
        # has ended, the source of that boost or hold)
        self._restore: dict[str, tuple[float, str | None]] = {}
        self._state_dirty = False
        self._lock = asyncio.Lock()
        self._unsub: list[Callable[[], None]] = []
        self._unsub_states: Callable[[], None] | None = None
        self._tz = dt_util.get_default_time_zone()

    # ------------------------------------------------------------------ lifecycle

    async def async_start(self) -> None:
        today = dt_util.now().date()
        self.config = await self._config_store.load(today)
        stored = await self._state_store.load()
        self.state = stored.state
        self.changes = stored.changes
        self.calendar_events = stored.calendars
        self.would_write_log = stored.would_write
        self.write_log = stored.writes
        self.requests = stored.requests
        if self.calendar_events:
            self.calendar_ok = None  # last known, not yet confirmed this run
            _LOGGER.info("Using the last known calendar events (%s) until the first read succeeds", ", ".join(self.calendar_events))
        self.started_at = dt_util.utcnow()
        _LOGGER.info(
            "Heating engine starting: config rev %s, %d rooms (%d in control), %d people, api_version %s",
            self.config.rev,
            len(self.config.rooms),
            sum(1 for r in self.config.rooms if r.control),
            len(self.config.people),
            API_VERSION,
        )
        self._subscribe_states()
        self._unsub.append(async_track_time_interval(self.hass, self._on_timer, EVALUATE_EVERY))
        await self.async_evaluate()

    async def async_stop(self) -> None:
        for unsub in self._unsub:
            unsub()
        self._unsub.clear()
        if self._unsub_states:
            self._unsub_states()
            self._unsub_states = None
        await self._save_state()

    async def _save_state(self) -> None:
        await self._state_store.save(
            StoredState(
                state=self.state,
                changes=self.changes,
                calendars=self.calendar_events,
                would_write=self.would_write_log,
                writes=self.write_log,
                requests=self.requests,
            )
        )

    def _record_request(
        self,
        now: datetime,
        action: str,
        detail: str,
        *,
        room: str | None = None,
        person: str | None = None,
        source: str | None = None,
        user: str | None = None,
    ) -> None:
        """Every accepted request goes in the activity log, write or no write."""
        self.requests.add(Request(when=now, action=action, detail=detail, room=room, person=person, source=source, user=user))
        self._state_dirty = True
        _LOGGER.info("Request: %s %s %s (source %s, user %s)", action, room or person or "", detail, source or "-", user or "-")

    def _subscribe_states(self) -> None:
        if self._unsub_states:
            self._unsub_states()
            self._unsub_states = None
        entities: list[str] = []
        for r in self.config.rooms:
            if r.climate:
                entities.append(r.climate)
            entities.extend(e for e in r.watch if e)
        if entities:
            self._unsub_states = async_track_state_change_event(self.hass, entities, self._on_climate_change)

    # ------------------------------------------------------------------ config

    async def async_set_config(self, raw: dict[str, Any], base_rev: int, *, source: str | None = None, user: str | None = None) -> int:
        """Replace the whole document. Rejects a stale base_rev or an invalid document."""
        if base_rev != self.config.rev:
            raise RevConflict(self.config.rev)
        cfg = parse_and_validate(raw)  # raises ValidationError with every message
        cfg.rev = base_rev + 1
        self._record_request(dt_util.utcnow(), ACTION_CONFIG, CONFIG_DETAIL, source=source, user=user)
        await self._adopt_config(cfg)
        return cfg.rev

    async def _adopt_config(self, cfg: Config) -> None:
        old_rooms = {r.id for r in self.config.rooms}
        self.config = cfg
        await self._config_store.save(cfg)
        # overrides, boosts and holds for rooms that no longer exist are dropped
        for rid in list(self.state.overrides):
            if cfg.room(rid) is None:
                del self.state.overrides[rid]
        for rid in list(self.state.boosts):
            if cfg.room(rid) is None:
                del self.state.boosts[rid]
        for rid in list(self.state.holds):
            if cfg.room(rid) is None:
                del self.state.holds[rid]
        for rid in old_rooms - {r.id for r in cfg.rooms}:
            self.rooms.pop(rid, None)
            self._last_would.pop(rid, None)
        self._subscribe_states()
        _LOGGER.info("Config accepted: rev %s, %d rooms (%d in control), %d people", cfg.rev, len(cfg.rooms), sum(1 for r in cfg.rooms if r.control), len(cfg.people))
        async_dispatcher_send(self.hass, SIGNAL_CONFIG_CHANGED)
        await self.async_evaluate()

    async def _bump_config(self) -> None:
        """An engine-made change to the document (exceptions from a service)."""
        self.config.rev += 1
        await self._config_store.save(self.config)
        async_dispatcher_send(self.hass, SIGNAL_CONFIG_CHANGED)
        await self.async_evaluate()

    def _prune_exceptions(self, today: date) -> bool:
        cutoff = today - timedelta(days=1)
        keep = [e for e in self.config.exceptions if e.night >= cutoff]
        if len(keep) != len(self.config.exceptions):
            self.config.exceptions = keep
            return True
        return False

    # ------------------------------------------------------------------ services

    async def async_boost(self, room_id: str, temp: float | None, minutes: int | None, *, source: str | None = None, user: str | None = None) -> None:
        room = self.config.room(room_id)
        if room is None:
            raise UnknownRoom(room_id)
        now = dt_util.utcnow()
        t = float(temp) if temp is not None else room.temps.comfort
        mins = int(minutes) if minutes else DEFAULT_BOOST_MINUTES
        # A boost (like a hold) is written even while a room is only observing.
        # Remember what the valve held so it can be put back after.
        restore = self._restore_point(room)
        self.state.boosts[room_id] = Boost(room=room_id, temp=t, until=now + timedelta(minutes=mins), start=now, restore_temp=restore, source=source)
        self._restore.pop(room_id, None)
        self._state_dirty = True
        _LOGGER.info("Boost: %s to %s for %d min%s", room.name, t, mins, "" if room.control else f" (observing room; will restore {restore} after)")
        self._record_request(now, ACTION_BOOST, boost_detail(t, mins), room=room_id, source=source, user=user)
        await self.async_evaluate()

    async def async_cancel_boost(self, room_id: str, *, source: str | None = None, user: str | None = None) -> None:
        room = self.config.room(room_id)
        if room is None:
            raise UnknownRoom(room_id)
        boost = self.state.boosts.pop(room_id, None)
        if boost is not None:
            self._state_dirty = True
            self._schedule_restore(room, boost.restore_temp, boost.source)
            _LOGGER.info("Boost cancelled: %s", room.name)
            self._record_request(dt_util.utcnow(), ACTION_CANCEL_BOOST, cancel_boost_detail(room_id in self.state.holds), room=room_id, source=source, user=user)
        await self.async_evaluate()

    async def async_hold(
        self,
        room_id: str,
        kind: str,
        temp: float | None,
        until: str | None,
        *,
        source: str | None = None,
        user: str | None = None,
    ) -> Hold:
        """Set a hold from the dashboard (rule 2), replacing any hold on the room
        and any change made at the radiator. `until` is 'next_change',
        'indefinite' or an ISO datetime; None picks the kind's natural end."""
        room = self.config.room(room_id)
        if room is None:
            raise UnknownRoom(room_id)
        if kind not in HOLD_KINDS:
            raise ValidationError([f"kind must be one of {', '.join(HOLD_KINDS)}"])
        now = dt_util.utcnow()
        hold_temp: float | None = None
        if kind == "temp":
            if temp is None:
                raise ValidationError(["a temp hold needs a temp"])
            errors: list[str] = []
            check_temp(errors, "temp", float(temp))
            if errors:
                raise ValidationError(errors)
            hold_temp = float(temp)
        until_dt = self._hold_end(room, kind, until, now)
        # A hold (like a boost) is written even while a room is only observing.
        restore = self._restore_point(room)
        hold = Hold(room=room_id, kind=kind, until=until_dt, set_at=now, temp=hold_temp, restore_temp=restore, source=source)
        self.state.holds[room_id] = hold
        # the dashboard's word replaces an earlier change at the radiator
        if self.state.overrides.pop(room_id, None) is not None:
            _LOGGER.info("Hold replaces the change made at the radiator in %s", room.name)
        self._restore.pop(room_id, None)
        self._state_dirty = True
        _LOGGER.info(
            "Hold: %s %s at %s until %s%s",
            room.name,
            kind,
            hold.target(room),
            until_dt.isoformat() if until_dt else "cancelled",
            "" if room.control else f" (observing room; will restore {restore} after)",
        )
        self._record_request(now, ACTION_HOLD, hold_detail(kind, hold.target(room), until_dt, now, self._tz), room=room_id, source=source, user=user)
        await self.async_evaluate()
        return hold

    async def async_cancel_hold(self, room_id: str, *, source: str | None = None, user: str | None = None) -> None:
        room = self.config.room(room_id)
        if room is None:
            raise UnknownRoom(room_id)
        hold = self.state.holds.pop(room_id, None)
        if hold is not None:
            self._state_dirty = True
            self._schedule_restore(room, hold.restore_temp, hold.source)
            _LOGGER.info("Hold cancelled: %s", room.name)
            self._record_request(dt_util.utcnow(), ACTION_CANCEL_HOLD, cancel_hold_detail(room_id in self.state.boosts), room=room_id, source=source, user=user)
        await self.async_evaluate()

    def _hold_end(self, room: Any, kind: str, until: str | None, now: datetime) -> datetime | None:
        """Work out when a hold ends. None = until it is cancelled."""
        text = (str(until).strip() if until is not None else "") or ("indefinite" if kind in ("eco", "frost") else "next_change")
        if text == "indefinite":
            return None
        if text == "next_change":
            # None when the schedule never changes inside the look-ahead: hold until cancelled
            return next_schedule_change(self.config, room, now, self.calendar_events, self._tz)
        dt = dt_util.parse_datetime(text)
        if dt is None:
            raise ValidationError(["until must be 'next_change', 'indefinite' or an ISO 8601 datetime"])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=self._tz)
        dt = dt_util.as_utc(dt)
        if dt <= now:
            raise ValidationError(["until is in the past"])
        return dt

    def _restore_point(self, room: Any) -> float | None:
        """For a room in observe mode: the setpoint the valve held before the
        engine's first explicit write (boost or hold). An existing boost or hold
        already knows it; otherwise it is what the valve shows now."""
        if room.control:
            return None
        for existing in (self.state.boosts.get(room.id), self.state.holds.get(room.id)):
            if existing is not None and existing.restore_temp is not None:
                return existing.restore_temp
        _avail, setpoint, _mode = _setpoint_and_mode(self.hass.states.get(room.climate))
        return setpoint

    def _schedule_restore(self, room: Any, restore_temp: float | None, source: str | None = None) -> None:
        """A boost or hold on an observing room has ended: put the valve back,
        unless the other kind of request is still running (it carries the same
        restore value and will put it back when it ends). The restore write is
        filed under the source of the boost or hold that ended."""
        if room.control or restore_temp is None:
            return
        if room.id in self.state.boosts or room.id in self.state.holds:
            return
        self._restore[room.id] = (restore_temp, source)

    async def async_set_exception(self, person_id: str, night: date, here: bool, *, source: str | None = None, user: str | None = None) -> None:
        person = self.config.person(person_id)
        if person is None:
            raise UnknownPerson(person_id)
        if person.kind == "calendar":
            raise ValidationError([f"{person.name} follows a calendar; edit the calendar instead"])
        self.config.exceptions = [e for e in self.config.exceptions if not (e.person == person_id and e.night == night)]
        self.config.exceptions.append(ExceptionNight(person=person_id, night=night, here=here))
        _LOGGER.info("Exception: %s %s on %s", person.name, "here" if here else "away", night.isoformat())
        self._record_request(dt_util.utcnow(), ACTION_SET_EXCEPTION, exception_detail(here, night), person=person_id, source=source, user=user)
        await self._bump_config()

    async def async_clear_exception(self, person_id: str, night: date, *, source: str | None = None, user: str | None = None) -> None:
        if self.config.person(person_id) is None:
            raise UnknownPerson(person_id)
        before = len(self.config.exceptions)
        self.config.exceptions = [e for e in self.config.exceptions if not (e.person == person_id and e.night == night)]
        if len(self.config.exceptions) != before:
            self._record_request(dt_util.utcnow(), ACTION_CLEAR_EXCEPTION, clear_exception_detail(night), person=person_id, source=source, user=user)
            await self._bump_config()

    # ------------------------------------------------------------------ plan

    def plan(self, start: datetime, end: datetime) -> list[Segment]:
        start = start.astimezone(self._tz)
        end = min(end.astimezone(self._tz), start + timedelta(days=MAX_PLAN_DAYS))
        return resolve_plan(self.config, start, end, self.calendar_events, self.state)

    # ------------------------------------------------------------------ the loop

    @callback
    def _on_timer(self, _now: datetime) -> None:
        self.hass.async_create_task(self.async_evaluate())

    async def async_evaluate(self) -> None:
        async with self._lock:
            try:
                await self._evaluate()
            except Exception as exc:  # noqa: BLE001 - the loop must survive and report
                self.consecutive_errors += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
                _LOGGER.exception("Evaluation failed (%d in a row)", self.consecutive_errors)
            else:
                self.consecutive_errors = 0
                self.last_error = None
            finally:
                if self._state_dirty:
                    self._state_dirty = False
                    try:
                        await self._save_state()
                    except Exception:  # noqa: BLE001
                        _LOGGER.exception("Could not save runtime state")
                async_dispatcher_send(self.hass, SIGNAL_UPDATED)

    async def _evaluate(self) -> None:
        now = dt_util.utcnow()
        now_local = now.astimezone(self._tz)
        problems: list[Problem] = []

        if self._prune_exceptions(now_local.date()):
            await self._config_store.save(self.config)

        await self._maybe_refresh_calendars(now)
        if self.calendar_ok is False:
            known = "using the last known trips" if self.calendar_events else "nobody is treated as away"
            problems.append(Problem(PROBLEM_CALENDAR, None, f"Can't check the calendar ({known}): {self.calendar_error}"))

        self._expire_runtime(now)

        current = resolve_now(self.config, now_local, self.calendar_events, self.state)

        for room in self.config.rooms:
            seg = current.get(room.id)
            status = self.rooms.get(room.id) or RoomStatus(room_id=room.id, room_name=room.name, climate=room.climate)
            status.room_name = room.name
            status.climate = room.climate
            status.mode = MODE_CONTROL if room.control else MODE_OBSERVE
            status.problem = None
            status.would_write = False
            boost = self.state.boosts.get(room.id)
            status.boost_until = boost.until if boost else None
            hold = self.state.holds.get(room.id)
            status.hold = hold
            if boost and boost.start and now - boost.start > LONG_BOOST_NOTICE_AFTER:
                problems.append(Problem(PROBLEM_LONG_BOOST, room.id, f"{room.name} has been boosted for over {int((now - boost.start).total_seconds() // 3600)} hours"))

            if seg is not None:
                status.target = seg.temp
                status.reason_code = seg.reason_code
                status.reason_text = seg.reason_text
                status.rule = seg.rule
                status.until = seg.until

            state = self.hass.states.get(room.climate)
            available, setpoint, mode = _setpoint_and_mode(state)
            status.available = available
            status.current_setpoint = setpoint
            status.current_mode = mode
            status.current_temperature = self._room_temperature(room, state)
            status.hvac_action = _hvac_action(state)

            if not available:
                status.confirmed = False
                status.problem = PROBLEM_UNAVAILABLE
                problems.append(Problem(PROBLEM_UNAVAILABLE, room.id, f"{room.name}: {room.climate} is unavailable"))
                self.rooms[room.id] = status
                continue

            # read-back of an earlier write
            outcome = self.tracker.observe(room.climate, now, setpoint, mode)
            if outcome == FAILED:
                self.write_failures += 1
                status.problem = PROBLEM_NOT_RESPONDING
                problems.append(Problem(PROBLEM_NOT_RESPONDING, room.id, f"{room.name} is not responding: {room.climate} did not take the setpoint after 3 attempts"))
                _LOGGER.error("Not responding: %s (%s) did not take the setpoint after 3 attempts", room.name, room.climate)
            elif outcome == RETRY and seg is not None:
                pending = self.tracker.pending[room.climate]
                _LOGGER.warning("Retrying write to %s (%s): attempt %d", room.name, room.climate, pending.attempts + 1)
                cause, source = self._cause_and_source(room, status.reason_code)
                await self._write(room, pending.temp, now, status, cause=cause, source=source)

            status.pending_write = self.tracker.is_pending(room.climate)

            # a boost or hold on an observing room has just ended: put the valve back
            restore = self._restore.pop(room.id, None)
            if restore is not None and not room.control:
                restore_temp, restore_source = restore
                if needs_write(restore_temp, setpoint, mode):
                    await self._write(
                        room,
                        restore_temp,
                        now,
                        status,
                        reason="Boost or hold ended, back to the previous setting",
                        cause=CAUSE_RESTORE,
                        source=restore_source,
                    )
                    status.pending_write = True
                self.rooms[room.id] = status
                continue

            # does the valve differ from what we want?
            if seg is not None:
                differs = needs_write(seg.temp, setpoint, mode)
                status.confirmed = not differs
                if differs:
                    in_flight = status.pending_write and abs(self.tracker.pending[room.climate].temp - seg.temp) <= self.tracker.tolerance
                    if in_flight:
                        pass  # written already, waiting for the valve to report it
                    elif room.control or boost is not None or hold is not None:
                        # control mode, or an explicit boost or hold: the writes an observing room gets
                        cause, source = self._cause_and_source(room, seg.reason_code)
                        await self._write(room, seg.temp, now, status, cause=cause, source=source)
                        status.pending_write = True
                    else:
                        status.would_write = True
                        self._log_would_write(room, setpoint, mode, seg, now)
            self.rooms[room.id] = status

        # per-person "here tonight"
        today = now_local.date()
        self.people_here = {}
        for p in self.config.people:
            try:
                self.people_here[p.id] = person_here_tonight(self.config, p, today, self._tz, self.calendar_events)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Could not work out whether %s is here: %s", p.name, exc)

        self.problems = problems
        self.last_evaluated = now
        self.evaluations += 1

    def _room_temperature(self, room: Any, climate_state: State | None) -> float | None:
        if room.temp_sensor:
            st = self.hass.states.get(room.temp_sensor)
            if st is not None and st.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                return _float_or_none(st.state)
        if climate_state is not None:
            return _float_or_none(climate_state.attributes.get("current_temperature"))
        return None

    def _expire_runtime(self, now: datetime) -> None:
        for rid, b in list(self.state.boosts.items()):
            if b.until <= now:
                del self.state.boosts[rid]
                self._state_dirty = True
                room = self.config.room(rid)
                if room is not None:
                    self._schedule_restore(room, b.restore_temp, b.source)
                _LOGGER.info("Boost ended: %s", rid)
        for rid, h in list(self.state.holds.items()):
            if h.until is not None and h.until <= now:
                del self.state.holds[rid]
                self._state_dirty = True
                room = self.config.room(rid)
                if room is not None:
                    self._schedule_restore(room, h.restore_temp, h.source)
                _LOGGER.info("Hold ended: %s", rid)
        for rid, o in list(self.state.overrides.items()):
            room = self.config.room(rid)
            if room is None:
                del self.state.overrides[rid]
                self._state_dirty = True
                continue
            end, _ = override_end(self.config, room, o, self.calendar_events, self._tz)
            if end <= now:
                del self.state.overrides[rid]
                self._state_dirty = True
                _LOGGER.info("Manual change in %s expired at the schedule change", room.name)

    # ------------------------------------------------------------------ writes

    def _cause_and_source(self, room: Any, reason_code: str | None) -> tuple[str, str | None]:
        """What a write to match the resolved target is for, and who asked for
        it: the source of the boost or hold behind it, else None."""
        cause = write_cause(reason_code)
        if cause == CAUSE_BOOST and (boost := self.state.boosts.get(room.id)) is not None:
            return cause, boost.source
        if cause == CAUSE_HOLD and (hold := self.state.holds.get(room.id)) is not None:
            return cause, hold.source
        return cause, None

    async def _write(
        self,
        room: Any,
        temp: float,
        now: datetime,
        status: RoomStatus,
        reason: str | None = None,
        cause: str | None = None,
        source: str | None = None,
    ) -> None:
        """Send mode heat + temperature, record it as pending, remember it as ours
        on the written entity and on every watched device in the room."""
        reason = reason or status.reason_text
        cause = cause or write_cause(status.reason_code)
        data = {ATTR_ENTITY_ID: room.climate, ATTR_TEMPERATURE: temp, ATTR_HVAC_MODE: HEAT}
        # Record the expectation BEFORE the call. A blocking climate call returns
        # only after the device reports back, and that report is a state change
        # the engine sees while the call is still awaiting. Recording afterwards
        # (v0.2.1 and earlier) made the engine file its own echo as an unexpected
        # change and, for a setpoint echo, install a manual override at the value
        # it had just written. Seen live: a boost that never restored.
        pw = self.tracker.record_write(room.climate, room.id, temp, now)
        for other in room.watch:
            self.tracker.expect(other, temp, now)
        try:
            await self.hass.services.async_call(CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, data, blocking=True)
        except HomeAssistantError as exc:
            _LOGGER.warning("Write to %s (%s) raised %s; will read back and retry", room.name, room.climate, exc)
        self.writes_sent += 1
        status.last_write = now
        entry = {
            "when": now.isoformat(),
            "room": room.id,
            "entity_id": room.climate,
            "temp": temp,
            "attempt": pw.attempts,
            "reason": reason,
            "cause": cause,
            "source": source,
        }
        self.write_log.append(entry)
        del self.write_log[:-LOG_LIMIT]
        self._state_dirty = True  # the write log survives a restart
        _LOGGER.info("write: %s %s -> %s/heat (attempt %d) %s [%s%s]", room.name, room.climate, temp, pw.attempts, reason or "", cause, f", {source}" if source else "")

    def _log_would_write(self, room: Any, setpoint: float | None, mode: str | None, seg: Segment, now: datetime) -> None:
        key = (seg.temp, seg.reason_code)
        if self._last_would.get(room.id) == key:
            return  # same would-write as last time: say it once, not every minute
        self._last_would[room.id] = key
        entry = {
            "when": now.isoformat(),
            "room": room.id,
            "entity_id": room.climate,
            "from": setpoint,
            "from_mode": mode,
            "to": seg.temp,
            "reason": seg.reason_text,
        }
        self.would_write_log.append(entry)
        del self.would_write_log[:-LOG_LIMIT]
        self._state_dirty = True  # the would-write log survives a restart
        _LOGGER.info("would write: %s %s %s/%s -> %s/heat (%s)", room.name, room.climate, setpoint, mode, seg.temp, seg.reason_text)

    # ------------------------------------------------------------------ changes we did not make

    @callback
    def _on_climate_change(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        old = event.data.get("old_state")
        new = event.data.get("new_state")
        room = next((r for r in self.config.rooms if r.climate == entity_id or entity_id in r.watch), None)
        if room is None:
            return
        _old_avail, old_sp, old_mode = _setpoint_and_mode(old)
        _new_avail, new_sp, new_mode = _setpoint_and_mode(new)
        now = dt_util.utcnow()
        verdict = self.tracker.classify_change(entity_id, now, old_sp, new_sp, old_mode, new_mode)
        if verdict in (MANUAL_SETPOINT, MODE_CHANGE):
            old_txt = f"{old_sp}/{old_mode}"
            new_txt = f"{new_sp}/{new_mode}"
            self.changes.add(ChangeLogEntry(when=now, room=room.id, entity_id=entity_id, kind=verdict, old=old_txt, new=new_txt))
            self._state_dirty = True
            if verdict == MANUAL_SETPOINT and new_sp is not None:
                # a hand on the radiator is the latest word: it replaces a dashboard hold
                if self.state.holds.pop(room.id, None) is not None:
                    self._restore.pop(room.id, None)
                    _LOGGER.info("Change at the radiator replaces the hold in %s", room.name)
                self.state.overrides[room.id] = ManualOverride(room=room.id, temp=new_sp, since=now)
                _LOGGER.warning("Unexpected setpoint change in %s: %s %s -> %s (not written by the engine; holding until the next schedule change)", room.name, entity_id, old_txt, new_txt)
            else:
                _LOGGER.warning("Unexpected mode change in %s: %s %s -> %s (not written by the engine)", room.name, entity_id, old_txt, new_txt)
        elif verdict == OWN:
            _LOGGER.debug("Own write reported back on %s: %s/%s", entity_id, new_sp, new_mode)
        self.hass.async_create_task(self.async_evaluate())

    # ------------------------------------------------------------------ calendars

    async def _maybe_refresh_calendars(self, now: datetime) -> None:
        calendars = sorted({p.calendar for p in self.config.people if p.kind == "calendar" and p.calendar})
        if not calendars:
            self.calendar_ok = None
            return
        if self._calendar_fetched_at is not None:
            # after a failure with nothing known, try again every minute; otherwise every 5
            wait = timedelta(minutes=1) if (self.calendar_ok is False and not self.calendar_events) else CALENDAR_REFRESH
            if now - self._calendar_fetched_at < wait:
                return
        # Read each calendar entity directly, the way HA's own GET /api/calendars/<entity>
        # does. The calendar.get_events service goes through the entity service layer,
        # which drops an entity whose state is `unavailable` and then fails with
        # "did not match any entities", while the entity itself can still answer from
        # what it last fetched. See calendar_read.py.
        start = now - CALENDAR_LOOKBACK
        end = now + CALENDAR_LOOKAHEAD
        fetched: dict[str, list[CalendarEvent]] = {}
        unavailable: set[str] = set()
        try:
            for cal in calendars:
                read = await read_calendar(self.hass, cal, start, end, self._tz)
                fetched[cal] = [CalendarEvent(start=s, end=e) for s, e in read.spans]
                if not read.available:
                    unavailable.add(cal)
        except Exception as exc:  # noqa: BLE001
            self.calendar_ok = False
            self.calendar_error = f"{type(exc).__name__}: {exc}"
            _LOGGER.warning("Calendar read failed, keeping the last known events: %s", self.calendar_error)
            self._calendar_fetched_at = now  # do not hammer a broken calendar every minute
            return
        newly_unavailable = unavailable - self._calendars_unavailable
        if newly_unavailable:
            _LOGGER.warning(
                "%s is unavailable in Home Assistant; reading what it last fetched. Check its integration.",
                ", ".join(sorted(newly_unavailable)),
            )
        for cal in self._calendars_unavailable - unavailable:
            _LOGGER.info("%s is available again", cal)
        self._calendars_unavailable = unavailable
        if fetched != self.calendar_events:
            self._state_dirty = True  # persist the last known events for the next restart
        self.calendar_events = fetched
        self.calendar_ok = True
        self.calendar_error = None
        self._calendar_fetched_at = now

    # ------------------------------------------------------------------ health

    @property
    def healthy(self) -> bool:
        if self.last_evaluated is None:
            return False
        if dt_util.utcnow() - self.last_evaluated > HEARTBEAT_STALE_AFTER:
            return False
        return self.consecutive_errors < 3

    def health_attributes(self) -> dict[str, Any]:
        now = dt_util.utcnow()
        return {
            "api_version": API_VERSION,
            "config_rev": self.config.rev,
            "last_evaluated": self.last_evaluated.isoformat() if self.last_evaluated else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "evaluations": self.evaluations,
            "consecutive_errors": self.consecutive_errors,
            "last_error": self.last_error,
            "unexpected_changes_24h": self.changes.count_since(now - timedelta(hours=24)),
            "unexpected_changes_total": len(self.changes.entries),
            "writes_sent": self.writes_sent,
            "write_failures": self.write_failures,
            "pending_writes": len(self.tracker.pending),
            "calendar_ok": self.calendar_ok,
            "rooms": len(self.config.rooms),
            "rooms_in_control": sum(1 for r in self.config.rooms if r.control),
            "rooms_observing": sum(1 for r in self.config.rooms if not r.control),
            "boosts_active": len(self.state.boosts),
            "holds_active": len(self.state.holds),
            "problems": [p.to_dict() for p in self.problems],
            "problem_count": len(self.problems),
        }

    def recent_log(self) -> dict[str, Any]:
        return {
            "would_write": list(self.would_write_log),
            "writes": list(self.write_log),
            "unexpected_changes": self.changes.to_list(),
            "requests": self.requests.to_list(),
        }
