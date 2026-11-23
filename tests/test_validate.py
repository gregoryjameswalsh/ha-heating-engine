from datetime import date

import pytest

from heating_core import Block, ExceptionNight, Holiday, Person, Temps, ValidationError, validate_config
from heating_core.validate import parse_and_validate

from household import make_config, room


def test_the_invented_household_is_valid():
    assert validate_config(make_config()) == []


def test_parse_and_validate_round_trip():
    cfg = make_config()
    assert parse_and_validate(cfg.to_dict()).to_dict() == cfg.to_dict()


def test_parse_and_validate_reports_a_missing_field_plainly():
    with pytest.raises(ValidationError) as e:
        parse_and_validate({"people": []})
    assert "rota_anchor" in e.value.errors[0]


def test_anchor_must_be_a_monday():
    cfg = make_config()
    cfg.rota_anchor = date(2026, 1, 6)
    assert any("not a Monday" in m for m in validate_config(cfg))


def test_pattern_length_and_values():
    cfg = make_config()
    cfg.person("pip").pattern = [1] * 13
    assert any("exactly 14 nights" in m for m in validate_config(cfg))
    cfg.person("pip").pattern = [1] * 13 + [2]
    assert any("0 or 1" in m for m in validate_config(cfg))


def test_times_must_be_quarter_hours():
    cfg = make_config()
    cfg.person("quinn").arrive = "15:10"
    assert any("quarter hour" in m and "Quinn" in m for m in validate_config(cfg))
    cfg = make_config()
    room(cfg, "attic").schedule["mon"] = [Block("06:35", "08:00", 19.0)]
    assert any("quarter hour" in m and "Attic" in m for m in validate_config(cfg))


def test_bad_time_strings():
    cfg = make_config()
    room(cfg, "attic").schedule["mon"] = [Block("6am", "08:00", 19.0)]
    assert any("not a time" in m for m in validate_config(cfg))


def test_block_may_not_cross_midnight_and_may_end_at_24_00():
    cfg = make_config()
    room(cfg, "attic").schedule["mon"] = [Block("22:00", "01:00", 19.0)]
    assert any("cross midnight" in m for m in validate_config(cfg))
    cfg = make_config()
    room(cfg, "attic").schedule["mon"] = [Block("22:00", "24:00", 19.0)]
    assert validate_config(cfg) == []
    cfg = make_config()
    room(cfg, "attic").schedule["mon"] = [Block("24:00", "24:00", 19.0)]
    assert any("24:00 is not allowed" in m for m in validate_config(cfg))


def test_blocks_may_not_overlap_but_may_touch():
    cfg = make_config()
    room(cfg, "attic").schedule["mon"] = [Block("06:00", "09:00", 19.0), Block("08:00", "10:00", 19.0)]
    assert any("overlaps" in m for m in validate_config(cfg))
    cfg = make_config()
    room(cfg, "attic").schedule["mon"] = [Block("06:00", "09:00", 19.0), Block("09:00", "10:00", 20.0)]
    assert validate_config(cfg) == []


def test_temperatures_in_range_and_half_degrees():
    cfg = make_config()
    room(cfg, "attic").temps = Temps(31.0, 16.0, 14.0)
    assert any("outside 5 to 30" in m for m in validate_config(cfg))
    cfg = make_config()
    room(cfg, "attic").temps = Temps(19.2, 16.0, 14.0)
    assert any("not a half degree" in m for m in validate_config(cfg))
    cfg = make_config()
    room(cfg, "attic").schedule["mon"] = [Block("06:00", "09:00", 4.5)]
    assert any("outside" in m for m in validate_config(cfg))


def test_references_must_resolve():
    cfg = make_config()
    room(cfg, "attic").occupants = ["nobody"]
    assert any("not a known person" in m for m in validate_config(cfg))
    cfg = make_config()
    cfg.exceptions.append(ExceptionNight("nobody", date(2026, 1, 5), True))
    assert any("not a known person" in m for m in validate_config(cfg))


def test_duplicate_ids_and_climates():
    cfg = make_config()
    cfg.rooms.append(room(cfg, "attic"))
    msgs = validate_config(cfg)
    assert any("used twice" in m for m in msgs)
    assert any("already used by another room" in m for m in msgs)
    cfg = make_config()
    cfg.people.append(Person(id="pip", name="Pip again", kind="always"))
    assert any("used twice" in m for m in validate_config(cfg))


def test_written_entity_must_not_also_be_watched():
    cfg = make_config()
    r = room(cfg, "studio_lounge")
    r.watch = [r.climate]
    assert any("do not list it under watch" in m for m in validate_config(cfg))


def test_room_needs_a_climate_entity():
    cfg = make_config()
    room(cfg, "attic").climate = ""
    assert any("needs the climate entity" in m for m in validate_config(cfg))


def test_exception_for_calendar_person_is_rejected():
    cfg = make_config()
    cfg.exceptions.append(ExceptionNight("visitors", date(2026, 1, 5), False))
    assert any("edit the calendar instead" in m for m in validate_config(cfg))


def test_duplicate_exception_is_rejected():
    cfg = make_config()
    cfg.exceptions.append(ExceptionNight("pip", date(2026, 1, 5), False))
    cfg.exceptions.append(ExceptionNight("pip", date(2026, 1, 5), True))
    assert any("listed twice" in m for m in validate_config(cfg))


def test_holiday_dates_and_preheat():
    cfg = make_config(holidays=[Holiday(date(2026, 2, 20), date(2026, 2, 13))])
    assert any("return date must be after the start" in m for m in validate_config(cfg))
    cfg = make_config(holidays=[Holiday(date(2026, 2, 13), date(2026, 2, 20), preheat_hours=-1)])
    assert any("cannot be negative" in m for m in validate_config(cfg))


def test_calendar_person_needs_a_calendar():
    cfg = make_config()
    cfg.person("visitors").calendar = None
    assert any("needs a calendar entity" in m for m in validate_config(cfg))


def test_unknown_kind():
    cfg = make_config()
    cfg.person("teddy").kind = "sometimes"
    assert any("must be one of" in m for m in validate_config(cfg))


def test_every_error_is_reported_not_just_the_first():
    cfg = make_config()
    cfg.rota_anchor = date(2026, 1, 6)
    room(cfg, "attic").climate = ""
    cfg.person("pip").pattern = [1]
    assert len(validate_config(cfg)) >= 3
