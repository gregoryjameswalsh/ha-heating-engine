"""Read a calendar entity the way Home Assistant's own REST view does.

`calendar.get_events` goes through the entity service layer, which quietly drops
an entity whose state is `unavailable` and then fails with "Service call
requested response data but did not match any entities". `GET
/api/calendars/<entity>` does not: it looks the entity up in the calendar
component and asks it for its events directly, and an entity whose integration
has gone unavailable can usually still answer from what it last fetched. The
engine wants the trips, so it reads the way the REST view does, and says in
plain words when it cannot.

This module imports only two names from Home Assistant so that it can be tested
with stand-ins where Home Assistant is not installed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from typing import Any

from homeassistant.components.calendar import DOMAIN as CALENDAR_DOMAIN
from homeassistant.exceptions import HomeAssistantError

Span = tuple[datetime, datetime]


@dataclass(frozen=True)
class CalendarRead:
    spans: list[Span]  # (start, end), aware datetimes
    available: bool  # the entity's own availability; False means it answered from what it last had


def _component(hass: Any) -> Any:
    component = hass.data.get(CALENDAR_DOMAIN)
    if component is None:
        raise HomeAssistantError("the calendar integration is not loaded (no calendar entity has been set up)")
    return component


def find_calendar_entity(hass: Any, entity_id: str) -> Any:
    """The calendar entity object, or a HomeAssistantError that says why not."""
    component = _component(hass)
    entity = component.get_entity(entity_id)
    if entity is not None:
        return entity
    wanted = entity_id.strip().lower()
    for other in component.entities:
        if other.entity_id.lower() == wanted:
            raise HomeAssistantError(f"{entity_id!r} is not a calendar entity; did you mean {other.entity_id}?")
    if hass.states.get(entity_id) is not None:
        raise HomeAssistantError(f"{entity_id} exists but is not a calendar entity")
    raise HomeAssistantError(f"{entity_id} is not an entity Home Assistant knows")


async def read_calendar(hass: Any, entity_id: str, start: datetime, end: datetime, tz: tzinfo) -> CalendarRead:
    """Every event of the calendar entity overlapping [start, end), as aware
    (start, end) pairs. All-day events become local midnights in `tz`."""
    entity = find_calendar_entity(hass, entity_id)
    raw_events = await entity.async_get_events(hass, start, end)
    spans: list[Span] = []
    for raw in raw_events:
        s = as_datetime(getattr(raw, "start", None), tz)
        e = as_datetime(getattr(raw, "end", None), tz)
        if s is not None and e is not None:
            spans.append((s, e))
    return CalendarRead(spans=spans, available=bool(entity.available))


def as_datetime(value: Any, tz: tzinfo) -> datetime | None:
    """An event boundary as an aware datetime. A date (all-day event) is local
    midnight in `tz`; a naive datetime is read in `tz`; an ISO string is parsed
    the same way. None for anything else."""
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=tz)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=tz)
    if isinstance(value, str):
        text = value.strip()
        try:
            if len(text) == 10:
                return datetime.combine(date.fromisoformat(text), datetime.min.time(), tzinfo=tz)
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=tz)
    return None
