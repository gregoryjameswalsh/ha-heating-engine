# Making failures loud: the watchdog outside the integration

The engine's health sensor, `binary_sensor.heating_engine_healthy`, carries a
heartbeat (`last_evaluated`) and a `problems` list. It cannot report its own
death: if the integration stops, the sensor keeps its last state. Something
outside the integration has to compare `last_evaluated` with the clock.

Below is a Jinja block for a **template sensor whose `notices` attribute is a
list** of `{id, severity, label, detail, max_age_min}` dicts. Paste it inside
the existing template, before the final `{{ ns.out }}`, in a sensor that
already builds `ns.out` the same way. Adjust the namespace name if yours
differs. It uses `now()`, so Home Assistant re-renders it every minute, which is
what makes the staleness check work.

```jinja
{# ---- heating engine ----
   critical: engine missing or its heartbeat older than 10 minutes.
   warning:  a valve not taking its setpoint, a valve unavailable, or the
             calendar unreadable.
   info:     a boost running for over three hours. #}
{% set h = states.binary_sensor.heating_engine_healthy %}
{% if h is none %}
  {% set ns.out = ns.out + [{
       'id': 'heating-engine-missing',
       'severity': 'critical',
       'label': 'Heating schedule has stopped',
       'detail': 'the engine is not loaded'
     }] %}
{% else %}
  {% set last = h.attributes.get('last_evaluated') %}
  {% set age = ((now() - as_datetime(last)).total_seconds()) if last else 999999 %}
  {% if h.state != 'on' or age > 600 %}
    {% set ns.out = ns.out + [{
         'id': 'heating-engine-stopped',
         'severity': 'critical',
         'label': 'Heating schedule has stopped',
         'detail': ('last ran ' ~ (age / 60) | round(0) | int ~ ' min ago') if last else 'never ran'
       }] %}
  {% endif %}
  {% for p in h.attributes.get('problems', []) %}
    {% if p.kind in ['not_responding', 'unavailable', 'calendar'] %}
      {% set ns.out = ns.out + [{
           'id': 'heating-' ~ p.kind ~ '-' ~ (p.room or 'house'),
           'severity': 'warning',
           'label': 'Heating',
           'detail': p.text
         }] %}
    {% elif p.kind == 'long_boost' %}
      {% set ns.out = ns.out + [{
           'id': 'heating-long-boost-' ~ p.room,
           'severity': 'info',
           'label': 'Heating',
           'detail': p.text,
           'max_age_min': 180
         }] %}
    {% endif %}
  {% endfor %}
{% endif %}
```

## Checking it

1. Developer Tools, Template: paste the block wrapped in
   `{% set ns = namespace(out=[]) %}` ... `{{ ns.out }}` and confirm it renders
   `[]` while the engine is healthy.
2. Stop the engine (disable the integration under Settings, Devices and
   services) and wait ten minutes: the critical notice must appear.
3. Re-enable it: the notice must clear within a minute of the next evaluation.

Step 2 is the test that matters. A watchdog that has never been seen to fire is
an assumption, not a safeguard.

## Problem kinds the engine reports

| `kind` | When |
|---|---|
| `not_responding` | three writes to a room's climate entity were not reported back within two minutes each |
| `unavailable` | the room's climate entity is `unavailable` or `unknown` |
| `calendar` | a calendar person's calendar could not be read; the last known events are kept |
| `long_boost` | a boost has run for more than three hours |
