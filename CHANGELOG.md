# Changelog

## Unreleased

- CI: pytest, byte-compile and manifest checks run on every push and tag.
- Guard test: `heating_core` must import nothing outside the standard library, and the repo must contain no IP addresses or token shapes.

## v0.1.0 — 2026-11-23

Phase 1: the engine.

- Pure resolver (`heating_core`): fortnightly rota, nights to occupied hours, calendar-driven people with a return pre-heat, holidays, boosts, manual overrides, resolution rules 1, 2, 4, 5, 7 and 9, reason text, config validation. Tested, including both UK clock-change days.
- Home Assistant shell: single-instance config flow, Store-backed config with `rev` checking, a one-minute evaluation loop, per-room `control` flag (default off: observe only), write-read-back-retry, logging and counting of every setpoint or mode change the engine did not make, per-room target sensors, per-person "here" sensors, a heartbeat health sensor, websocket commands `get_config` / `set_config` / `plan` / `log`, services `boost` / `cancel_boost` / `set_exception` / `clear_exception`.
- `docs/dashboard-notices.md`: the watchdog block that belongs outside the integration.
