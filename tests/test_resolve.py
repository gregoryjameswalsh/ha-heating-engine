from datetime import date, timedelta

import pytest

from heating_core import (
    PLAN_GRACE,
    Boost,
    CalendarEvent,
    EngineState,
    ExceptionNight,
    Hold,
    Holiday,
    ManualOverride,
    matches_plan,
    override_end,
    resolve_now,
    resolve_plan,
)

from household import L, TZ, U, make_config, room

MON = date(2026, 1, 5)  # Week A Monday
TUE = date(2026, 1, 6)


def segs_for(segments, room_id):
    return [s for s in segments if s.room == room_id]


def brief(segments):
    """(start HH:MM, end HH:MM, temp, code) per segment, local time, for readable asserts."""
    return [(s.start.strftime("%a %H:%M"), s.end.strftime("%a %H:%M"), s.temp, s.reason_code) for s in segments]


def assert_contiguous(segments, start, end):
    assert segments, "no segments"
    assert segments[0].start == start
    assert segments[-1].end == end
    for a, b in zip(segments, segments[1:]):
        assert a.end == b.start, f"gap or overlap between {a} and {b}"
        assert a.end > a.start


# ---- rules 4, 5, 7, 9 on an ordinary day ----


def test_monday_week_a_blue_room():
    cfg = make_config()
    segs = segs_for(resolve_plan(cfg, L(2026, 1, 5), L(2026, 1, 6)), "blue_room")
    assert_contiguous(segs, L(2026, 1, 5), L(2026, 1, 6))
    # nobody slept there Sunday night (Week B Sunday), so empty until the 15:00 arrival
    assert brief(segs) == [
        ("Mon 00:00", "Mon 15:00", 14.0, "away"),
        ("Mon 15:00", "Mon 17:00", 16.0, "eco"),
        ("Mon 17:00", "Mon 21:00", 19.0, "warm"),
        ("Mon 21:00", "Tue 00:00", 16.0, "eco"),
    ]
    assert segs[0].reason_text == "Empty until 15:00. Pip and Quinn are away"
    assert segs[1].reason_text == "Eco until 17:00"
    assert segs[2].reason_text == "Warm until 21:00"
    assert segs[3].reason_text == "Eco until 06:30 tomorrow"
    assert segs[3].until == L(2026, 1, 6, 6, 30)
    assert segs[3].end == L(2026, 1, 6)  # clipped to the window


def test_tuesday_week_a_blue_room_full_schedule():
    cfg = make_config()
    segs = segs_for(resolve_plan(cfg, L(2026, 1, 6), L(2026, 1, 7)), "blue_room")
    assert brief(segs) == [
        ("Tue 00:00", "Tue 06:30", 16.0, "eco"),
        ("Tue 06:30", "Tue 08:00", 19.0, "warm"),
        ("Tue 08:00", "Tue 17:00", 16.0, "eco"),
        ("Tue 17:00", "Tue 21:00", 19.0, "warm"),
        ("Tue 21:00", "Wed 00:00", 16.0, "eco"),
    ]
    assert segs[1].reason_text == "Warm until 08:00"


def test_away_text_names_a_pair_and_uses_tomorrow():
    cfg = make_config()
    # Green Room, Week A Monday: Rowan & Sky arrive Tuesday 15:00
    segs = segs_for(resolve_plan(cfg, L(2026, 1, 5), L(2026, 1, 6)), "green_room")
    assert brief(segs) == [("Mon 00:00", "Tue 00:00", 14.0, "away")]
    assert segs[0].reason_text == "Empty until tomorrow. Rowan & Sky are away"
    assert segs[0].until == L(2026, 1, 6, 15)


def test_away_text_for_a_whole_empty_week_uses_a_date():
    cfg = make_config()
    # Green Room, Week B: empty from Mon 09:00 until Week A Tuesday 15:00
    segs = segs_for(resolve_plan(cfg, L(2026, 1, 12), L(2026, 1, 13)), "green_room")
    assert brief(segs) == [
        ("Mon 00:00", "Mon 06:30", 16.0, "eco"),
        ("Mon 06:30", "Mon 08:00", 19.0, "warm"),
        ("Mon 08:00", "Mon 09:00", 16.0, "eco"),
        ("Mon 09:00", "Tue 00:00", 14.0, "away"),
    ]
    assert segs[-1].reason_text == "Empty until Tue 20 Jan. Rowan & Sky are away"


