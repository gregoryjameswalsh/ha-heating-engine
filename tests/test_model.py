from datetime import date

from heating_core import Config, ExceptionNight, Holiday, Segment
from heating_core.model import DAYS

from household import L, make_config


def test_config_round_trips_through_dict():
    cfg = make_config(holidays=[Holiday(date(2026, 2, 13), date(2026, 2, 20), 6)])
    cfg.exceptions.append(ExceptionNight("pip", date(2026, 1, 8), True))
    raw = cfg.to_dict()
    again = Config.from_dict(raw)
    assert again.to_dict() == raw
    assert again.rota_anchor == cfg.rota_anchor
    assert [p.id for p in again.people] == [p.id for p in cfg.people]
    assert again.room("studio_lounge").watch == ["climate.studio_lounge_valve"]


def test_every_day_present_in_schedule_output_even_when_empty():
    cfg = make_config()
    raw = cfg.room("big_bedroom").to_dict()
    assert set(raw["schedule"]) == set(DAYS)
    assert raw["schedule"]["tue"] == []


def test_control_defaults_to_false_and_survives_round_trip():
    cfg = make_config()
    assert all(r.control is False for r in cfg.rooms)
    cfg.room("attic").control = True
    again = Config.from_dict(cfg.to_dict())
    assert again.room("attic").control is True
    assert again.room("kitchen").control is False


def test_from_dict_tolerates_missing_optional_keys():
    raw = {
        "rota_anchor": "2026-01-05",
        "people": [{"id": "x", "kind": "always"}],
        "rooms": [{"id": "r", "climate": "climate.r", "temps": {"comfort": 19, "eco": 16, "empty": 14}}],
    }
    cfg = Config.from_dict(raw)
    assert cfg.version == 1 and cfg.rev == 0
    assert cfg.people[0].name == "x"
    assert cfg.rooms[0].name == "r"
    assert cfg.rooms[0].schedule["mon"] == []


def test_segment_to_dict_is_json_friendly():
    s = Segment(room="r", start=L(2026, 1, 5, 6, 30), end=L(2026, 1, 5, 8), temp=19.0, rule=7, reason_code="warm", reason_text="Warm until 08:00", until=L(2026, 1, 5, 8))
    d = s.to_dict()
    assert d["start"].startswith("2026-01-05T06:30:00")
    assert d["until"].startswith("2026-01-05T08:00:00")
    assert d["reason_text"] == "Warm until 08:00"
