from datetime import date, timedelta

import pytest

from heating_core import CalendarEvent, ExceptionNight, person_here_tonight
from heating_core.occupancy import (
    FAR_FUTURE,
    FAR_PAST,
    complement,
    contains,
    merge_intervals,
    person_present_intervals,
    room_occupied_intervals,
)
from heating_core.timeutil import local_at, to_utc

from household import L, TZ, U, WEEK_A_MON, WEEK_B_MON, make_config, person, room

NIGHTS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def occupied_on_night(cfg, room_id: str, night: date) -> bool:
    """Is the room in use at 23:00 local on `night`?"""
    r = room(cfg, room_id)
    probe = to_utc(local_at(night, "23:00", TZ))
    occ = room_occupied_intervals(cfg, r, probe - timedelta(days=3), probe + timedelta(days=3), TZ, {})
    return contains(occ, probe)


def nights_in_use(cfg, room_id: str, monday: date) -> list[str]:
    return [NIGHTS[i] for i in range(7) if occupied_on_night(cfg, room_id, monday + timedelta(days=i))]


# ---- the occupancy table from household.py, both weeks ----


@pytest.mark.parametrize(
    "room_id, week_a, week_b",
    [
        ("blue_room", ["Mon", "Tue", "Wed", "Fri", "Sat", "Sun"], ["Wed", "Thu"]),
        ("green_room", ["Tue", "Wed", "Fri", "Sat", "Sun"], []),
        ("attic", NIGHTS, NIGHTS),
    ],
)
def test_room_occupancy_table(room_id, week_a, week_b):
    cfg = make_config()
    assert nights_in_use(cfg, room_id, WEEK_A_MON) == week_a
    assert nights_in_use(cfg, room_id, WEEK_B_MON) == week_b


def test_green_room_is_empty_nine_nights_in_fourteen():
    cfg = make_config()
    used = len(nights_in_use(cfg, "green_room", WEEK_A_MON)) + len(nights_in_use(cfg, "green_room", WEEK_B_MON))
    assert 14 - used == 9


# ---- nights to hours ----


def test_consecutive_nights_merge_into_one_stay():
    cfg = make_config()
    # Rowan & Sky, Week A: Tue Wed -> one stay Tue 15:00 to Thu 09:00; Fri Sat Sun -> Fri 15:00 to Mon 09:00
    iv = person_present_intervals(cfg, person(cfg, "rowan_sky"), L(2026, 1, 5), L(2026, 1, 12), TZ, {})
    inside = [(s, e) for s, e in iv if s >= U(2026, 1, 5) and s < U(2026, 1, 12)]
    assert inside == [
        (U(2026, 1, 6, 15), U(2026, 1, 8, 9)),
        (U(2026, 1, 9, 15), U(2026, 1, 12, 9)),
    ]


def test_arrival_and_leave_times_are_per_person():
    cfg = make_config()
    quinn = person(cfg, "quinn")
    quinn.arrive = "16:30"
    quinn.leave = "08:15"
    iv = person_present_intervals(cfg, quinn, L(2026, 1, 5), L(2026, 1, 6), TZ, {})
    mon = [(s, e) for s, e in iv if s == U(2026, 1, 5, 16, 30)]
    assert mon and mon[0][1] == U(2026, 1, 6, 8, 15)


def test_daytime_is_occupied_between_two_here_nights_and_empty_before_an_away_night():
    cfg = make_config()
    r = room(cfg, "blue_room")
    occ = room_occupied_intervals(cfg, r, L(2026, 1, 5), L(2026, 1, 10), TZ, {})
    # Week A: Pip is here Mon, Tue and Wed nights, nobody Thursday night
    assert contains(occ, U(2026, 1, 6, 12))  # Tue midday: one continuous stay Mon 15:00 -> Thu 09:00
    assert contains(occ, U(2026, 1, 7, 12))  # Wed midday: same stay
    assert contains(occ, U(2026, 1, 8, 8, 59))  # Thu 08:59: still here
    assert not contains(occ, U(2026, 1, 8, 9))  # Thu 09:00: gone until Friday 15:00
    assert not contains(occ, U(2026, 1, 9, 14, 59))
    assert contains(occ, U(2026, 1, 9, 15))  # Fri 15:00: back for the weekend


def test_room_with_no_occupants_is_always_in_use():
    cfg = make_config()
    occ = room_occupied_intervals(cfg, room(cfg, "kitchen"), L(2026, 1, 5), L(2026, 1, 19), TZ, {})
    assert occ == [(FAR_PAST, FAR_FUTURE)]


