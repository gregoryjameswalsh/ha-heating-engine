"""The activity log: who asked, in the engine's own words, surviving a restart.

Covers the pure parts: the `source` check, the write cause vocabulary, the
request records and their log, the detail text for each kind of request, the
persistence round trip of the three logs, `source` on boosts and holds, and the
`features` list a page keys off. The Home Assistant wiring that feeds these
(services, REST views, the engine's write path) is not importable here.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from heating_core import (
    API_VERSION,
    CONFIG_DETAIL,
    FEATURES,
    REQUEST_ACTIONS,
    WRITE_CAUSES,
    Boost,
    EngineState,
    Hold,
    Request,
    RequestLog,
    ValidationError,
    boost_detail,
    cancel_boost_detail,
    cancel_hold_detail,
    check_source,
    clear_exception_detail,
    exception_detail,
    hold_detail,
    logs_from_dict,
    logs_to_dict,
    state_from_dict,
    state_to_dict,
    write_cause,
)
from household import make_config, room

TZ = ZoneInfo("Europe/London")
NOW = datetime(2026, 11, 24, 15, 0, tzinfo=timezone.utc)  # Tue, 15:00 UTC = 15:00 GMT


def L(*args: int) -> datetime:
    return datetime(*args, tzinfo=TZ)


# ----------------------------------------------------------------- source

@pytest.mark.parametrize("value", ["heating_page", "a", "x" * 32, "phone_two", "_"])
def test_source_accepts_short_lowercase_tags(value):
    assert check_source(value) == value


def test_source_none_is_none():
    assert check_source(None) is None


@pytest.mark.parametrize("value", ["", "x" * 33, "Heating_Page", "heating-page", "heating page", "page1", 42, ["heating_page"]])
def test_source_rejects_anything_else_in_words(value):
    with pytest.raises(ValidationError) as excinfo:
        check_source(value)
    assert excinfo.value.errors == ["source must be 1 to 32 characters of a-z or _"]


# ----------------------------------------------------------------- write cause

@pytest.mark.parametrize("reason_code, cause", [
    ("boost", "boost"),
    ("hold", "hold"),
    ("manual", "manual"),
    ("warm", "schedule"),
    ("eco", "schedule"),
    ("away", "schedule"),
    ("holiday", "schedule"),
    (None, "schedule"),
])
def test_write_cause_follows_the_resolved_reason(reason_code, cause):
    assert write_cause(reason_code) == cause
    assert cause in WRITE_CAUSES


def test_the_cause_vocabulary_is_the_documented_one():
    assert WRITE_CAUSES == ("schedule", "boost", "hold", "manual", "restore")
    assert REQUEST_ACTIONS == ("boost", "cancel_boost", "hold", "cancel_hold", "set_exception", "clear_exception", "config")


# ----------------------------------------------------------------- request records

def test_request_dict_shape_and_round_trip():
    r = Request(when=NOW, action="hold", detail="Skip until 19:00", room="blue_room", source="heating_page", user="Pip")
    d = r.to_dict()
    assert d == {
        "when": "2026-11-24T15:00:00+00:00",
        "room": "blue_room",
        "person": None,
        "action": "hold",
        "detail": "Skip until 19:00",
        "source": "heating_page",
        "user": "Pip",
    }
    assert Request.from_dict(d) == r


def test_a_request_without_source_or_user_carries_nulls():
    r = Request(when=NOW, action="config", detail=CONFIG_DETAIL)
    d = r.to_dict()
    assert d["room"] is None and d["person"] is None and d["source"] is None and d["user"] is None
    assert Request.from_dict(d) == r


def test_an_unknown_action_cannot_be_read_back():
    with pytest.raises(ValueError):
        Request.from_dict({"when": NOW.isoformat(), "action": "nap", "detail": ""})


def test_request_log_is_oldest_first_and_bounded_at_200():
    log = RequestLog()
    for i in range(250):
        log.add(Request(when=NOW + timedelta(minutes=i), action="boost", detail=f"#{i}", room="attic"))
    assert len(log.entries) == 200
    assert log.entries[0].detail == "#50"
    assert log.entries[-1].detail == "#249"
    assert log.to_list()[0]["detail"] == "#50"


def test_request_log_from_list_skips_garbage_and_keeps_the_newest_200():
    raw = [{"when": (NOW + timedelta(minutes=i)).isoformat(), "action": "boost", "detail": f"#{i}", "room": "attic"} for i in range(210)]
    raw.insert(5, "not a dict")
    raw.insert(9, {"when": "never", "action": "boost", "detail": "bad when"})
    raw.insert(12, {"action": "boost", "detail": "no when"})
    raw.insert(20, {"when": NOW.isoformat(), "action": "nap", "detail": "unknown action"})
    log = RequestLog.from_list(raw)
    assert all(e.action == "boost" for e in log.entries)
    assert len(log.entries) == 200
    assert log.entries[-1].detail == "#209"
    assert RequestLog.from_list(None).entries == []
    assert RequestLog.from_list("junk").entries == []


# ----------------------------------------------------------------- detail text

def test_boost_detail():
    assert boost_detail(21.0, 15) == "Boost to 21° for 15 min"
    assert boost_detail(20.5, 60) == "Boost to 20.5° for 60 min"


def test_cancel_details_say_what_the_room_goes_back_to():
    assert cancel_boost_detail(hold_remains=False) == "Back to the schedule"
    assert cancel_boost_detail(hold_remains=True) == "Boost cancelled, hold resumes"
    assert cancel_hold_detail(boost_running=False) == "Back to the schedule"
    assert cancel_hold_detail(boost_running=True) == "Hold cancelled, boost still running"


def test_hold_details_for_each_kind():
    until = L(2026, 11, 24, 19, 0)
    assert hold_detail("skip", 16.0, until, NOW, TZ) == "Skip until 19:00"
    assert hold_detail("skip", 16.0, None, NOW, TZ) == "Skip until turned back on"
    assert hold_detail("eco", 16.0, None, NOW, TZ) == "Hold at Eco until turned back on"
    assert hold_detail("eco", 16.0, until, NOW, TZ) == "Hold at Eco until 19:00"
    assert hold_detail("frost", 5.0, None, NOW, TZ) == "Frost protection until turned back on"
    assert hold_detail("frost", 5.0, until, NOW, TZ) == "Frost protection until 19:00"
    assert hold_detail("temp", 18.0, until, NOW, TZ) == "Set to 18° until 19:00"
    assert hold_detail("temp", 18.5, None, NOW, TZ) == "Set to 18.5° until turned back on"


def test_hold_detail_until_on_another_day_reads_like_the_reason_text():
    assert hold_detail("skip", 16.0, L(2026, 11, 25, 6, 30), NOW, TZ) == "Skip until 06:30 tomorrow"
    assert hold_detail("temp", 18.0, L(2026, 11, 27, 17, 0), NOW, TZ) == "Set to 18° until Fri 17:00"
    assert hold_detail("eco", 16.0, L(2026, 12, 4, 9, 0), NOW, TZ) == "Hold at Eco until Fri 4 Dec"


def test_exception_details():
    assert exception_detail(False, date(2026, 11, 25)) == "Away on Wed 25 Nov"
    assert exception_detail(True, date(2026, 12, 3)) == "Here on Thu 3 Dec"
    assert clear_exception_detail(date(2026, 11, 25)) == "Back to the rota on Wed 25 Nov"
    assert CONFIG_DETAIL == "Schedule or settings changed"


# ----------------------------------------------------------------- persistence

def test_the_three_logs_round_trip_and_are_bounded():
    would = [{"when": (NOW + timedelta(minutes=i)).isoformat(), "room": "attic", "to": 19.0} for i in range(205)]
    writes = [
        {"when": NOW.isoformat(), "room": "attic", "entity_id": "climate.attic_valve", "temp": 21.0, "attempt": 1, "reason": "Boosted until 16:00", "cause": "boost", "source": "heating_page"},
        {"when": (NOW + timedelta(hours=1)).isoformat(), "room": "attic", "entity_id": "climate.attic_valve", "temp": 17.5, "attempt": 1, "reason": "Boost or hold ended, back to the previous setting", "cause": "restore", "source": "heating_page"},
    ]
    requests = RequestLog()
    requests.add(Request(when=NOW, action="boost", detail="Boost to 21° for 60 min", room="attic", source="heating_page", user="Pip"))
    requests.add(Request(when=NOW + timedelta(hours=1), action="cancel_boost", detail="Back to the schedule", room="attic"))

    doc = logs_to_dict(would, writes, requests)
    assert set(doc) == {"would_write", "writes", "requests"}
    assert len(doc["would_write"]) == 200 and doc["would_write"][0]["to"] == 19.0
    assert doc["writes"] == writes
    assert doc["requests"][0]["user"] == "Pip"

    would2, writes2, requests2 = logs_from_dict(doc)
    assert would2 == doc["would_write"]
    assert writes2 == writes
    assert requests2 == requests
    assert writes2[1]["cause"] == "restore" and writes2[1]["source"] == "heating_page"


def test_logs_missing_from_an_older_state_document_are_empty():
    would, writes, requests = logs_from_dict({"boosts": [], "changes": []})
    assert would == [] and writes == [] and requests.entries == []
    would, writes, requests = logs_from_dict(None)
    assert would == [] and writes == [] and requests.entries == []


def test_unreadable_log_entries_are_skipped_not_fatal():
    doc = {
        "would_write": [{"when": NOW.isoformat(), "room": "attic"}, "junk", {"room": "no when"}, {"when": "never"}],
        "writes": "not a list",
        "requests": [{"when": NOW.isoformat(), "action": "hold", "detail": "Skip until 19:00", "room": "attic"}, 7],
    }
    would, writes, requests = logs_from_dict(doc)
    assert would == [{"when": NOW.isoformat(), "room": "attic"}]
    assert writes == []
    assert [r.detail for r in requests.entries] == ["Skip until 19:00"]


def test_source_on_boosts_and_holds_round_trips_and_defaults_to_none():
    state = EngineState(
        boosts={"attic": Boost("attic", 21.0, until=L(2026, 1, 7, 20), start=L(2026, 1, 7, 19), source="heating_page")},
        holds={"kitchen": Hold("kitchen", "eco", until=None, set_at=L(2026, 1, 6, 18), source="heating_page")},
    )
    raw = state_to_dict(state)
    assert raw["boosts"][0]["source"] == "heating_page"
    assert raw["holds"][0]["source"] == "heating_page"
    assert state_from_dict(raw) == state
    # an older document without the key
    for entry in raw["boosts"] + raw["holds"]:
        del entry["source"]
    older = state_from_dict(raw)
    assert older.boosts["attic"].source is None
    assert older.holds["kitchen"].source is None


def test_hold_public_shape_carries_its_source():
    kitchen = room(make_config(), "kitchen")
    hold = Hold("kitchen", "eco", until=None, set_at=L(2026, 1, 6, 18), source="heating_page")
    assert hold.to_dict(kitchen)["source"] == "heating_page"
    assert Hold("kitchen", "eco", until=None, set_at=L(2026, 1, 6, 18)).to_dict(kitchen)["source"] is None


# ----------------------------------------------------------------- features

def test_features_list_is_what_the_page_keys_off():
    assert "holds" in FEATURES
    assert "activity" in FEATURES
    assert API_VERSION == 1  # all of this is additive