def test_single_occupant_grammar():
    cfg = make_config()
    cfg.exceptions.append(ExceptionNight("teddy", date(2026, 1, 7), False))  # Teddy out Wednesday night
    segs = segs_for(resolve_plan(cfg, L(2026, 1, 7), L(2026, 1, 8)), "attic")
    away = [s for s in segs if s.reason_code == "away"]
    assert len(away) == 1
    assert away[0].start == L(2026, 1, 7, 9)
    assert away[0].reason_text == "Empty until tomorrow. Teddy is away"


def test_room_with_no_occupants_never_goes_empty():
    cfg = make_config()
    segs = resolve_plan(cfg, L(2026, 1, 12), L(2026, 1, 19))
    assert all(s.reason_code != "away" for s in segs_for(segs, "kitchen"))
    assert all(s.reason_code != "away" for s in segs_for(segs, "big_bedroom"))


def test_schedule_block_inside_an_empty_stretch_does_not_show():
    cfg = make_config()
    # Blue Room Monday morning block 06:30-08:00 falls while the room is empty
    segs = segs_for(resolve_plan(cfg, L(2026, 1, 5, 6), L(2026, 1, 5, 9)), "blue_room")
    assert brief(segs) == [("Mon 06:00", "Mon 09:00", 14.0, "away")]


# ---- rule 4: holiday ----


def test_holiday_beats_schedule_and_ends_preheat_early():
    cfg = make_config(holidays=[Holiday(date(2026, 1, 6), date(2026, 1, 9), preheat_hours=6)])
    segs = segs_for(resolve_plan(cfg, L(2026, 1, 6), L(2026, 1, 7)), "kitchen")
    assert brief(segs) == [("Tue 00:00", "Wed 00:00", 12.0, "holiday")]
    assert segs[0].reason_text == "Holiday until Thursday"
    assert segs[0].until == L(2026, 1, 8, 18)  # Fri 00:00 minus 6 h
    # and the pre-heat really does hand back to the schedule
    thu = segs_for(resolve_plan(cfg, L(2026, 1, 8, 17), L(2026, 1, 8, 19)), "kitchen")
    assert brief(thu) == [("Thu 17:00", "Thu 18:00", 12.0, "holiday"), ("Thu 18:00", "Thu 19:00", 19.0, "warm")]


def test_holiday_outranks_away():
    cfg = make_config(holidays=[Holiday(date(2026, 1, 5), date(2026, 1, 7), preheat_hours=0)])
    segs = segs_for(resolve_plan(cfg, L(2026, 1, 5), L(2026, 1, 6)), "green_room")
    assert [s.reason_code for s in segs] == ["holiday"]


# ---- rule 1: boost ----


def test_boost_wins_over_everything_and_has_a_known_end():
    cfg = make_config()
    state = EngineState(boosts={"blue_room": Boost("blue_room", 21.0, until=L(2026, 1, 6, 19), start=L(2026, 1, 6, 18))})
    segs = segs_for(resolve_plan(cfg, L(2026, 1, 6, 17), L(2026, 1, 6, 22), state=state), "blue_room")
    assert brief(segs) == [
        ("Tue 17:00", "Tue 18:00", 19.0, "warm"),
        ("Tue 18:00", "Tue 19:00", 21.0, "boost"),
        ("Tue 19:00", "Tue 21:00", 19.0, "warm"),
        ("Tue 21:00", "Tue 22:00", 16.0, "eco"),
    ]
    assert segs[1].reason_text == "Boosted to 21° until 19:00"
    assert segs[1].rule == 1


def test_boost_with_no_start_covers_from_window_start():
    cfg = make_config()
    state = EngineState(boosts={"attic": Boost("attic", 20.5, until=L(2026, 1, 6, 1))})
    segs = segs_for(resolve_plan(cfg, L(2026, 1, 6), L(2026, 1, 6, 2), state=state), "attic")
    assert brief(segs) == [("Tue 00:00", "Tue 01:00", 20.5, "boost"), ("Tue 01:00", "Tue 02:00", 16.0, "eco")]
    assert segs[0].reason_text == "Boosted to 20.5° until 01:00"


