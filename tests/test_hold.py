"""Dashboard holds (rule 2): resolution order, reason text, expiry, the next
scheduled change, persistence and the public shape."""
from datetime import date, timedelta

import pytest

from heating_core import (
    DEFAULT_FROST_TEMP,
    Boost,
    CalendarEvent,
    EngineState,
    Hold,
    Holiday,
    ManualOverride,
    Temps,
    next_schedule_change,
    resolve_now,
    resolve_plan,
    state_from_dict,
    state_to_dict,
    validate_config,
)

from household import L, TZ, U, make_config, room


def segs_for(segments, room_id):
    return [s for s in segments if s.room == room_id]


def brief(segments):
    return [(s.start.strftime("%a %H:%M"), s.end.strftime("%a %H:%M"), s.temp, s.reason_code) for s in segments]


# Tuesday 6 Jan 2026 (Week A): Blue Room warm 06:30-08:00 and 17:00-21:00, eco otherwise, Pip there.


# ---- resolution order ----


def test_hold_beats_the_schedule_and_resumes_it_at_until():
    cfg = make_config()
    state = EngineState(holds={"blue_room": Hold("blue_room", "skip", until=L(2026, 1, 6, 21), set_at=L(2026, 1, 6, 18))})
    segs = segs_for(resolve_plan(cfg, L(2026, 1, 6, 17), L(2026, 1, 6, 22), state=state), "blue_room")
    assert brief(segs) == [
        ("Tue 17:00", "Tue 18:00", 19.0, "warm"),
        ("Tue 18:00", "Tue 21:00", 16.0, "hold"),
        ("Tue 21:00", "Tue 22:00", 16.0, "eco"),
    ]
    assert segs[1].rule == 2
    assert segs[1].reason_text == "Skipping until 21:00"
    assert segs[1].until == L(2026, 1, 6, 21)


def test_boost_beats_a_hold_while_it_runs_then_the_hold_resumes():
    cfg = make_config()
    state = EngineState(
        boosts={"blue_room": Boost("blue_room", 21.0, until=L(2026, 1, 6, 19), start=L(2026, 1, 6, 18, 30))},
        holds={"blue_room": Hold("blue_room", "skip", until=L(2026, 1, 6, 21), set_at=L(2026, 1, 6, 18))},
    )
    segs = segs_for(resolve_plan(cfg, L(2026, 1, 6, 17, 30), L(2026, 1, 6, 21, 30), state=state), "blue_room")
    assert brief(segs) == [
        ("Tue 17:30", "Tue 18:00", 19.0, "warm"),
        ("Tue 18:00", "Tue 18:30", 16.0, "hold"),
        ("Tue 18:30", "Tue 19:00", 21.0, "boost"),
        ("Tue 19:00", "Tue 21:00", 16.0, "hold"),
        ("Tue 21:00", "Tue 21:30", 16.0, "eco"),
    ]
    assert [s.rule for s in segs] == [7, 2, 1, 2, 9]


def test_hold_beats_a_change_made_at_the_radiator():
    cfg = make_config()
    state = EngineState(
        holds={"blue_room": Hold("blue_room", "temp", until=L(2026, 1, 6, 21), set_at=L(2026, 1, 6, 18), temp=18.0)},
        overrides={"blue_room": ManualOverride("blue_room", 17.5, since=L(2026, 1, 6, 18, 30))},
    )
    segs = segs_for(resolve_plan(cfg, L(2026, 1, 6, 17, 30), L(2026, 1, 6, 21, 30), state=state), "blue_room")
    assert brief(segs) == [
        ("Tue 17:30", "Tue 18:00", 19.0, "warm"),
        ("Tue 18:00", "Tue 21:00", 18.0, "hold"),
        ("Tue 21:00", "Tue 21:30", 16.0, "eco"),
    ]
    assert segs[1].rule == 2


def test_hold_beats_a_holiday_and_an_empty_room():
    cfg = make_config(holidays=[Holiday(date(2026, 1, 5), date(2026, 1, 8), preheat_hours=0)])
    state = EngineState(holds={"green_room": Hold("green_room", "temp", until=None, set_at=L(2026, 1, 5, 10), temp=17.0)})
    segs = segs_for(resolve_plan(cfg, L(2026, 1, 5, 9), L(2026, 1, 5, 12), state=state), "green_room")
    assert brief(segs) == [("Mon 09:00", "Mon 10:00", 14.0, "holiday"), ("Mon 10:00", "Mon 12:00", 17.0, "hold")]


# ---- what each kind holds at, and what it says ----


