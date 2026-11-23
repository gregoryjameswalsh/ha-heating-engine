from datetime import date, timedelta

from heating_core import ExceptionNight
from heating_core.rota import pattern_index, sleeps_here, week_label

from household import ANCHOR, WEEK_A_MON, WEEK_B_MON, make_config, person


def test_pattern_index_counts_from_the_anchor_monday():
    assert pattern_index(ANCHOR, WEEK_A_MON) == 0
    assert pattern_index(ANCHOR, WEEK_A_MON + timedelta(days=6)) == 6
    assert pattern_index(ANCHOR, WEEK_B_MON) == 7
    assert pattern_index(ANCHOR, WEEK_B_MON + timedelta(days=6)) == 13
    assert pattern_index(ANCHOR, WEEK_B_MON + timedelta(days=7)) == 0  # next Week A


def test_pattern_index_works_before_the_anchor():
    # the Sunday before the anchor is the last night of a Week B
    assert pattern_index(ANCHOR, ANCHOR - timedelta(days=1)) == 13
    assert pattern_index(ANCHOR, ANCHOR - timedelta(days=14)) == 0


def test_week_label():
    assert week_label(ANCHOR, WEEK_A_MON) == "A"
    assert week_label(ANCHOR, WEEK_B_MON) == "B"
    assert week_label(ANCHOR, ANCHOR - timedelta(days=3)) == "B"


def test_rota_person_follows_pattern():
    cfg = make_config()
    pip = person(cfg, "pip")
    # Week A: Mon Tue Wed Fri Sat Sun
    expected_a = [True, True, True, False, True, True, True]
    for i, exp in enumerate(expected_a):
        here, source = sleeps_here(cfg, pip, WEEK_A_MON + timedelta(days=i))
        assert (here, source) == (exp, "rota"), f"Week A day {i}"
    # Week B: Wed Thu
    expected_b = [False, False, True, True, False, False, False]
    for i, exp in enumerate(expected_b):
        here, _ = sleeps_here(cfg, pip, WEEK_B_MON + timedelta(days=i))
        assert here == exp, f"Week B day {i}"


def test_always_person_is_always_here():
    cfg = make_config()
    teddy = person(cfg, "teddy")
    for i in range(14):
        assert sleeps_here(cfg, teddy, WEEK_A_MON + timedelta(days=i)) == (True, "always")


def test_exception_flips_one_night_only():
    cfg = make_config()
    pip = person(cfg, "pip")
    thu = WEEK_A_MON + timedelta(days=3)
    assert sleeps_here(cfg, pip, thu) == (False, "rota")
    cfg.exceptions.append(ExceptionNight("pip", thu, True))
    assert sleeps_here(cfg, pip, thu) == (True, "exception")
    assert sleeps_here(cfg, pip, thu + timedelta(days=1)) == (True, "rota")
    assert sleeps_here(cfg, pip, thu + timedelta(days=14)) == (False, "rota")  # not repeated


def test_exception_can_mark_an_always_person_away():
    cfg = make_config()
    teddy = person(cfg, "teddy")
    wed = date(2026, 1, 7)
    cfg.exceptions.append(ExceptionNight("teddy", wed, False))
    assert sleeps_here(cfg, teddy, wed) == (False, "exception")
    assert sleeps_here(cfg, teddy, wed + timedelta(days=1)) == (True, "always")