def test_expired_boost_has_no_effect():
    cfg = make_config()
    state = EngineState(boosts={"attic": Boost("attic", 22.0, until=L(2026, 1, 5, 23))})
    segs = segs_for(resolve_plan(cfg, L(2026, 1, 6), L(2026, 1, 6, 2), state=state), "attic")
    assert all(s.reason_code != "boost" for s in segs)


# ---- rule 3: manual change ----


def test_manual_change_holds_until_the_next_schedule_change():
    cfg = make_config()
    state = EngineState(overrides={"blue_room": ManualOverride("blue_room", 17.5, since=L(2026, 1, 6, 18, 30))})
    segs = segs_for(resolve_plan(cfg, L(2026, 1, 6, 17), L(2026, 1, 6, 22), state=state), "blue_room")
    assert brief(segs) == [
        ("Tue 17:00", "Tue 18:30", 19.0, "warm"),
        ("Tue 18:30", "Tue 21:00", 17.5, "manual"),
        ("Tue 21:00", "Tue 22:00", 16.0, "eco"),
    ]
    assert segs[1].reason_text == "Turned down at the radiator. Back to schedule at 21:00"
    assert segs[1].rule == 3


def test_manual_change_during_eco_runs_to_the_next_block_tomorrow():
    cfg = make_config()
    state = EngineState(overrides={"blue_room": ManualOverride("blue_room", 18.0, since=L(2026, 1, 6, 22))})
    segs = segs_for(resolve_plan(cfg, L(2026, 1, 6, 21), L(2026, 1, 7, 7), state=state), "blue_room")
    assert brief(segs) == [
        ("Tue 21:00", "Tue 22:00", 16.0, "eco"),
        ("Tue 22:00", "Wed 06:30", 18.0, "manual"),
        ("Wed 06:30", "Wed 07:00", 19.0, "warm"),
    ]
    assert segs[1].reason_text == "Turned up at the radiator. Back to schedule at 06:30 tomorrow"


def test_manual_change_made_before_the_window_still_applies():
    cfg = make_config()
    state = EngineState(overrides={"blue_room": ManualOverride("blue_room", 18.0, since=L(2026, 1, 6, 22))})
    segs = segs_for(resolve_plan(cfg, L(2026, 1, 7, 2), L(2026, 1, 7, 7), state=state), "blue_room")
    assert brief(segs)[0] == ("Wed 02:00", "Wed 06:30", 18.0, "manual")


def test_expired_manual_change_is_ignored():
    cfg = make_config()
    state = EngineState(overrides={"blue_room": ManualOverride("blue_room", 18.0, since=L(2026, 1, 6, 18))})  # schedule changed at 21:00
    segs = segs_for(resolve_plan(cfg, L(2026, 1, 6, 21), L(2026, 1, 6, 23), state=state), "blue_room")
    assert all(s.reason_code != "manual" for s in segs)


def test_boost_beats_manual_change():
    cfg = make_config()
    state = EngineState(
        boosts={"blue_room": Boost("blue_room", 21.0, until=L(2026, 1, 6, 19), start=L(2026, 1, 6, 18))},
        overrides={"blue_room": ManualOverride("blue_room", 17.5, since=L(2026, 1, 6, 18, 30))},
    )
    segs = segs_for(resolve_plan(cfg, L(2026, 1, 6, 17, 30), L(2026, 1, 6, 21, 30), state=state), "blue_room")
    assert brief(segs) == [
        ("Tue 17:30", "Tue 18:00", 19.0, "warm"),
        ("Tue 18:00", "Tue 19:00", 21.0, "boost"),
        ("Tue 19:00", "Tue 21:00", 17.5, "manual"),
        ("Tue 21:00", "Tue 21:30", 16.0, "eco"),
    ]


def test_override_end_helper_matches_resolver():
    cfg = make_config()
    ov = ManualOverride("blue_room", 17.5, since=L(2026, 1, 6, 18, 30))
    end, base_temp = override_end(cfg, room(cfg, "blue_room"), ov, {}, TZ)
    assert end == U(2026, 1, 6, 21)
    assert base_temp == 19.0


def test_manual_change_equal_to_schedule_says_changed():
    cfg = make_config()
    state = EngineState(overrides={"blue_room": ManualOverride("blue_room", 19.0, since=L(2026, 1, 6, 18))})
    segs = segs_for(resolve_plan(cfg, L(2026, 1, 6, 18), L(2026, 1, 6, 19), state=state), "blue_room")
    assert segs[0].reason_text == "Changed at the radiator. Back to schedule at 21:00"


