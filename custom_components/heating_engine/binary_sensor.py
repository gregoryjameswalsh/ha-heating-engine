"""`binary_sensor.heating_engine_healthy` and one `binary_sensor.heating_<person>_here`
per person.

The health sensor is a heartbeat: on while the engine evaluated inside the last
five minutes with fewer than three consecutive failures. It re-renders itself
every minute so a stalled loop shows up as `off`. If Home Assistant itself is
wedged nothing here runs, which is why something outside the integration must
also watch `last_evaluated` (see docs/dashboard-notices.md).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, SIGNAL_CONFIG_CHANGED, SIGNAL_UPDATED
from .engine import HeatingEngine


def _device(entry_id: str) -> DeviceInfo:
    return DeviceInfo(identifiers={(DOMAIN, entry_id)}, name="Heating Engine", manufacturer="ha-heating-engine", model="heating_engine")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: Callable) -> None:
    engine: HeatingEngine = entry.runtime_data
    async_add_entities([HealthSensor(engine, entry.entry_id)])

    known: dict[str, PersonHereSensor] = {}

    @callback
    def sync_people() -> None:
        wanted = {p.id: p for p in engine.config.people}
        new = [PersonHereSensor(engine, entry.entry_id, pid, p.name) for pid, p in wanted.items() if pid not in known]
        for ent in new:
            known[ent.person_id] = ent
        if new:
            async_add_entities(new)
        registry = er.async_get(hass)
        for pid in list(known):
            if pid not in wanted:
                ent = known.pop(pid)
                if ent.entity_id and registry.async_get(ent.entity_id) is not None:
                    registry.async_remove(ent.entity_id)
                else:
                    hass.async_create_task(ent.async_remove(force_remove=True))

    sync_people()
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_CONFIG_CHANGED, sync_people))


class HealthSensor(BinarySensorEntity):
    _attr_should_poll = False
    _attr_icon = "mdi:heart-pulse"

    def __init__(self, engine: HeatingEngine, entry_id: str) -> None:
        self._engine = engine
        self._attr_unique_id = f"{entry_id}_healthy"
        self._attr_name = "Heating engine healthy"
        self.entity_id = "binary_sensor.heating_engine_healthy"
        self._attr_device_info = _device(entry_id)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self._refresh))
        # re-render on a clock of its own, so a stalled engine loop reads as off
        self.async_on_remove(async_track_time_interval(self.hass, self._tick, timedelta(minutes=1)))

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()

    @callback
    def _tick(self, _now: Any) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._engine.healthy

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._engine.health_attributes()


class PersonHereSensor(BinarySensorEntity):
    _attr_should_poll = False
    _attr_device_class = BinarySensorDeviceClass.PRESENCE

    def __init__(self, engine: HeatingEngine, entry_id: str, person_id: str, name: str) -> None:
        self._engine = engine
        self.person_id = person_id
        self._attr_unique_id = f"{entry_id}_here_{person_id}"
        self._attr_name = f"Heating {name} here"
        self.entity_id = f"binary_sensor.heating_{person_id}_here"
        self._attr_device_info = _device(entry_id)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self._refresh))

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool | None:
        info = self._engine.people_here.get(self.person_id)
        return info[0] if info else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        info = self._engine.people_here.get(self.person_id)
        if info is None:
            return {"person": self.person_id, "source": None, "until": None}
        here, source, until = info
        return {"person": self.person_id, "source": source, "until": until.isoformat() if until else None}
