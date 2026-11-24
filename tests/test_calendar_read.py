"""The calendar read must work the way Home Assistant's own REST calendar view
does: look the entity up in the calendar component and ask it directly, so that
an entity whose integration is `unavailable` still answers from what it last
fetched, and say in plain words when the entity is missing or mis-named.

This is the Home Assistant shell, not the pure core, so Home Assistant's two
imports are stood in for when it is not installed (the test venv).
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

SHELL = Path(__file__).resolve().parent.parent / "custom_components" / "heating_engine"
TZ = ZoneInfo("Europe/London")

try:  # real Home Assistant, if it happens to be installed
    from homeassistant.exceptions import HomeAssistantError  # type: ignore
except ImportError:  # stand-ins: the two names calendar_read.py imports
    class HomeAssistantError(Exception):
        pass

    _ha = types.ModuleType("homeassistant")
    _components = types.ModuleType("homeassistant.components")
    _calendar = types.ModuleType("homeassistant.components.calendar")
    _calendar.DOMAIN = "calendar"  # type: ignore[attr-defined]
    _exceptions = types.ModuleType("homeassistant.exceptions")
    _exceptions.HomeAssistantError = HomeAssistantError  # type: ignore[attr-defined]
    sys.modules.setdefault("homeassistant", _ha)
    sys.modules.setdefault("homeassistant.components", _components)
    sys.modules.setdefault("homeassistant.components.calendar", _calendar)
    sys.modules.setdefault("homeassistant.exceptions", _exceptions)

_spec = importlib.util.spec_from_file_location("heating_engine_calendar_read", SHELL / "calendar_read.py")
assert _spec is not None and _spec.loader is not None
calendar_read = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = calendar_read  # dataclasses resolve annotations through sys.modules
_spec.loader.exec_module(calendar_read)


# ----------------------------------------------------------------- stand-ins

class FakeEntity:
    def __init__(self, entity_id: str, events: list, available: bool = True) -> None:
        self.entity_id = entity_id
        self.available = available
        self._events = events
        self.calls: list[tuple] = []

    async def async_get_events(self, hass, start, end):
        self.calls.append((start, end))
        return list(self._events)


class FakeComponent:
    def __init__(self, *entities: FakeEntity) -> None:
        self._by_id = {e.entity_id: e for e in entities}

    def get_entity(self, entity_id: str):
        return self._by_id.get(entity_id)

    @property
    def entities(self):
        return list(self._by_id.values())


class FakeStates:
    def __init__(self, *entity_ids: str) -> None:
        self._ids = set(entity_ids)

    def get(self, entity_id: str):
        return object() if entity_id in self._ids else None


def hass_with(component: FakeComponent | None, *state_ids: str):
    data = {} if component is None else {"calendar": component}
    return SimpleNamespace(data=data, states=FakeStates(*state_ids))


def read(hass, entity_id: str):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 31, tzinfo=timezone.utc)
    return asyncio.run(calendar_read.read_calendar(hass, entity_id, start, end, TZ))


# ----------------------------------------------------------------- reading

def test_reads_events_from_the_entity_directly_and_passes_the_window_through():
    ev = SimpleNamespace(
        start=datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc),
        end=datetime(2026, 1, 9, 12, 0, tzinfo=timezone.utc),
    )
    entity = FakeEntity("calendar.studio", [ev])
    result = read(hass_with(FakeComponent(entity), "calendar.studio"), "calendar.studio")
    assert result.spans == [(ev.start, ev.end)]
    assert result.available is True
    assert entity.calls == [(datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 31, tzinfo=timezone.utc))]


def test_an_unavailable_calendar_still_answers_from_what_it_last_had():
    # the entity service layer would drop this entity and fail with
    # "did not match any entities"; the direct read must not
    ev = SimpleNamespace(start=date(2026, 1, 10), end=date(2026, 1, 20))
    entity = FakeEntity("calendar.studio", [ev], available=False)
    result = read(hass_with(FakeComponent(entity), "calendar.studio"), "calendar.studio")
    assert result.spans == [
        (datetime(2026, 1, 10, 0, 0, tzinfo=TZ), datetime(2026, 1, 20, 0, 0, tzinfo=TZ)),
    ]
    assert result.available is False


def test_all_day_events_become_local_midnights_and_naive_datetimes_are_read_in_the_zone():
    events = [
        SimpleNamespace(start=date(2026, 1, 3), end=date(2026, 1, 4)),
        SimpleNamespace(start=datetime(2026, 1, 6, 9, 30), end=datetime(2026, 1, 6, 17, 0)),
    ]
    result = read(hass_with(FakeComponent(FakeEntity("calendar.studio", events))), "calendar.studio")
    assert result.spans == [
        (datetime(2026, 1, 3, tzinfo=TZ), datetime(2026, 1, 4, tzinfo=TZ)),
        (datetime(2026, 1, 6, 9, 30, tzinfo=TZ), datetime(2026, 1, 6, 17, 0, tzinfo=TZ)),
    ]


def test_an_event_without_usable_boundaries_is_skipped_not_fatal():
    events = [
        SimpleNamespace(start="not a date", end=date(2026, 1, 4)),
        SimpleNamespace(start=None, end=None),
        SimpleNamespace(start=date(2026, 1, 8), end=date(2026, 1, 9)),
    ]
    result = read(hass_with(FakeComponent(FakeEntity("calendar.studio", events))), "calendar.studio")
    assert result.spans == [(datetime(2026, 1, 8, tzinfo=TZ), datetime(2026, 1, 9, tzinfo=TZ))]


# ----------------------------------------------------------------- saying why not

def test_calendar_integration_not_loaded_is_said_in_words():
    with pytest.raises(HomeAssistantError, match="calendar integration is not loaded"):
        read(hass_with(None), "calendar.studio")


def test_a_mis_cased_or_padded_entity_id_points_at_the_real_one():
    component = FakeComponent(FakeEntity("calendar.studio", []))
    with pytest.raises(HomeAssistantError, match="did you mean calendar.studio"):
        read(hass_with(component), "calendar.Studio")
    with pytest.raises(HomeAssistantError, match="did you mean calendar.studio"):
        read(hass_with(component), "calendar.studio ")


def test_an_entity_that_exists_but_is_not_a_calendar_is_said_so():
    component = FakeComponent(FakeEntity("calendar.studio", []))
    with pytest.raises(HomeAssistantError, match="exists but is not a calendar entity"):
        read(hass_with(component, "sensor.studio"), "sensor.studio")


def test_an_unknown_entity_is_said_so():
    component = FakeComponent(FakeEntity("calendar.studio", []))
    with pytest.raises(HomeAssistantError, match="not an entity Home Assistant knows"):
        read(hass_with(component), "calendar.attic")


# ----------------------------------------------------------------- as_datetime

@pytest.mark.parametrize("value, expected", [
    (datetime(2026, 1, 6, 9, 30, tzinfo=timezone.utc), datetime(2026, 1, 6, 9, 30, tzinfo=timezone.utc)),
    (datetime(2026, 1, 6, 9, 30), datetime(2026, 1, 6, 9, 30, tzinfo=TZ)),
    (date(2026, 1, 6), datetime(2026, 1, 6, tzinfo=TZ)),
    ("2026-01-06", datetime(2026, 1, 6, tzinfo=TZ)),
    ("2026-01-06T09:30:00+00:00", datetime(2026, 1, 6, 9, 30, tzinfo=timezone.utc)),
    ("2026-01-06T09:30:00", datetime(2026, 1, 6, 9, 30, tzinfo=TZ)),
    ("2026-13-40", None),
    ("soon", None),
    (None, None),
    (42, None),
])
def test_as_datetime(value, expected):
    assert calendar_read.as_datetime(value, TZ) == expected
