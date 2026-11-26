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

Since v0.5.0 it also keeps a **learning record**: how fast each room warms up
and cools down, measured from the readings. The record acts on nothing; it is
the groundwork for warm-by times later. See [The learning record](#the-learning-record).

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
| 2 | A hold set from the dashboard | Eco, frost protection or a chosen temperature, until the hold ends |
| 3 | Someone changed the setpoint at the valve or in another app | that temperature, until the next scheduled change |
| 4 | A holiday | the room's Empty temperature |
| 5 | Nobody who uses the room is here | the room's Empty temperature |
| 7 | Inside a warm block of the schedule | the block's temperature |
| 9 | Otherwise | the room's Eco temperature |

Rules 6 and 8 (everyone out, room sensed empty) and open-window detection are
reserved for a later phase.

### Holds

A hold is the dashboard's way of saying "not the schedule, this". Four kinds:

| Kind | Holds at | Ends, unless `until` says otherwise |
|---|---|---|
| `skip` | the room's Eco temperature | at the next scheduled change |
| `eco` | the room's Eco temperature | when cancelled |
| `frost` | the room's frost-protection temperature (`temps.frost`, default 5°) | when cancelled |
| `temp` | the given `temp`, half-degree steps between 5° and 30° | at the next scheduled change |

`until` may be `next_change`, `indefinite`, or an ISO 8601 datetime. The next
scheduled change is the next moment the schedule itself would move the room's
target: a warm block starting or ending, someone arriving or leaving by the
rota or calendar, or a holiday edge. The same moment a change made at the
radiator runs to. If nothing changes within the next fifteen days the hold runs
until cancelled.

- A new hold replaces any hold on the room. Holds survive a restart and end on
  their own at `until`.
- A boost on a held room wins while it runs; the hold resumes after.
- A hand on the radiator is the latest word: a setpoint change the engine did
  not make replaces the dashboard hold, and the room's reason text says so
  ("Turned up at the radiator. Back to schedule at 21:00"). Setting a hold
  likewise replaces an earlier change made at the radiator.
- A setpoint change that lands on what the rules already ask for is not a
  manual change. The engine compares the new setpoint (in `heat` mode) with the
  room's target now and over the next 90 seconds, so a thermostat whose own
  schedule still runs and steps to the same value a few seconds either side of
  a block edge leaves the room on the schedule's own reason (`warm`, `eco`),
  and the change is not counted as unexpected. Putting the radiator back to the
  schedule's value while a manual change is on ends that manual change early.
  A boost or hold counts as "what the rules ask for" here; a change made while
  one is on and equal to its target is that boost's or hold's, not a hand on
  the radiator. The same 90-second grace applies to writes: when the valve
  already shows the target the plan is about to switch to, the engine waits
  for the edge instead of writing the old target and then the new one.
- Like a boost, a hold writes to the valve even while the room is only
  observing, and the setpoint the valve held before is put back when the hold
  ends.

The reason text for a held room reads "Skipping until 19:00", "Holding at Eco
until you turn it back on", "Frost protection until you turn it back on" or
"Set to 18° until 19:00"; an `until` on another day reads like the other rules
("06:30 tomorrow", "Fri 17:00", "Fri 27 Nov").

A person who sleeps here on night *D* occupies their room from an arrival time
on day *D* to a departure time on day *D+1* (defaults 15:00 and 09:00).
Consecutive nights merge into one stay. People can follow a 14-night pattern, be
always present, or follow a calendar where an event means they are away, with a
pre-heat before they return.

The engine reads a calendar entity the way Home Assistant's own
`GET /api/calendars/<entity>` does: it asks the entity directly rather than
calling `calendar.get_events`. The service route silently drops an entity whose
state is `unavailable` and then fails with "did not match any entities", while
the entity itself can usually still answer from what it last fetched. If a
calendar entity is unavailable the engine logs that once and carries on reading
it; if the id in the config is mis-cased or is not a calendar entity at all, the
health problem says so in words.

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
- Every request the engine accepts (a boost, a hold, an exception, a config
  save) is logged with who asked, whether or not it led to a write.
- A health sensor carries a heartbeat; something outside the integration should
  watch it and shout if it goes stale.

## What it exposes

Websocket commands (send them over the normal Home Assistant websocket):

| Command | Payload | Returns |
|---|---|---|
| `heating_engine/get_config` | none | `{config, rev, api_version}` |
| `heating_engine/set_config` | `{config, base_rev, source?}` | `{rev}`, or error `rev_conflict` / `invalid_config` with a plain message |
| `heating_engine/plan` | `{start, end}` ISO datetimes, at most 15 days | `{segments: [...]}` |
| `heating_engine/log` | none | the would-write log, writes sent, every change the engine did not make, and every request accepted |
| `heating_engine/learning` | none | the learning record: per room, the summary and every kept warm-up and cool-down |

REST, for clients that cannot open a websocket (same contract, Home Assistant
bearer-token auth):

| Route | Returns |
|---|---|
| `GET /api/heating_engine/state` | rooms, people, boosts, holds, exceptions, health and `features` in one call |
| `GET` / `POST /api/heating_engine/config` | as the websocket `get_config` / `set_config` (`POST` body `{config, base_rev, source?}`) |
| `GET /api/heating_engine/plan?start=&end=` | `{segments}` |
| `GET /api/heating_engine/log` | as the websocket `log` |
| `GET /api/heating_engine/learning` | as the websocket `learning` |
| `POST /api/heating_engine/hold` `{room, kind, temp?, until?, source?}` | `{room: <that room's state entry>, hold}`; 400 `invalid_request`, 404 `unknown_room` |
| `POST /api/heating_engine/cancel-hold` `{room, source?}` | `{room: <that room's state entry>, hold: null}` |

`features` in `state` is a list of what this engine can do beyond the first
shape of `api_version` 1, currently `["holds", "activity", "learning"]`. A page
that must work against older engines keys off it: `holds` means the hold
services and views exist, `activity` means the services take `source` and the
log carries `requests`, `learning` means each room in `state` carries a
`learning` block, the `learning` command and view exist, and the config takes
`outside_sensor`. Everything listed is additive; `api_version` stays 1.

Boosts and exceptions go through the ordinary service endpoints, for example
`POST /api/services/heating_engine/boost`.

Each room in `state` carries `hvac_action` (the climate entity's own
`hvac_action`, passed through as it is, `null` when the entity does not report
one) and `hold`: `null`, or `{kind, target, until, set_at}` with `until` an ISO
datetime or `null` for a hold that runs until cancelled. A top-level `holds`
list carries the same objects with a `room` field, as `boosts` does.

`hvac_action` is `null` for Tado X valves and thermostats paired over Matter.
Home Assistant's Matter integration only fills `hvac_action` from the
thermostat's optional `ThermostatRunningState` attribute, and Tado X devices do
not expose it (nor do Eve Thermo or Nest over Matter). So for those rooms the
engine cannot say whether the valve is calling for heat right now, and a
dashboard should treat a `null` as "not reported", not as "idle". Climate
entities from integrations that do report it (Tado's cloud integration, most
Zigbee and Z-Wave thermostats) come through as `heating`, `idle` or `off`.

A boost writes to the valve even while the room is only observing: it is an
explicit, time-bounded request. For an observing room the engine puts back the
setpoint the valve held before the boost when it ends.

Services:

| Service | Fields |
|---|---|
| `heating_engine.boost` | `room`, optional `temp`, optional `minutes`, optional `source` |
| `heating_engine.cancel_boost` | `room`, optional `source` |
| `heating_engine.hold` | `room`, `kind` (`skip`, `eco`, `frost`, `temp`), `temp` (for `temp` only), optional `until` (`next_change`, `indefinite`, or an ISO 8601 datetime; defaults to the kind's own end), optional `source` |
| `heating_engine.cancel_hold` | `room`, optional `source` |
| `heating_engine.set_exception` | `person`, `night`, `here`, optional `source` |
| `heating_engine.clear_exception` | `person`, `night`, optional `source` |

`source` says who is asking: 1 to 32 characters of `a-z` and `_`, for example
`heating_page`. It goes in the activity log next to the name of the Home
Assistant user behind the call, when the call carried one, and is copied to
the boost or hold it creates and to every write that boost or hold causes. A
call without it is logged with `source: null`.

Entities:

| Entity | State | Attributes |
|---|---|---|
| `sensor.heating_<room>_target` | target in degrees | `mode` (observe or control), `reason_code`, `reason_text`, `rule`, `next_change`, `confirmed`, `current_setpoint`, `current_mode`, `current_temperature`, `hvac_action`, `would_write`, `pending_write`, `problem`, `boost_until`, `hold_kind`, `hold_until` |
| `binary_sensor.heating_engine_healthy` | on / off | `last_evaluated`, `config_rev`, `api_version`, `unexpected_changes_24h`, `write_failures`, `calendar_ok`, `boosts_active`, `holds_active`, `problems` |
| `binary_sensor.heating_<person>_here` | on / off | `source` (rota, exception, always, calendar), `until` |

The health sensor cannot report its own death. See
[docs/dashboard-notices.md](docs/dashboard-notices.md) for the watchdog that
lives outside the integration.

## Reading the log

The engine keeps four lists, each bounded at 200 entries, oldest first, and all
of them survive a restart. `heating_engine/log` over the websocket and
`GET /api/heating_engine/log` return them as JSON:

| List | One entry per | Fields |
|---|---|---|
| `would_write` | distinct target an observing room's valve differed from (once per target, not every minute) | `when`, `room`, `entity_id`, `from`, `from_mode`, `to`, `reason` |
| `writes` | write sent to a valve | `when`, `room`, `entity_id`, `temp`, `attempt`, `reason`, `cause`, `source` |
| `unexpected_changes` | setpoint or mode change the engine did not make | `when`, `room`, `entity_id`, `kind`, `old`, `new` |
| `requests` | request the engine accepted, write or no write | `when`, `room`, `person`, `action`, `detail`, `source`, `user` |

`cause` on a write is `schedule`, `boost`, `hold`, `manual` (re-asserting a
change made at the radiator) or `restore` (putting an observing room's setpoint
back after a boost or hold). `source` is copied from the boost or hold behind
the write, so a restore after a boost from the phone is filed under the phone
too; `null` otherwise.

A request's `action` is `boost`, `cancel_boost`, `hold`, `cancel_hold`,
`set_exception`, `clear_exception` or `config`. `room` or `person` says what it
was about (both `null` for a config save). `detail` is plain words: "Boost to
21° for 15 min", "Back to the schedule", "Skip until 19:00", "Hold at Eco until
turned back on", "Frost protection until turned back on", "Set to 18° until
19:00", "Away on Wed 25 Nov", "Back to the rota on Wed 25 Nov", "Schedule or
settings changed". A cancel that found nothing to cancel, and a clear that found
nothing to clear, are not logged. `source` is the caller's tag and `user` the
Home Assistant user's name, each `null` when absent.

The same lines reach the Home Assistant log (Settings, System, Logs, search
`heating_engine`): `would write:`, `write:`, `Unexpected setpoint change`,
`Request:`, and at info level `matches the schedule` for a change that was
not counted as manual. And `sensor.heating_<room>_target` carries `would_write: true`
while an observing room's valve differs from the target.

## The learning record

The engine watches every room's temperature against its setpoint and keeps a
record of each **warm-up** and **cool-down**. It acts on nothing: no target,
block or write depends on it. It is the measured groundwork for warm-by times
("warm by 07:00", starting early by however long the room needs) in a later
release, and it makes visible how far a radiator valve's own reading is from
the room's.

- A **warm-up** starts when the setpoint rises, or heating comes on, to at
  least 1° above the reading. It ends when the reading is within 0.3° of the
  setpoint, when the setpoint or mode changes, or after four hours. Its
  **lag** is the time until the reading has risen 0.3°; its **rate**
  (degrees per hour) is a least-squares slope from there to where it reached
  the setpoint, over at most 90 minutes.
- A **cool-down** starts when the setpoint drops to at least 1.5° below the
  reading, or heating goes off. The first 45 minutes are skipped (a valve's
  sensor sits beside the radiator, which is cooling fast); the rate is the
  slope from 45 minutes to 2 h 45 min, stopping where the reading comes within
  0.3° of the new setpoint. With an `outside_sensor` in the config it also
  carries `k`, the rate over (inside minus outside).
- Each room is followed through every **source** of readings it has:
  `device`, the climate entity's own `current_temperature` (for a valve, the
  air beside the radiator), and `sensor`, the room's `temp_sensor` when one is
  set. Both are recorded side by side.
- An episode that is open when Home Assistant restarts, or whose readings go
  missing for more than ten minutes, is dropped, never guessed. One with no
  measurable rate is not kept. The newest 60 warm-ups and 60 cool-downs are
  kept per room and source, in the runtime state document (never in the
  config, never part of `rev`).
- A summary counts only episodes with at least ten samples, read from the
  source's current entity, so changing a room's `temp_sensor` starts a fresh
  count. Until a room has ten, its text says so: "Not enough warm-ups yet (4 of
  10)."

