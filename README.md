# Heating Engine

A small custom integration for Home Assistant that decides the target
temperature for each room from a weekly schedule **and** a fortnightly rota of
who is sleeping in the house, then sets it on the room's climate entity. Built
for Tado X valves and thermostats paired over Matter, but it only ever calls
`climate.set_temperature`, so any `climate` entity with a `heat` mode will do.

Rooms nobody is sleeping in drop to a single low "Empty" temperature. Everything
else follows the room's own weekly schedule.

## Status

Phase 1: the engine. Pure logic with tests, plus a thin Home Assistant shell.
No dashboard, no window detection, no presence sensing yet.

## What is in this repo, and what is not

Nothing about any real household is in this repository. No names, rooms,
schedules, temperatures, entity ids or addresses. The configuration lives in
Home Assistant's own storage and is created there. Tests and examples use an
invented household. A pre-push check (`scripts/check-household.ps1`) scans
every outgoing change against a denylist that is kept outside the repo.

## Install

Home Assistant 2026.11 or later, through the built-in **Marketplace**:

1. Settings, Marketplace, **Add from link**, enter
   `gregoryjameswalsh/ha-heating-engine`, Add.
2. Open the entry and **Install**. Restart Home Assistant when asked.
3. Settings, Devices and services, **Add integration**, "Heating Engine".

One instance only.

## How it decides

For each room, the first rule that applies wins:

| # | Rule | Target |
|---|---|---|
| 1 | An active boost | the boost temperature |
| 2 | Someone changed the setpoint at the valve or in another app | that temperature, until the next scheduled change |
| 4 | A holiday | the room's Empty temperature |
| 5 | Nobody who uses the room is here | the room's Empty temperature |
| 7 | Inside a warm block of the schedule | the block's temperature |
| 9 | Otherwise | the room's Eco temperature |

Rules 3, 6 and 8 (open window, everyone out, room sensed empty) are reserved
for a later phase.

A person who sleeps here on night *D* occupies their room from an arrival time
on day *D* to a departure time on day *D+1* (defaults 15:00 and 09:00).
Consecutive nights merge into one stay. People can follow a 14-night pattern, be
always present, or follow a calendar where an event means they are away, with a
pre-heat before they return.

All times are local wall-clock times in Home Assistant's zone. Both clock-change
days are covered by tests.

## Safety

- **Observe before control.** Every room has a `control` flag, default `false`.
  With it off the engine publishes its sensors and logs what it *would* write,
  and sends nothing to the valve.
- Writes happen only when the target differs from the current setpoint, always
  send mode `heat` with the temperature, are read back, and are retried three
  times before a warning is raised.
- Every setpoint change the engine did not make is logged and counted on the
  health sensor.
- A health sensor carries a heartbeat; something outside the integration should
  watch it and shout if it goes stale.

## What it exposes

Websocket commands (send them over the normal Home Assistant websocket):

| Command | Payload | Returns |
|---|---|---|
| `heating_engine/get_config` | none | `{config, rev, api_version}` |
| `heating_engine/set_config` | `{config, base_rev}` | `{rev}`, or error `rev_conflict` / `invalid_config` with a plain message |
| `heating_engine/plan` | `{start, end}` ISO datetimes, at most 15 days | `{segments: [...]}` |
| `heating_engine/log` | none | the would-write log, writes sent, and every change the engine did not make |

REST, for clients that cannot open a websocket (same contract, Home Assistant
bearer-token auth): `GET /api/heating_engine/state` (rooms, people, boosts,
exceptions and health in one call), `GET` and `POST /api/heating_engine/config`,
`GET /api/heating_engine/plan?start=&end=`, `GET /api/heating_engine/log`.
Boosts and exceptions go through the ordinary service endpoints, for example
`POST /api/services/heating_engine/boost`.

A boost writes to the valve even while the room is only observing: it is an
explicit, time-bounded request. For an observing room the engine puts back the
setpoint the valve held before the boost when it ends.