# ---- a setpoint that lands on the schedule is the schedule's, not a manual change ----
# Blue Room, Tue 6 Jan: warm 19 from 17:00 to 21:00, eco 16 after, warm again 06:30.


def test_matches_plan_now_and_within_the_grace_but_not_before():
    cfg = make_config()
    blue = room(cfg, "blue_room")
    assert matches_plan(cfg, blue, 19.0, "heat", L(2026, 1, 6, 19)) is True
    assert matches_plan(cfg, blue, 16.0, "heat", L(2026, 1, 6, 19)) is False
    # the device's own schedule steps five seconds before the engine's block edge
    assert matches_plan(cfg, blue, 16.0, "heat", L(2026, 1, 6, 20, 59) + timedelta(seconds=55)) is True
    # or two seconds after it
    assert matches_plan(cfg, blue, 16.0, "heat", L(2026, 1, 6, 21) + timedelta(seconds=2)) is True
    # two minutes early is somebody's hand
    assert matches_plan(cfg, blue, 16.0, "heat", L(2026, 1, 6, 20, 58)) is False
    assert PLAN_GRACE == timedelta(seconds=90)
    assert matches_plan(cfg, blue, 16.0, "heat", L(2026, 1, 6, 20, 58), grace=timedelta(minutes=5)) is True


def test_matches_plan_needs_heat_mode_and_a_setpoint():
    cfg = make_config()
    blue = room(cfg, "blue_room")
    assert matches_plan(cfg, blue, 19.0, "off", L(2026, 1, 6, 19)) is False
    assert matches_plan(cfg, blue, 19.0, "auto", L(2026, 1, 6, 19)) is False
    assert matches_plan(cfg, blue, 19.0, None, L(2026, 1, 6, 19)) is False
    assert matches_plan(cfg, blue, None, "heat", L(2026, 1, 6, 19)) is False


def test_matches_plan_uses_the_apply_tolerance():
    cfg = make_config()
    blue = room(cfg, "blue_room")
    assert matches_plan(cfg, blue, 19.04, "heat", L(2026, 1, 6, 19)) is True
    assert matches_plan(cfg, blue, 19.5, "heat", L(2026, 1, 6, 19)) is False


def test_matches_plan_sees_through_the_rooms_own_override_only_when_asked():
    cfg = make_config()
    blue = room(cfg, "blue_room")
    state = EngineState(overrides={"blue_room": ManualOverride("blue_room", 17.5, since=L(2026, 1, 6, 18, 30))})
    at = L(2026, 1, 6, 19)
    # the radiator is put back to the schedule's 19: that is the schedule, and the override should end
    assert matches_plan(cfg, blue, 19.0, "heat", at, state=state, ignore_override=True) is True
    # as the plan stands, the room is at 17.5 under the override
    assert matches_plan(cfg, blue, 19.0, "heat", at, state=state) is False
    assert matches_plan(cfg, blue, 17.5, "heat", at, state=state) is True
    # another room's override is not in the way either way
    other = EngineState(overrides={"kitchen": ManualOverride("kitchen", 17.5, since=L(2026, 1, 6, 18, 30))})
    assert matches_plan(cfg, blue, 19.0, "heat", at, state=other) is True
    assert matches_plan(cfg, blue, 19.0, "heat", at, state=other, ignore_override=True) is True
    assert state.overrides == {"blue_room": ManualOverride("blue_room", 17.5, since=L(2026, 1, 6, 18, 30))}  # not mutated