Each room in `GET state` carries `learning`:

```json
"learning": {
  "sources": {
    "device": {
      "entity": "climate.blue_room_valve",
      "label": "The heating device's own reading",
      "heat": { "count": 12, "needed": 10, "enough": true,
                "rate": { "median": 0.63, "q1": 0.55, "q3": 0.7 },
                "lag_min": 37.5, "reach_share": 0.25,
                "first": "2026-11-14T06:30:00+00:00", "last": "2026-11-24T17:02:00+00:00",
                "text": "Warms about 1° every 95 minutes, from 12 warm-ups (14 Nov to 24 Nov)." },
      "cool": { "count": 4, "needed": 10, "enough": false, "rate": { "median": -0.2, "q1": -0.3, "q3": -0.15 },
                "k": 0.015, "first": "...", "last": "...",
                "text": "Not enough cool-downs yet (4 of 10)." }
    }
  },
  "open": { "device": "heat" }
}
```

`open` names the sources with an episode in progress. `heating_engine/learning`
and `GET /api/heating_engine/learning` return `{outside_sensor, outside, rooms}`
with, per room, `name`, the same `summary` and every kept episode under
`episodes.<source>`, oldest first: `kind`, `entity`, `started`, `ended`,
`from`, `target`, `outside`, `rooms_calling` (how many rooms were asking for
heat when it started), `samples`, `rate`, `end` (`reached`, `changed` or
`timeout`), `lag_min`, `reach_min`, `k` and `usable`. Each closed episode is
also logged at info level, `Learning: ...`.

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
      "temps": { "comfort": 19.0, "eco": 16.0, "empty": 14.0, "frost": 5.0 },
      "schedule": {
        "mon": [ { "start": "06:30", "end": "08:00", "temp": 19.0 },
                 { "start": "17:00", "end": "21:00", "temp": 19.0 } ],
        "tue": [], "wed": [], "thu": [], "fri": [], "sat": [], "sun": []
      },
      "control": false }
  ],
  "exceptions": [ { "person": "pip", "night": "2026-01-08", "here": true } ],
  "holidays": [ { "start": "2026-02-13", "end": "2026-02-20", "preheat_hours": 6 } ],
  "outside_sensor": "sensor.outdoor"
}
```

- `pattern` has 14 values, index 0 = the Monday of a Week A; `rota_anchor` is a
  Monday that starts a Week A.
- `occupants: []` means the room is always in use.
- `temps.frost` is where a frost-protection hold sits. It may be left out and
  defaults to 5.
- A block may not cross midnight, blocks in a day may not overlap, and times are
  on quarter hours. `24:00` is allowed as an end.
- `watch` lists other climate entities in the same room that are watched for
  manual changes but never written to.
- `temp_sensor` (optional, per room) is a sensor with the room's air
  temperature. When set, the room's `current_temperature` comes from it, and the
  learning record measures the room from it as well as from the climate
  entity. Nothing else reads it.
- `outside_sensor` (optional) is a numeric `sensor` with the outside
  temperature, or a `weather` entity whose `temperature` attribute is read.
  Only the learning record uses it.
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