Services: `heating_engine.boost` (`room`, optional `temp`, optional `minutes`),
`heating_engine.cancel_boost` (`room`), `heating_engine.set_exception`
(`person`, `night`, `here`), `heating_engine.clear_exception` (`person`, `night`).

Entities:

| Entity | State | Attributes |
|---|---|---|
| `sensor.heating_<room>_target` | target in degrees | `mode` (observe or control), `reason_code`, `reason_text`, `rule`, `next_change`, `confirmed`, `current_setpoint`, `current_mode`, `current_temperature`, `would_write`, `pending_write`, `problem`, `boost_until` |
| `binary_sensor.heating_engine_healthy` | on / off | `last_evaluated`, `config_rev`, `api_version`, `unexpected_changes_24h`, `write_failures`, `calendar_ok`, `problems` |
| `binary_sensor.heating_<person>_here` | on / off | `source` (rota, exception, always, calendar), `until` |

The health sensor cannot report its own death. See
[docs/dashboard-notices.md](docs/dashboard-notices.md) for the watchdog that
lives outside the integration.

## Reading the observe-mode log

With every room in observe mode the engine writes nothing, but it says what it
would have written. Three places to read it:

1. **Logbook / logs.** Settings, System, Logs, search `heating_engine`. Lines
   start `would write:`, `write:` or `Unexpected setpoint change`.
2. **The websocket.** `heating_engine/log` returns the same three lists as JSON.
3. **The sensors.** `sensor.heating_<room>_target` carries `would_write: true`
   while the valve differs from the target.

A would-write line is logged once per distinct target, not every minute.

## Config document

One JSON document, stored by the integration. An example with an invented
household:

```json
{
  "version": 1,
  "rev": 1,
  "rota_anchor": "2026-01-05",
  "people": [
    { "id": "pip", "name": "Pip", "kind": "rota",
      "pattern": [1,1,1,0,1,1,1, 0,0,1,1,0,0,0], "arrive": "15:00", "leave": "09:00" },
    { "id": "teddy", "name": "Teddy", "kind": "always" },
    { "id": "visitors", "name": "Gran & Gramps", "kind": "calendar",
      "calendar": "calendar.studio", "event_means": "away", "return_preheat_hours": 12 }
  ],
  "rooms": [
    { "id": "blue_room", "name": "Blue Room",
      "climate": "climate.blue_room_valve", "watch": [],
      "occupants": ["pip"],
      "temps": { "comfort": 19.0, "eco": 16.0, "empty": 14.0 },
      "schedule": {
        "mon": [ { "start": "06:30", "end": "08:00", "temp": 19.0 },
                 { "start": "17:00", "end": "21:00", "temp": 19.0 } ],
        "tue": [], "wed": [], "thu": [], "fri": [], "sat": [], "sun": []
      },
      "control": false }
  ],
  "exceptions": [ { "person": "pip", "night": "2026-01-08", "here": true } ],
  "holidays": [ { "start": "2026-02-13", "end": "2026-02-20", "preheat_hours": 6 } ]
}
```

- `pattern` has 14 values, index 0 = the Monday of a Week A; `rota_anchor` is a
  Monday that starts a Week A.
- `occupants: []` means the room is always in use.
- A block may not cross midnight, blocks in a day may not overlap, and times are
  on quarter hours. `24:00` is allowed as an end.
- `watch` lists other climate entities in the same room that are watched for
  manual changes but never written to.
- `rev` increments on every accepted write; a write must quote the `rev` it
  started from.

## Developing

```
python -m venv .venv
.venv\Scripts\pip install pytest tzdata
.venv\Scripts\python -m pytest
```

The pure logic is in `custom_components/heating_engine/heating_core/` and has no
Home Assistant imports. `tests/conftest.py` puts the integration folder on
`sys.path` so `import heating_core` works without Home Assistant installed.