def test_skip_and_eco_hold_at_the_rooms_eco_temperature():
    cfg = make_config()
    state = EngineState(holds={"blue_room": Hold("blue_room", "eco", until=None, set_at=L(2026, 1, 6, 18))})
    now = resolve_now(cfg, L(2026, 1, 6, 18, 30), state=state)["blue_room"]
    assert (now.temp, now.reason_code, now.rule) == (16.0, "hold", 2)
    assert now.reason_text == "Holding at Eco until you turn it back on"
    assert now.until is None


def test_frost_holds_at_the_rooms_frost_temperature_which_defaults_to_five():
    cfg = make_config()
    assert room(cfg, "blue_room").temps.frost == DEFAULT_FROST_TEMP == 5.0
    state = EngineState(holds={"blue_room": Hold("blue_room", "frost", until=None, set_at=L(2026, 1, 6, 18))})
    now = resolve_now(cfg, L(2026, 1, 6, 18, 30), state=state)["blue_room"]
    assert (now.temp, now.reason_text) == (5.0, "Frost protection until you turn it back on")


def test_a_configured_frost_temperature_is_used():
    cfg = make_config()
    r = room(cfg, "attic")
    r.temps = Temps(comfort=19.0, eco=16.0, empty=14.0, frost=7.0)
    state = EngineState(holds={"attic": Hold("attic", "frost", until=L(2026, 1, 6, 20), set_at=L(2026, 1, 6, 18))})
    now = resolve_now(cfg, L(2026, 1, 6, 18, 30), state=state)["attic"]
    assert (now.temp, now.reason_text) == (7.0, "Frost protection until 20:00")


def test_temp_hold_says_the_temperature():
    cfg = make_config()
    state = EngineState(holds={"blue_room": Hold("blue_room", "temp", until=L(2026, 1, 6, 21), set_at=L(2026, 1, 6, 18), temp=18.5)})
    now = resolve_now(cfg, L(2026, 1, 6, 18, 30), state=state)["blue_room"]
    assert (now.temp, now.reason_text) == (18.5, "Set to 18.5° until 21:00")
    state = EngineState(holds={"blue_room": Hold("blue_room", "temp", until=None, set_at=L(2026, 1, 6, 18), temp=18.0)})
    now = resolve_now(cfg, L(2026, 1, 6, 18, 30), state=state)["blue_room"]
    assert now.reason_text == "Set to 18° until you turn it back on"


def test_skip_with_no_end_says_so():
    cfg = make_config()
    state = EngineState(holds={"blue_room": Hold("blue_room", "skip", until=None, set_at=L(2026, 1, 6, 18))})
    now = resolve_now(cfg, L(2026, 1, 6, 18, 30), state=state)["blue_room"]
    assert now.reason_text == "Skipping until you turn it back on"


def test_until_tomorrow_and_a_far_until_are_worded_like_other_rules():
    cfg = make_config()
    state = EngineState(holds={"blue_room": Hold("blue_room", "skip", until=L(2026, 1, 7, 6, 30), set_at=L(2026, 1, 6, 22))})
    now = resolve_now(cfg, L(2026, 1, 6, 22, 30), state=state)["blue_room"]
    assert now.reason_text == "Skipping until 06:30 tomorrow"
    # an end past the resolver's look-ahead is still a known end
    state = EngineState(holds={"blue_room": Hold("blue_room", "eco", until=L(2026, 2, 20, 9), set_at=L(2026, 1, 6, 22))})
    now = resolve_now(cfg, L(2026, 1, 6, 22, 30), state=state)["blue_room"]
    assert now.reason_text == "Holding at Eco until Fri 20 Feb"
    assert now.until == L(2026, 2, 20, 9)


# ---- expiry ----


def test_hold_has_no_effect_after_until_and_before_set_at():
    cfg = make_config()
    state = EngineState(holds={"blue_room": Hold("blue_room", "skip", until=L(2026, 1, 6, 21), set_at=L(2026, 1, 6, 18))})
    before = resolve_now(cfg, L(2026, 1, 6, 17, 59), state=state)["blue_room"]
    at_end = resolve_now(cfg, L(2026, 1, 6, 21), state=state)["blue_room"]
    after = resolve_now(cfg, L(2026, 1, 6, 23), state=state)["blue_room"]
    assert before.reason_code == "warm"
    assert at_end.reason_code == "eco"
    assert after.reason_code == "eco"
    at_start = resolve_now(cfg, L(2026, 1, 6, 18), state=state)["blue_room"]
    assert at_start.reason_code == "hold"


