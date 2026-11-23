from datetime import datetime, timedelta, timezone

from heating_core.apply import (
    CONFIRMED,
    FAILED,
    MANUAL_SETPOINT,
    MODE_CHANGE,
    NO_CHANGE,
    NONE,
    OWN,
    RETRY,
    WAITING,
    ApplyTracker,
    ChangeLog,
    ChangeLogEntry,
    needs_write,
)

T0 = datetime(2026, 1, 6, 18, 0, tzinfo=timezone.utc)
E = "climate.blue_room_valve"


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


# ---- needs_write ----


def test_needs_write_only_when_setpoint_or_mode_differs():
    assert needs_write(19.0, 19.0, "heat") is False
    assert needs_write(19.0, 19.04, "heat") is False  # inside tolerance
    assert needs_write(19.0, 18.5, "heat") is True
    assert needs_write(19.0, 19.0, "off") is True  # a room switched off still needs heat back
    assert needs_write(19.0, None, "heat") is True  # unknown setpoint: write
    assert needs_write(19.0, 19.0, None) is True


# ---- read-back, retry, fail ----


def test_write_is_confirmed_when_the_entity_reports_it():
    tr = ApplyTracker()
    tr.record_write(E, "blue_room", 19.0, at(0))
    assert tr.observe(E, at(0.1), 17.0, "heat") == WAITING
    assert tr.observe(E, at(0.5), 19.0, "heat") == CONFIRMED
    assert tr.observe(E, at(0.6), 19.0, "heat") == NONE
    assert not tr.is_pending(E)


def test_confirmation_needs_heat_mode_too():
    tr = ApplyTracker()
    tr.record_write(E, "blue_room", 19.0, at(0))
    assert tr.observe(E, at(0.5), 19.0, "off") == WAITING


def test_three_attempts_then_failed():
    tr = ApplyTracker()
    tr.record_write(E, "blue_room", 19.0, at(0))
    assert tr.observe(E, at(1), 17.0, "heat") == WAITING
    assert tr.observe(E, at(2), 17.0, "heat") == RETRY  # attempt 1 timed out
    tr.record_write(E, "blue_room", 19.0, at(2))
    assert tr.pending[E].attempts == 2
    assert tr.observe(E, at(4), 17.0, "heat") == RETRY
    tr.record_write(E, "blue_room", 19.0, at(4))
    assert tr.pending[E].attempts == 3
    assert tr.observe(E, at(5), 17.0, "heat") == WAITING
    assert tr.observe(E, at(6), 17.0, "heat") == FAILED
    assert not tr.is_pending(E)
    assert tr.observe(E, at(7), 17.0, "heat") == NONE


def test_a_new_target_replaces_the_pending_write():
    tr = ApplyTracker()
    tr.record_write(E, "blue_room", 19.0, at(0))
    tr.record_write(E, "blue_room", 16.0, at(1))
    assert tr.pending[E].temp == 16.0 and tr.pending[E].attempts == 1
    assert tr.observe(E, at(1.5), 19.0, "heat") == WAITING  # the old value arriving late is not confirmation
    assert tr.observe(E, at(1.6), 16.0, "heat") == CONFIRMED


def test_forget_clears_everything_for_an_entity():
    tr = ApplyTracker()
    tr.record_write(E, "blue_room", 19.0, at(0))
    tr.forget(E)
    assert tr.observe(E, at(1), 17.0, "heat") == NONE
    assert tr.classify_change(E, at(1), 17.0, 19.0, "heat", "heat") == MANUAL_SETPOINT


# ---- classifying changes ----


def test_our_own_write_arriving_is_not_a_manual_change():
    tr = ApplyTracker()
    tr.record_write(E, "blue_room", 19.0, at(0))
    assert tr.classify_change(E, at(0.2), 17.0, 19.0, "heat", "heat") == OWN


def test_a_different_value_during_a_pending_write_is_manual():
    tr = ApplyTracker()
    tr.record_write(E, "blue_room", 19.0, at(0))
    assert tr.classify_change(E, at(0.2), 17.0, 21.0, "heat", "heat") == MANUAL_SETPOINT


def test_our_value_is_still_ours_inside_the_expect_window_but_not_after():
    tr = ApplyTracker()
    tr.record_write(E, "blue_room", 19.0, at(0))
    tr.observe(E, at(0.5), 19.0, "heat")  # confirmed
    assert tr.classify_change(E, at(3), 19.5, 19.0, "heat", "heat") == OWN  # e.g. a late duplicate report
    assert tr.classify_change(E, at(6), 19.5, 19.0, "heat", "heat") == MANUAL_SETPOINT


def test_setpoint_change_with_nothing_pending_is_manual():
    tr = ApplyTracker()
    assert tr.classify_change(E, at(0), 17.0, 21.0, "heat", "heat") == MANUAL_SETPOINT


def test_mode_change_alone_is_a_mode_change_unless_we_wrote_heat():
    tr = ApplyTracker()
    assert tr.classify_change(E, at(0), 19.0, 19.0, "heat", "off") == MODE_CHANGE
    assert tr.classify_change(E, at(0), 19.0, 19.0, "off", "heat") == MODE_CHANGE
    tr.record_write(E, "blue_room", 19.0, at(1))
    assert tr.classify_change(E, at(1.1), 19.0, 19.0, "off", "heat") == OWN


def test_expect_marks_a_followers_value_as_ours_without_a_pending_write():
    tr = ApplyTracker()
    follower = "climate.studio_lounge_valve"
    tr.expect(follower, 19.0, at(0))
    assert not tr.is_pending(follower)
    assert tr.classify_change(follower, at(0.5), 17.0, 19.0, "heat", "heat") == OWN
    assert tr.classify_change(follower, at(0.6), 19.0, 21.0, "heat", "heat") == MANUAL_SETPOINT
    assert tr.observe(follower, at(1), 19.0, "heat") == NONE


def test_unavailable_transitions_are_not_changes():
    tr = ApplyTracker()
    assert tr.classify_change(E, at(0), 19.0, None, "heat", None) == NO_CHANGE
    assert tr.classify_change(E, at(0), None, 19.0, None, "heat") == NO_CHANGE


def test_identical_report_is_no_change():
    tr = ApplyTracker()
    assert tr.classify_change(E, at(0), 19.0, 19.0, "heat", "heat") == NO_CHANGE
    assert tr.classify_change(E, at(0), 19.0, 19.04, "heat", "heat") == NO_CHANGE


# ---- change log ----


def test_change_log_is_bounded_and_counts_recent():
    log = ChangeLog(limit=3)
    for i in range(5):
        log.add(ChangeLogEntry(at(i), "blue_room", E, MANUAL_SETPOINT, "17", "19"))
    assert len(log.entries) == 3
    assert [e.when for e in log.entries] == [at(2), at(3), at(4)]
    assert log.count_since(at(3)) == 2
    again = ChangeLog.from_list(log.to_list(), limit=3)
    assert [e.when for e in again.entries] == [at(2), at(3), at(4)]


def test_change_log_skips_bad_rows_when_loading():
    log = ChangeLog.from_list([{"when": "not a date"}, {"when": at(0).isoformat(), "room": "r", "entity_id": E, "kind": MODE_CHANGE, "old": "heat", "new": "off"}])
    assert len(log.entries) == 1