def test_matches_plan_counts_a_hold_and_a_boost_as_the_plan():
    cfg = make_config()
    blue = room(cfg, "blue_room")
    held = EngineState(holds={"blue_room": Hold("blue_room", "eco", None, L(2026, 1, 6, 18))})
    # a hand setting the held room to Eco is the hold's own value, not a manual change
    assert matches_plan(cfg, blue, 16.0, "heat", L(2026, 1, 6, 19), state=held, ignore_override=True) is True
    assert matches_plan(cfg, blue, 19.0, "heat", L(2026, 1, 6, 19), state=held, ignore_override=True) is False
    boosted = EngineState(boosts={"blue_room": Boost("blue_room", 21.0, until=L(2026, 1, 6, 19, 30), start=L(2026, 1, 6, 18, 30))})
    assert matches_plan(cfg, blue, 21.0, "heat", L(2026, 1, 6, 19), state=boosted) is True
    # ten seconds before the boost ends, the schedule's 19 is already the plan
    assert matches_plan(cfg, blue, 19.0, "heat", L(2026, 1, 6, 19, 29) + timedelta(seconds=50), state=boosted) is True
    assert matches_plan(cfg, blue, 19.0, "heat", L(2026, 1, 6, 19), state=boosted) is False


def test_matches_plan_at_a_rota_edge():
    cfg = make_config()
    blue = room(cfg, "blue_room")
    # Mon 5 Jan: empty (14) until the 15:00 arrival, then eco 16
    assert matches_plan(cfg, blue, 16.0, "heat", L(2026, 1, 5, 14, 59) + timedelta(seconds=30)) is True
    assert matches_plan(cfg, blue, 16.0, "heat", L(2026, 1, 5, 14, 30)) is False
    assert matches_plan(cfg, blue, 14.0, "heat", L(2026, 1, 5, 14, 30)) is True


def test_matches_plan_wants_an_aware_time():
    cfg = make_config()
    with pytest.raises(ValueError):
        matches_plan(cfg, room(cfg, "blue_room"), 19.0, "heat", L(2026, 1, 6, 19).replace(tzinfo=None))


# ---- calendar-driven rooms ----


def test_calendar_away_room_warms_for_the_preheat():
    cfg = make_config()
    events = {"calendar.studio": [CalendarEvent(L(2026, 1, 3), L(2026, 1, 10))]}
    segs = segs_for(resolve_plan(cfg, L(2026, 1, 9), L(2026, 1, 10), calendar_events=events), "studio_lounge")
    assert brief(segs) == [
        ("Fri 00:00", "Fri 12:00", 12.0, "away"),
        ("Fri 12:00", "Fri 17:00", 15.0, "eco"),
        ("Fri 17:00", "Fri 21:00", 19.0, "warm"),
        ("Fri 21:00", "Sat 00:00", 15.0, "eco"),
    ]
    assert segs[0].reason_text == "Empty until 12:00. Gran & Gramps are away"


# ---- whole-plan shape ----


def test_plan_covers_every_room_contiguously_for_fourteen_days():
    cfg = make_config(holidays=[Holiday(date(2026, 1, 14), date(2026, 1, 16))])
    start, end = L(2026, 1, 5), L(2026, 1, 19)
    events = {"calendar.studio": [CalendarEvent(L(2026, 1, 3), L(2026, 1, 10))]}
    state = EngineState(
        boosts={"attic": Boost("attic", 21.0, until=L(2026, 1, 7, 20), start=L(2026, 1, 7, 19))},
        overrides={"blue_room": ManualOverride("blue_room", 17.5, since=L(2026, 1, 6, 18, 30))},
    )
    segs = resolve_plan(cfg, start, end, events, state)
    assert [r.id for r in cfg.rooms] == list(dict.fromkeys(s.room for s in segs))  # config order
    for r in cfg.rooms:
        assert_contiguous(segs_for(segs, r.id), start, end)
        for s in segs_for(segs, r.id):
            assert s.reason_text  # never empty
            assert s.rule in (1, 2, 3, 4, 5, 7, 9)


def test_resolve_now_returns_one_segment_per_room_containing_now():
    cfg = make_config()
    now = L(2026, 1, 6, 18, 10)
    current = resolve_now(cfg, now)
    assert set(current) == {r.id for r in cfg.rooms}
    blue = current["blue_room"]
    assert blue.start == now
    assert (blue.temp, blue.reason_code, blue.reason_text) == (19.0, "warm", "Warm until 21:00")
    assert blue.until == L(2026, 1, 6, 21)


def test_naive_datetimes_are_refused():
    cfg = make_config()
    from datetime import datetime

    import pytest

    with pytest.raises(ValueError):
        resolve_plan(cfg, datetime(2026, 1, 5), datetime(2026, 1, 6))
    with pytest.raises(ValueError):
        resolve_plan(cfg, L(2026, 1, 6), L(2026, 1, 5))
