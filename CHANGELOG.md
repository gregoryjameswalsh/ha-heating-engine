# Changelog

## v0.3.1 — 2026-11-24

- **Fix: a calendar whose entity is `unavailable` could not be read, so trips never updated.** The engine called `calendar.get_events`, which goes through Home Assistant's entity service layer; that layer silently drops an entity whose state is `unavailable` and then raises "Service call requested response data but did not match any entities", every five minutes, for as long as the entity stays that way, while `GET /api/calendars/<entity>` kept answering with the current events. Seen live: a trip shortened in the calendar that the engine did not notice. The engine now reads the calendar entity directly, the way that REST view does (`calendar_read.py`), so an unavailable calendar still answers from what it last fetched; the engine logs once when a calendar is unavailable and once when it comes back. A mis-cased id, an id that is not a calendar entity, or an unknown entity now each produce a health problem that says so in words. The read is covered by tests with stand-ins for the two Home Assistant names it uses.
- **Docs: `hvac_action` is `null` for Tado X over Matter.** Home Assistant's Matter integration only fills `hvac_action` from the optional `ThermostatRunningState` attribute, which Tado X does not expose. The README says so, and that a dashboard should read `null` as "not reported", not "idle".

## v0.3.0 — 2026-11-24

- **Holds set from the dashboard, as resolution rule 2.** A new runtime object alongside boosts: `skip` (the room's Eco until the next scheduled change), `eco` (Eco until cancelled), `frost` (the room's new `temps.frost`, default 5°, until cancelled) and `temp` (a half-degree temperature until the next scheduled change). `until` may also be `indefinite` or an ISO datetime. The next scheduled change is the same moment a change at the radiator runs to: a block edge, an arrival or departure, a holiday edge. A hold sits below a boost (which wins while it runs, then the hold resumes) and above a change made at the radiator. A hand on the radiator replaces a dashboard hold and the reason text says so; setting a hold replaces an earlier hand on the radiator. Holds persist, survive a restart and end on their own at `until`. Like a boost, a hold writes to an observing room's valve and the earlier setpoint is put back after; a boost and a hold on the same observing room share one restore point. Reason text: "Skipping until 19:00", "Holding at Eco until you turn it back on", "Frost protection until you turn it back on", "Set to 18° until 19:00".
- **The valve-read manual hold moves from rule 2 to rule 3.** Its `reason_code` (`manual`) and text are unchanged; only the `rule` number moves, to keep the number ordered by priority. Rules 6 and 8 stay reserved; the open-window rule will take a number when it is built.
- **Services** `heating_engine.hold` (`room`, `kind`, `temp`, `until`) and `heating_engine.cancel_hold` (`room`). **REST** `POST /api/heating_engine/hold` and `POST /api/heating_engine/cancel-hold` call the same code and return the room's state entry and its hold.
- **State:** each room in `GET state` gains `hvac_action` (the climate entity's own `hvac_action`, verbatim, `null` when absent) and `hold` (`null` or `{kind, target, until, set_at}`); a top-level `holds` list mirrors `boosts`. The room target sensor gains `hvac_action`, `hold_kind` and `hold_until`; the health sensor gains `holds_active`. `api_version` stays 1: every change to the state and config documents is additive (`temps.frost` defaults when missing). The only existing field whose value changes is `rule` for a manual hold, noted above.
- Runtime state (boosts, holds, overrides) is serialised in `heating_core/persist.py`, with no Home Assistant imports, so the round trip is tested.

## v0.2.2 — 2026-11-24

- **The engine no longer mistakes its own write for someone else's.** `_write` recorded the expectation only after the blocking climate call returned, but a Matter device reports the new setpoint while that call is still awaiting, so the engine saw its own echo first, counted it as an unexpected change and, for a setpoint echo, installed a manual override at the value it had just written. Seen live: a boost whose end never restored the schedule because the boost's own echo had become a "manual hold". The expectation is now recorded before the call. A guard test keeps that order.

## v0.2.1 — 2026-11-23

- **The last successful calendar read is kept across restarts.** Seen live after a Home Assistant restart: the engine evaluated before the calendar integration had loaded, `calendar.get_events` was "not found", and with no events every calendar person counted as home for five minutes. Now the last known events are stored with the runtime state and used until a read succeeds, the integration loads after `calendar`, and while nothing is known a failed read is retried every minute rather than every five. The problem text says which it is: "using the last known trips" or "nobody is treated as away".

## v0.2.0 — 2026-11-23

- **A boost writes to the valve even while the room is observing.** It is an explicit, time-bounded request. For an observing room the engine remembers the setpoint the valve held before the boost and puts it back when the boost ends or is cancelled, instead of its own schedule target. A room in control simply resumes its schedule.
- **REST views** under `/api/heating_engine/{state,config,plan,log}` with the same contract as the websocket commands, for clients without a websocket (the heating page's server). `GET state` returns rooms, people, boosts, exceptions and health in one call.
- `set_config` no longer requires an admin user, over websocket or REST: any authenticated Home Assistant user may save schedules. The household decides who holds a token.
- Health sensor gains `boosts_active`.
- CI: pytest, byte-compile and manifest checks run on every push and tag.
- Guard test: `heating_core` must import nothing outside the standard library, and the repo must contain no IP addresses or token shapes.

## v0.1.0 — 2026-11-23

Phase 1: the engine.

- Pure resolver (`heating_core`): fortnightly rota, nights to occupied hours, calendar-driven people with a return pre-heat, holidays, boosts, manual overrides, resolution rules 1, 2, 4, 5, 7 and 9, reason text, config validation. Tested, including both UK clock-change days.
- Home Assistant shell: single-instance config flow, Store-backed config with `rev` checking, a one-minute evaluation loop, per-room `control` flag (default off: observe only), write-read-back-retry, logging and counting of every setpoint or mode change the engine did not make, per-room target sensors, per-person "here" sensors, a heartbeat health sensor, websocket commands `get_config` / `set_config` / `plan` / `log`, services `boost` / `cancel_boost` / `set_exception` / `clear_exception`.
- `docs/dashboard-notices.md`: the watchdog block that belongs outside the integration.