def test_hold_set_before_the_window_still_applies():
    cfg = make_config()
    state = EngineState(holds={"blue_room": Hold("blue_room", "skip", until=L(2026, 1, 7, 6, 30), set_at=L(2026, 1, 6, 22))})
    segs = segs_for(resolve_plan(cfg, L(2026, 1, 7, 2), L(2026, 1, 7, 7), state=state), "blue_room")
    assert brief(segs) == [("Wed 02:00", "Wed 06:30", 16.0, "hold"), ("Wed 06:30", "Wed 07:00", 19.0, "warm")]


def test_an_indefinite_hold_runs_through_the_whole_plan():
    cfg = make_config()
    state = EngineState(holds={"blue_room": Hold("blue_room", "eco", until=None, set_at=L(2026, 1, 5))})
    segs = segs_for(resolve_plan(cfg, L(2026, 1, 5), L(2026, 1, 19), state=state), "blue_room")
    assert brief(segs) == [("Mon 00:00", "Mon 00:00", 16.0, "hold")]
    assert segs[0].until is None


# ---- the next scheduled change ----


def test_next_change_inside_a_warm_block_is_the_block_end():
    cfg = make_config()
    assert next_schedule_change(cfg, room(cfg, "blue_room"), L(2026, 1, 6, 18), {}, TZ) == U(2026, 1, 6, 21)


def test_next_change_in_the_evening_is_tomorrows_first_block_across_midnight():
    cfg = make_config()
    assert next_schedule_change(cfg, room(cfg, "blue_room"), L(2026, 1, 6, 22), {}, TZ) == U(2026, 1, 7, 6, 30)


def test_next_change_at_a_boundary_is_the_following_boundary():
    cfg = make_config()
    assert next_schedule_change(cfg, room(cfg, "blue_room"), L(2026, 1, 6, 21), {}, TZ) == U(2026, 1, 7, 6, 30)


def test_next_change_counts_someone_arriving_or_leaving():
    cfg = make_config()
    # Blue Room is empty on Week A Monday until Pip arrives at 15:00
    assert next_schedule_change(cfg, room(cfg, "blue_room"), L(2026, 1, 5, 10), {}, TZ) == U(2026, 1, 5, 15)
    # Green Room, Week B Monday: Rowan & Sky leave at 09:00
    assert next_schedule_change(cfg, room(cfg, "green_room"), L(2026, 1, 12, 8, 30), {}, TZ) == U(2026, 1, 12, 9)


def test_next_change_counts_a_holiday_edge_and_a_calendar_return():
    cfg = make_config(holidays=[Holiday(date(2026, 1, 6), date(2026, 1, 9), preheat_hours=6)])
    assert next_schedule_change(cfg, room(cfg, "kitchen"), L(2026, 1, 7, 12), {}, TZ) == U(2026, 1, 8, 18)
    cfg = make_config()
    events = {"calendar.studio": [CalendarEvent(L(2026, 1, 3), L(2026, 1, 10))]}
    assert next_schedule_change(cfg, room(cfg, "studio_lounge"), L(2026, 1, 9, 6), events, TZ) == U(2026, 1, 9, 12)


def test_next_change_is_none_when_nothing_changes_inside_the_look_ahead():
    cfg = make_config()
    r = room(cfg, "big_bedroom")  # always in use, one block on Mondays
    r.schedule = {}
    assert next_schedule_change(cfg, r, L(2026, 1, 6, 10), {}, TZ) is None


def test_next_change_across_the_autumn_clock_change():
    cfg = make_config()
    # Sunday 25 Oct 2026 is 25 hours long; the Attic's weekend block still ends at 21:00 on the wall
    end = next_schedule_change(cfg, room(cfg, "attic"), L(2026, 10, 25, 0, 30), {}, TZ)
    assert end is not None
    assert end.astimezone(TZ).strftime("%a %H:%M") == "Sun 08:00"
    assert end - U(2026, 10, 24, 23, 30) == timedelta(hours=8, minutes=30)


# ---- persistence ----