def test_either_sharer_makes_the_room_in_use():
    cfg = make_config()
    # Week A Tuesday: Pip yes, Quinn no -> Blue Room in use
    assert occupied_on_night(cfg, "blue_room", WEEK_A_MON + timedelta(days=1))
    # Week A Thursday: neither -> empty
    assert not occupied_on_night(cfg, "blue_room", WEEK_A_MON + timedelta(days=3))


def test_exception_changes_room_occupancy():
    cfg = make_config()
    thu = WEEK_A_MON + timedelta(days=3)
    assert not occupied_on_night(cfg, "blue_room", thu)
    cfg.exceptions.append(ExceptionNight("quinn", thu, True))
    assert occupied_on_night(cfg, "blue_room", thu)


# ---- calendar people ----


def test_calendar_person_is_away_during_event_minus_preheat():
    cfg = make_config()
    events = {"calendar.studio": [CalendarEvent(L(2026, 1, 3), L(2026, 1, 10))]}  # all-day Sat 3 to Fri 9 inclusive
    iv = person_present_intervals(cfg, person(cfg, "visitors"), L(2026, 1, 1), L(2026, 1, 20), TZ, events)
    assert not contains(iv, U(2026, 1, 5, 12))
    assert not contains(iv, U(2026, 1, 9, 11, 59))
    assert contains(iv, U(2026, 1, 9, 12))  # 12 h pre-heat before the Saturday-midnight return
    assert contains(iv, U(2026, 1, 2, 23, 59))


def test_calendar_person_with_no_events_is_always_here():
    cfg = make_config()
    iv = person_present_intervals(cfg, person(cfg, "visitors"), L(2026, 1, 1), L(2026, 1, 20), TZ, {})
    assert iv == [(FAR_PAST, FAR_FUTURE)]


def test_short_event_swallowed_by_preheat_means_never_away():
    cfg = make_config()
    events = {"calendar.studio": [CalendarEvent(L(2026, 1, 5, 9), L(2026, 1, 5, 18))]}  # 9 h trip, 12 h pre-heat
    iv = person_present_intervals(cfg, person(cfg, "visitors"), L(2026, 1, 1), L(2026, 1, 20), TZ, events)
    assert iv == [(FAR_PAST, FAR_FUTURE)]


# ---- helpers ----


def test_merge_and_complement():
    a = [(U(2026, 1, 1, 10), U(2026, 1, 1, 12)), (U(2026, 1, 1, 11), U(2026, 1, 1, 13)), (U(2026, 1, 1, 13), U(2026, 1, 1, 14)), (U(2026, 1, 2), U(2026, 1, 1))]
    assert merge_intervals(a) == [(U(2026, 1, 1, 10), U(2026, 1, 1, 14))]
    comp = complement(a)
    assert comp[0] == (FAR_PAST, U(2026, 1, 1, 10))
    assert comp[-1] == (U(2026, 1, 1, 14), FAR_FUTURE)


# ---- the per-person 'here tonight' sensor ----


def test_person_here_tonight_rota_and_until():
    cfg = make_config()
    here, source, until = person_here_tonight(cfg, person(cfg, "pip"), WEEK_A_MON, TZ, {})
    assert (here, source, until) == (True, "rota", WEEK_A_MON + timedelta(days=3))  # away Thursday


def test_person_here_tonight_exception_source():
    cfg = make_config()
    thu = WEEK_A_MON + timedelta(days=3)
    cfg.exceptions.append(ExceptionNight("pip", thu, True))
    here, source, until = person_here_tonight(cfg, person(cfg, "pip"), thu, TZ, {})
    assert (here, source) == (True, "exception")
    assert until == WEEK_B_MON  # Week B Monday is the next night away


def test_person_here_tonight_always_has_no_until():
    cfg = make_config()
    assert person_here_tonight(cfg, person(cfg, "teddy"), WEEK_A_MON, TZ, {}) == (True, "always", None)


def test_person_here_tonight_calendar():
    cfg = make_config()
    events = {"calendar.studio": [CalendarEvent(L(2026, 1, 3), L(2026, 1, 10))]}
    here, source, until = person_here_tonight(cfg, person(cfg, "visitors"), WEEK_A_MON, TZ, events)
    assert (here, source, until) == (False, "calendar", date(2026, 1, 9))
