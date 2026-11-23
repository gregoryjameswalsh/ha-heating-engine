"""Both UK clock changes.

Spring forward: last Sunday in March, 01:00 GMT -> 02:00 BST (the day is 23 h).
Fall back: last Sunday in October, 02:00 BST -> 01:00 GMT (the day is 25 h).

Dates used: 2026-10-25 (back), 2027-03-28 (forward), 2027-10-31 (back).
"""
from datetime import date, timedelta

import pytest

from heating_core import Block, EngineState, ManualOverride, Room, Temps, resolve_plan
from heating_core.occupancy import person_present_intervals
from heating_core.rota import pattern_index
from heating_core.timeutil import local_at, to_utc

from household import ANCHOR, L, TZ, U, make_config, person

SPRING_2027 = date(2027, 3, 28)
AUTUMN_2026 = date(2026, 10, 25)
AUTUMN_2027 = date(2027, 10, 31)

HOUR = timedelta(hours=1)


def segs_for(segments, room_id):
    return [s for s in segments if s.room == room_id]


@pytest.mark.parametrize("day, expected_hours", [(SPRING_2027, 23), (AUTUMN_2026, 25), (AUTUMN_2027, 25)])
def test_the_day_itself_is_23_or_25_hours(day, expected_hours):
    start = to_utc(local_at(day, "00:00", TZ))
    end = to_utc(local_at(day + timedelta(days=1), "00:00", TZ))
    assert end - start == expected_hours * HOUR


@pytest.mark.parametrize("day", [SPRING_2027, AUTUMN_2026, AUTUMN_2027])
def test_a_block_keeps_its_wall_clock_times_on_clock_change_day(day):
    """A weekend block 08:00-21:00 is still 08:00-21:00 on the wall and 13 h long."""
    cfg = make_config()
    segs = segs_for(resolve_plan(cfg, L(day.year, day.month, day.day), L(day.year, day.month, day.day) + timedelta(days=1)), "kitchen")
    warm = [s for s in segs if s.reason_code == "warm"]
    assert len(warm) == 1
    assert warm[0].start.strftime("%H:%M") == "08:00"
    assert warm[0].end.strftime("%H:%M") == "21:00"
    assert to_utc(warm[0].end) - to_utc(warm[0].start) == 13 * HOUR


@pytest.mark.parametrize("day, expected_hours", [(SPRING_2027, 23), (AUTUMN_2026, 25), (AUTUMN_2027, 25)])
def test_segments_are_contiguous_and_sum_to_the_real_day_length(day, expected_hours):
    cfg = make_config()
    start = L(day.year, day.month, day.day)
    end = start + timedelta(days=1)  # wall-clock midnight to midnight
    for room_id in ("kitchen", "attic", "blue_room", "green_room"):
        segs = segs_for(resolve_plan(cfg, start, end), room_id)
        assert segs[0].start == start and segs[-1].end == end
        total = timedelta()
        for a, b in zip(segs, segs[1:]):
            assert to_utc(a.end) == to_utc(b.start)
        for s in segs:
            total += to_utc(s.end) - to_utc(s.start)
        assert total == expected_hours * HOUR


def test_night_across_spring_forward_is_17_real_hours():
    """Arrive Sat 15:00 GMT, leave Sun 09:00 BST: 17 h, not 18."""
    cfg = make_config()
    sat = SPRING_2027 - timedelta(days=1)
    iv = person_present_intervals(cfg, person(cfg, "teddy"), L(2027, 3, 26), L(2027, 3, 30), TZ, {})
    # Teddy is always here so the stays merge; probe the two wall-clock instants directly instead
    s = to_utc(local_at(sat, "15:00", TZ))
    e = to_utc(local_at(SPRING_2027, "09:00", TZ))
    assert e - s == 17 * HOUR
    assert s == U(2027, 3, 27, 15) and e == U(2027, 3, 28, 8)
    assert iv  # and the merged stay exists