def test_runtime_state_round_trips_through_dict():
    state = EngineState(
        boosts={"attic": Boost("attic", 21.0, until=L(2026, 1, 7, 20), start=L(2026, 1, 7, 19), restore_temp=17.5)},
        holds={
            "blue_room": Hold("blue_room", "skip", until=L(2026, 1, 6, 21), set_at=L(2026, 1, 6, 18), restore_temp=16.0),
            "kitchen": Hold("kitchen", "temp", until=None, set_at=L(2026, 1, 6, 18), temp=18.5),
            "attic": Hold("attic", "frost", until=None, set_at=U(2026, 1, 6, 18)),
        },
        overrides={"green_room": ManualOverride("green_room", 17.5, since=L(2026, 1, 6, 18, 30))},
    )
    raw = state_to_dict(state)
    again = state_from_dict(raw)
    assert again == state
    assert state_to_dict(again) == raw
    assert raw["holds"][0] == {"room": "blue_room", "kind": "skip", "temp": None, "until": "2026-01-06T21:00:00+00:00", "set_at": "2026-01-06T18:00:00+00:00", "restore_temp": 16.0, "source": None}
    assert again.holds["kitchen"].until is None
    assert again.holds["attic"].set_at.tzinfo is not None


def test_unreadable_or_unknown_entries_are_skipped_not_fatal():
    raw = {
        "holds": [
            {"room": "a", "kind": "skip", "until": None, "set_at": "2026-01-06T18:00:00+00:00"},
            {"room": "b", "kind": "nap", "until": None, "set_at": "2026-01-06T18:00:00+00:00"},
            {"room": "c", "kind": "temp", "until": "not a time", "set_at": "2026-01-06T18:00:00+00:00"},
            {"kind": "eco"},
        ],
        "boosts": [{"room": "x"}],
        "overrides": "garbage",
    }
    state = state_from_dict({"holds": raw["holds"], "boosts": raw["boosts"]})
    assert set(state.holds) == {"a"}
    assert state.boosts == {}
    assert state_from_dict(None) == EngineState()
    assert state_from_dict({}) == EngineState()


# ---- the public shape ----


def test_hold_to_dict_is_the_state_api_shape():
    cfg = make_config()
    h = Hold("blue_room", "skip", until=L(2026, 1, 6, 21), set_at=L(2026, 1, 6, 18), restore_temp=16.0)
    d = h.to_dict(room(cfg, "blue_room"))
    assert d == {"room": "blue_room", "kind": "skip", "target": 16.0, "until": "2026-01-06T21:00:00+00:00", "set_at": "2026-01-06T18:00:00+00:00", "source": None}
    assert "restore_temp" not in d and "temp" not in d
    h = Hold("blue_room", "temp", until=None, set_at=L(2026, 1, 6, 18), temp=18.0)
    assert h.to_dict(room(cfg, "blue_room"))["target"] == 18.0
    assert h.to_dict(room(cfg, "blue_room"))["until"] is None
    assert Hold("blue_room", "frost", until=None, set_at=L(2026, 1, 6, 18)).to_dict(room(cfg, "blue_room"))["target"] == 5.0


def test_a_hold_at_eco_follows_the_rooms_eco_if_the_config_changes():
    cfg = make_config()
    h = Hold("blue_room", "eco", until=None, set_at=L(2026, 1, 6, 18))
    assert h.target(room(cfg, "blue_room")) == 16.0
    room(cfg, "blue_room").temps = Temps(comfort=19.0, eco=15.5, empty=14.0)
    assert h.target(room(cfg, "blue_room")) == 15.5


# ---- frost in the config document ----


def test_frost_temperature_round_trips_and_is_validated():
    cfg = make_config()
    assert room(cfg, "blue_room").to_dict()["temps"] == {"comfort": 19.0, "eco": 16.0, "empty": 14.0, "frost": 5.0}
    assert Temps.from_dict({"comfort": 19, "eco": 16, "empty": 14}).frost == 5.0
    assert Temps.from_dict({"comfort": 19, "eco": 16, "empty": 14, "frost": 8}).frost == 8.0
    room(cfg, "attic").temps = Temps(comfort=19.0, eco=16.0, empty=14.0, frost=4.0)
    assert any("frost" in m and "outside" in m for m in validate_config(cfg))
    room(cfg, "attic").temps = Temps(comfort=19.0, eco=16.0, empty=14.0, frost=6.3)
    assert any("frost" in m and "half degree" in m for m in validate_config(cfg))


@pytest.mark.parametrize("kind", ["skip", "eco", "frost", "temp"])
def test_every_kind_resolves_with_a_rule_and_text(kind):
    cfg = make_config()
    state = EngineState(holds={"attic": Hold("attic", kind, until=None, set_at=L(2026, 1, 6, 18), temp=20.0 if kind == "temp" else None)})
    now = resolve_now(cfg, L(2026, 1, 6, 18, 30), state=state)["attic"]
    assert now.rule == 2 and now.reason_code == "hold" and now.reason_text
