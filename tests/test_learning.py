"""The learning record: episodes are opened, measured and closed from synthetic
rooms whose behaviour is known, and summaries say only what the data supports."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from heating_core.learning import (
    ENOUGH_EPISODES,
    END_CHANGED,
    END_REACHED,
    END_TIMEOUT,
    KIND_COOL,
    KIND_HEAT,
    SOURCE_DEVICE,
    SOURCE_SENSOR,
    Episode,
    Learning,
    Sample,
    cool_rate_words,
    heat_rate_words,
    summarize,
)

UTC = timezone.utc
T0 = datetime(2026, 12, 1, 6, 0, tzinfo=UTC)
ROOM = "blue_room"
ENTITY = "climate.blue_room_valve"


def minute(n: float) -> datetime:
    return T0 + timedelta(minutes=n)


def feed_series(learning: Learning, series, source=SOURCE_DEVICE, entity=ENTITY, room=ROOM, outside=None, rooms_calling=None):
    """series: iterable of (minute, reading, setpoint, mode). Returns the closed episodes."""
    closed = []
    for m, reading, setpoint, mode in series:
        ep = learning.feed(room, source, entity, Sample(minute(m), reading, setpoint, mode, outside, rooms_calling))
        if ep is not None:
            closed.append(ep)
    return closed


def warm_up(start_reading, setpoint, lag_min, rate_per_hour, minutes, before=5, after_setpoint=None, step=1):
    """Setpoint `before` minutes below, then raised at minute 0. Reading flat until
    the lag, then rising at the rate. After `minutes`, the setpoint moves to
    `after_setpoint` (if given) so the episode closes as changed."""
    rows = [(m, start_reading, start_reading - 2, "heat") for m in range(-before, 0)]
    m = 0
    while m <= minutes:
        rise = max(0.0, (m - lag_min) / 60 * rate_per_hour)
        rows.append((m, round(start_reading + rise, 2), setpoint, "heat"))
        m += step
    if after_setpoint is not None:
        rows.append((minutes + step, rows[-1][1], after_setpoint, "heat"))
    return rows


# ---- warm-ups ----


def test_a_slow_room_warm_up_measures_lag_and_rate():
    learning = Learning()
    # 18.0 -> 19.5: reached at 19.2, i.e. 1.2 degrees after a 20 minute lag at 0.6 C/h = 140 min
    eps = feed_series(learning, warm_up(18.0, 19.5, lag_min=20, rate_per_hour=0.6, minutes=200))
    assert len(eps) == 1
    ep = eps[0]
    assert ep.kind == KIND_HEAT and ep.end == END_REACHED and ep.reached
    assert ep.from_temp == 18.0 and ep.target == 19.5
    assert ep.rate == pytest.approx(0.6, abs=0.05)
    assert 49 <= ep.lag_min <= 51  # 20 min flat + 0.3 degree at 0.6 C/h = 30 min more
    assert ep.reach_min == pytest.approx(140, abs=2)
    assert ep.usable


def test_a_fast_valve_warm_up_reaches_in_minutes():
    learning = Learning()
    eps = feed_series(learning, warm_up(17.0, 21.0, lag_min=6, rate_per_hour=12, minutes=60))
    assert len(eps) == 1
    ep = eps[0]
    assert ep.end == END_REACHED
    assert ep.rate == pytest.approx(12, rel=0.1)
    assert ep.reach_min < 30


def test_the_rate_is_measured_over_at_most_90_minutes_after_the_lag():
    learning = Learning()
    # never reaches: 1 C/h for 90 minutes, then the room stalls; the setpoint changes at 200
    rows = []
    for m in range(-5, 0):
        rows.append((m, 16.0, 14.0, "heat"))
    for m in range(0, 201):
        lagged = max(0, m - 10)
        reading = 16.0 + min(lagged, 90) / 60 * 1.0 + (0 if lagged <= 90 else 0)
        rows.append((m, round(reading, 3), 21.0, "heat"))
    rows.append((201, rows[-1][1], 15.0, "heat"))
    eps = feed_series(learning, rows)
    heat = [e for e in eps if e.kind == KIND_HEAT]
    assert len(heat) == 1
    assert heat[0].end == END_CHANGED and heat[0].reach_min is None
    # the window starts at the lag (+0.3 degree, 28 min) and runs 90 min, mostly on the 1 C/h slope
    assert 0.6 < heat[0].rate <= 1.05


def test_a_warm_up_times_out_after_four_hours():
    learning = Learning()
    eps = feed_series(learning, warm_up(15.0, 21.0, lag_min=10, rate_per_hour=0.5, minutes=260))
    assert len(eps) == 1 and eps[0].end == END_TIMEOUT
    assert eps[0].ended - eps[0].started == timedelta(hours=4)


def test_a_small_rise_opens_nothing():
    learning = Learning()
    eps = feed_series(learning, warm_up(19.6, 20.5, lag_min=5, rate_per_hour=3, minutes=60, after_setpoint=17.0))
    assert [e for e in eps if e.kind == KIND_HEAT] == []


def test_a_room_that_never_warms_records_nothing():
    learning = Learning()
    eps = feed_series(learning, warm_up(17.0, 21.0, lag_min=999, rate_per_hour=1, minutes=60, after_setpoint=17.0))
    assert eps == []


def test_heating_coming_on_counts_as_a_rise():
    learning = Learning()
    rows = [(m, 17.0, None, "off") for m in range(-5, 0)]
    rows += [(m, round(17.0 + max(0, m - 5) / 60 * 6, 2), 20.0, "heat") for m in range(0, 60)]
    eps = feed_series(learning, rows)
    assert len(eps) == 1 and eps[0].kind == KIND_HEAT and eps[0].reached


def test_a_setpoint_change_mid_warm_up_closes_it_as_changed():
    learning = Learning()
    rows = warm_up(17.0, 21.0, lag_min=5, rate_per_hour=3, minutes=40, after_setpoint=22.5)
    # the reading carries on rising after the second change
    last = rows[-1][1]
    rows += [(41 + i, round(last + i / 60 * 3, 2), 22.5, "heat") for i in range(1, 80)]
    eps = feed_series(learning, rows)
    assert eps[0].end == END_CHANGED and eps[0].target == 21.0
    # the rise to 22.5 opened a second warm-up: 22.5 is more than 1 degree above the reading then
    assert len(eps) == 2 and eps[1].kind == KIND_HEAT and eps[1].target == 22.5 and eps[1].reached


# ---- cool-downs ----


def cool_down(start, setpoint, fast_min=45, fast_rate=2.0, slow_rate=0.15, minutes=180):
    rows = [(m, start, start, "heat") for m in range(-5, 0)]
    for m in range(0, minutes + 1):
        if m <= fast_min:
            reading = start - m / 60 * fast_rate
        else:
            reading = start - fast_min / 60 * fast_rate - (m - fast_min) / 60 * slow_rate
        rows.append((m, round(reading, 3), setpoint, "heat"))
    return rows


def test_a_cool_down_skips_the_radiator_and_measures_the_room():
    learning = Learning()
    eps = feed_series(learning, cool_down(21.0, 15.0), outside=5.0)
    assert len(eps) == 1
    ep = eps[0]
    assert ep.kind == KIND_COOL and ep.end == END_TIMEOUT
    assert ep.rate == pytest.approx(-0.15, abs=0.01)
    # inside about 19.3 over the window, outside 5: k about 0.15 / 14.3
    assert ep.k == pytest.approx(0.0105, abs=0.0005)


def test_a_cool_down_without_an_outside_temperature_has_no_k():
    learning = Learning()
    eps = feed_series(learning, cool_down(21.0, 15.0))
    assert eps[0].k is None and eps[0].outside is None


def test_a_cool_down_stops_where_it_reaches_the_new_setpoint():
    learning = Learning()
    # 21 -> 19: the fast first 45 minutes takes it to 19.5, then 1 C/h reaches 19.3 within 12 min
    eps = feed_series(learning, cool_down(21.0, 19.0, slow_rate=1.0, minutes=180))
    # within REACH_WITHIN after only 12 minutes of window: too short to measure, nothing kept
    assert eps == []


def test_heating_going_off_opens_a_cool_down_with_no_floor():
    learning = Learning()
    rows = [(m, 20.0, 20.0, "heat") for m in range(-5, 0)]
    rows += [(m, round(20.0 - (0.75 if m > 45 else m / 60) - max(0, m - 45) / 60 * 0.2, 3), None, "off") for m in range(0, 170)]
    eps = feed_series(learning, rows)
    assert len(eps) == 1 and eps[0].kind == KIND_COOL and eps[0].target is None
    assert eps[0].rate == pytest.approx(-0.2, abs=0.02)


# ---- what is dropped, never guessed ----


def test_an_episode_open_at_a_restart_is_dropped():
    rows = warm_up(17.0, 21.0, lag_min=5, rate_per_hour=6, minutes=60)
    before, after = rows[:20], rows[20:]
    first = Learning()
    assert feed_series(first, before) == []
    restarted = Learning.from_dict(first.to_dict())
    assert feed_series(restarted, after) == []  # no setpoint change seen since the restart


def test_a_gap_in_the_readings_drops_the_open_episode():
    learning = Learning()
    rows = warm_up(17.0, 21.0, lag_min=5, rate_per_hour=3, minutes=90)
    # readings missing (unavailable) from minute 20 to minute 35
    rows = [(m, None if 20 <= m < 35 else r, sp, mode) for m, r, sp, mode in rows]
    assert feed_series(learning, rows) == []


def test_a_short_gap_is_tolerated():
    learning = Learning()
    rows = warm_up(17.0, 21.0, lag_min=5, rate_per_hour=6, minutes=60)
    rows = [(m, None if 20 <= m < 25 else r, sp, mode) for m, r, sp, mode in rows]
    eps = feed_series(learning, rows)
    assert len(eps) == 1 and eps[0].reached


def test_a_changed_entity_starts_a_fresh_tracker():
    learning = Learning()
    rows = warm_up(17.0, 21.0, lag_min=5, rate_per_hour=6, minutes=60)
    feed_series(learning, rows[:20], entity="sensor.old")
    assert feed_series(learning, rows[20:], entity="sensor.new") == []


def test_unknown_source_is_refused():
    with pytest.raises(ValueError):
        Learning().feed(ROOM, "valve", ENTITY, Sample(T0, 20, 21, "heat"))


# ---- summaries ----


def ep(kind=KIND_HEAT, rate=0.6, day=0, entity=ENTITY, samples=30, end=END_REACHED, lag=30.0, k=None):
    start = T0 + timedelta(days=day)
    return Episode(
        kind=kind, entity=entity, started=start, ended=start + timedelta(hours=1), from_temp=18, target=20,
        outside=5, rooms_calling=1, samples=samples, rate=rate, end=end,
        lag_min=lag if kind == KIND_HEAT else None, reach_min=90 if kind == KIND_HEAT else None, k=k,
    )


def test_a_summary_with_too_few_episodes_says_so():
    s = summarize([ep(day=i) for i in range(4)], KIND_HEAT, UTC)
    assert s["count"] == 4 and not s["enough"] and s["needed"] == ENOUGH_EPISODES
    assert s["text"] == "Not enough warm-ups yet (4 of 10)."
    assert s["rate"]["median"] == 0.6


def test_a_summary_with_nothing_says_so():
    s = summarize([], KIND_COOL, UTC)
    assert s["count"] == 0 and s["text"] == "No cool-downs recorded yet." and s["rate"] is None


def test_a_full_summary_has_quartiles_dates_and_words():
    eps = [ep(rate=0.5 + i * 0.02, day=i) for i in range(12)]
    s = summarize(eps, KIND_HEAT, UTC)
    assert s["enough"] and s["count"] == 12
    assert s["rate"]["q1"] < s["rate"]["median"] < s["rate"]["q3"]
    assert s["lag_min"] == 30.0 and s["reach_share"] == 1.0
    assert s["text"] == "Warms about 1° every 100 minutes, from 12 warm-ups (1 Dec to 12 Dec)."


def test_a_cool_summary_reads_as_a_loss():
    eps = [ep(kind=KIND_COOL, rate=-0.16, day=i, k=0.011, end=END_TIMEOUT) for i in range(10)]
    s = summarize(eps, KIND_COOL, UTC)
    assert s["k"] == 0.011
    assert s["text"] == "Loses about 0.2° an hour once the heating drops, from 10 cool-downs (1 Dec to 10 Dec)."


def test_episodes_with_too_few_samples_are_kept_but_not_counted():
    s = summarize([ep(samples=5), ep(day=1)], KIND_HEAT, UTC)
    assert s["count"] == 1


@pytest.mark.parametrize("rate, words", [
    (0.64, "about 1° every 95 minutes"),
    (0.3, "about 1° every 3.5 hours"),
    (1.9, "about 1° every 30 minutes"),
    (7.4, "about 7.5° an hour"),
    (14.0, "about 14° an hour"),
    (0.0, "no measurable warming"),
])
def test_heat_rate_words(rate, words):
    assert heat_rate_words(rate) == words


@pytest.mark.parametrize("rate, words", [(-0.14, "loses about 0.1° an hour"), (-0.5, "loses about 0.5° an hour"), (0.1, "barely cools"), (-0.04, "barely cools")])
def test_cool_rate_words(rate, words):
    assert cool_rate_words(rate) == words


def test_room_summary_counts_only_the_current_entity_and_lists_current_sources():
    learning = Learning()
    for i in range(3):
        learning._add(ROOM, SOURCE_SENSOR, ep(day=i, entity="sensor.old"))
    learning._add(ROOM, SOURCE_SENSOR, ep(day=5, entity="sensor.new"))
    learning._add(ROOM, SOURCE_DEVICE, ep(day=5, rate=8))
    out = learning.room_summary(ROOM, {SOURCE_DEVICE: ENTITY, SOURCE_SENSOR: "sensor.new"}, UTC)
    assert out["sources"][SOURCE_SENSOR]["heat"]["count"] == 1
    assert out["sources"][SOURCE_SENSOR]["label"] == "The room sensor"
    assert out["sources"][SOURCE_DEVICE]["heat"]["rate"]["median"] == 8
    only_device = learning.room_summary(ROOM, {SOURCE_DEVICE: ENTITY}, UTC)
    assert set(only_device["sources"]) == {SOURCE_DEVICE}
    assert out["open"] == {}


# ---- storage ----


def test_the_record_is_bounded_per_kind():
    learning = Learning(limit=5)
    for i in range(8):
        learning._add(ROOM, SOURCE_DEVICE, ep(day=i))
    for i in range(3):
        learning._add(ROOM, SOURCE_DEVICE, ep(kind=KIND_COOL, rate=-0.2, day=i))
    eps = learning.episodes[ROOM][SOURCE_DEVICE]
    heat = [e for e in eps if e.kind == KIND_HEAT]
    assert len(heat) == 5 and heat[0].started == T0 + timedelta(days=3)  # the newest five
    assert len([e for e in eps if e.kind == KIND_COOL]) == 3


def test_round_trip_and_unreadable_entries_are_skipped():
    learning = Learning()
    learning._add(ROOM, SOURCE_DEVICE, ep())
    learning._add(ROOM, SOURCE_SENSOR, ep(kind=KIND_COOL, rate=-0.1, k=0.01, end=END_TIMEOUT))
    raw = learning.to_dict()
    raw["rooms"][ROOM][SOURCE_DEVICE].append({"kind": "heat"})  # missing fields
    raw["rooms"][ROOM][SOURCE_DEVICE].append({**raw["rooms"][ROOM][SOURCE_DEVICE][0], "end": "exploded"})
    raw["rooms"][ROOM]["valve"] = [raw["rooms"][ROOM][SOURCE_DEVICE][0]]  # unknown source
    back = Learning.from_dict(raw)
    assert back.episodes[ROOM][SOURCE_DEVICE] == [learning.episodes[ROOM][SOURCE_DEVICE][0]]
    assert back.episodes[ROOM][SOURCE_SENSOR] == learning.episodes[ROOM][SOURCE_SENSOR]
    assert set(back.episodes[ROOM]) == {SOURCE_DEVICE, SOURCE_SENSOR}


@pytest.mark.parametrize("raw", [None, {}, {"rooms": []}, {"rooms": {"x": "nope"}}, "garbage"])
def test_reading_nonsense_gives_an_empty_record(raw):
    assert Learning.from_dict(raw).episodes == {}


def test_prune_drops_rooms_no_longer_configured():
    learning = Learning()
    learning._add(ROOM, SOURCE_DEVICE, ep())
    learning._add("gone", SOURCE_DEVICE, ep())
    assert learning.prune({ROOM}) is True
    assert set(learning.episodes) == {ROOM}
    assert learning.prune({ROOM}) is False


# ---- the config field the record reads ----


def test_outside_sensor_round_trips_and_is_optional():
    from heating_core import Config
    from heating_core.validate import validate_config

    base = {"version": 1, "rev": 3, "rota_anchor": "2026-11-23"}
    assert Config.from_dict(base).outside_sensor is None
    assert Config.from_dict(base).to_dict()["outside_sensor"] is None
    cfg = Config.from_dict({**base, "outside_sensor": "sensor.outdoor"})
    assert cfg.to_dict()["outside_sensor"] == "sensor.outdoor"
    assert validate_config(cfg) == []
    assert validate_config(Config.from_dict({**base, "outside_sensor": "weather.home"})) == []


@pytest.mark.parametrize("bad", ["climate.blue_room_valve", "sensor.", "outside", "sensor.Outside", "sensor.out side"])
def test_outside_sensor_must_be_a_sensor_or_weather_entity(bad):
    from heating_core import Config
    from heating_core.validate import validate_config

    errors = validate_config(Config.from_dict({"version": 1, "rev": 0, "rota_anchor": "2026-11-23", "outside_sensor": bad}))
    assert errors and "outside sensor" in errors[0]


def test_learning_is_in_the_feature_list():
    from heating_core import FEATURES

    assert "learning" in FEATURES