def test_night_across_fall_back_is_19_real_hours():
    sat = AUTUMN_2026 - timedelta(days=1)
    s = to_utc(local_at(sat, "15:00", TZ))
    e = to_utc(local_at(AUTUMN_2026, "09:00", TZ))
    assert e - s == 19 * HOUR
    assert s == U(2026, 10, 24, 14) and e == U(2026, 10, 25, 9)


def test_block_spanning_the_skipped_hour_is_shortened_not_broken():
    """00:30-03:00 on spring-forward day is 1.5 h of real time (02:00 never happens)."""
    cfg = make_config()
    r = Room(id="hall", name="Hall", climate="climate.hall_valve", temps=Temps(19, 16, 14), schedule={"sun": [Block("00:30", "03:00", 19.0)]})
    cfg.rooms.append(r)
    segs = segs_for(resolve_plan(cfg, L(2027, 3, 28), L(2027, 3, 28, 5)), "hall")
    warm = [s for s in segs if s.reason_code == "warm"]
    assert len(warm) == 1
    assert to_utc(warm[0].start) == U(2027, 3, 28, 0, 30)
    assert to_utc(warm[0].end) == U(2027, 3, 28, 2, 0)  # 03:00 BST
    assert to_utc(warm[0].end) - to_utc(warm[0].start) == timedelta(hours=1, minutes=30)
    # and nothing else is lost
    assert segs[0].start == L(2027, 3, 28) and segs[-1].end == L(2027, 3, 28, 5)
    assert to_utc(segs[-1].end) - to_utc(segs[0].start) == 4 * HOUR  # 5 wall hours minus the skipped one


def test_block_spanning_the_repeated_hour_is_lengthened_not_broken():
    """01:00-02:00 on fall-back day: 01:00 happens twice. The engine takes the
    first 01:00 (BST) and the only 02:00 (GMT), so the block is 2 h of real time
    and the segments stay contiguous. Documented behaviour, not an accident."""
    cfg = make_config()
    r = Room(id="hall", name="Hall", climate="climate.hall_valve", temps=Temps(19, 16, 14), schedule={"sun": [Block("01:00", "02:00", 19.0)]})
    cfg.rooms.append(r)
    segs = segs_for(resolve_plan(cfg, L(2026, 10, 25), L(2026, 10, 25, 4)), "hall")
    warm = [s for s in segs if s.reason_code == "warm"]
    assert len(warm) == 1
    assert to_utc(warm[0].start) == U(2026, 10, 25, 0)  # 01:00 BST
    assert to_utc(warm[0].end) == U(2026, 10, 25, 2)  # 02:00 GMT
    for a, b in zip(segs, segs[1:]):
        assert to_utc(a.end) == to_utc(b.start)


def test_rota_index_is_about_dates_not_elapsed_hours():
    # 2026-10-25 is 293 days after the anchor: 293 % 14 == 13, a Week B Sunday.
    assert (AUTUMN_2026 - ANCHOR).days == 293
    assert pattern_index(ANCHOR, AUTUMN_2026) == 13
    assert pattern_index(ANCHOR, AUTUMN_2026 + timedelta(days=1)) == 0
    assert pattern_index(ANCHOR, SPRING_2027) == (SPRING_2027 - ANCHOR).days % 14


def test_manual_change_across_fall_back_ends_at_the_wall_clock_boundary():
    """Turned up at 23:00 Saturday; schedule next changes at 08:00 Sunday (weekend
    block). That is 10 real hours on the 25-hour day, and the text still says 08:00."""
    cfg = make_config()
    state = EngineState(overrides={"kitchen": ManualOverride("kitchen", 20.0, since=L(2026, 10, 24, 23))})
    segs = segs_for(resolve_plan(cfg, L(2026, 10, 24, 22), L(2026, 10, 25, 9), state=state), "kitchen")
    manual = [s for s in segs if s.reason_code == "manual"]
    assert len(manual) == 1
    assert manual[0].end == L(2026, 10, 25, 8)
    assert to_utc(manual[0].end) - to_utc(manual[0].start) == 10 * HOUR
    assert manual[0].reason_text == "Turned up at the radiator. Back to schedule at 08:00 tomorrow"
