# Changelog

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
