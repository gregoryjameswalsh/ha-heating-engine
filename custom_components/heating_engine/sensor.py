"""One target sensor per room: `sensor.heating_<room>_target`.

State is the target the engine wants. The attributes say why, whether the
valve has confirmed it, and whether the room is in observe or control mode.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN, SIGNAL_CONFIG_CHANGED, SIGNAL_UPDATED
from .engine import HeatingEngine


def engine_device(entry_id: str) -> DeviceInfo:
    return DeviceInfo(identifiers={(DOMAIN, entry_id)}, name="Heating Engine", manufacturer="ha-heating-engine", model="heating_engine")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: Callable) -> None:
    engine: HeatingEngine = entry.runtime_data
    known: dict[str, RoomTargetSensor] = {}

    @callback
    def sync_rooms() -> None:
        wanted = {r.id: r for r in engine.config.rooms}
        new = [RoomTargetSensor(engine, entry.entry_id, rid, r.name) for rid, r in wanted.items() if rid not in known]
        for ent in new:
            known[ent.room_id] = ent
        if new:
            async_add_entities(new)
        for rid in list(known):
            if rid not in wanted:
                ent = known.pop(rid)
                _remove_entity(hass, ent)

    sync_rooms()
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_CONFIG_CHANGED, sync_rooms))


@callback
def _remove_entity(hass: HomeAssistant, ent: Any) -> None:
    registry = er.async_get(hass)
    if ent.entity_id and registry.async_get(ent.entity_id) is not None:
        registry.async_remove(ent.entity_id)  # removes the entity from Home Assistant too
    else:
        hass.async_create_task(ent.async_remove(force_remove=True))


class RoomTargetSensor(SensorEntity):
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_icon = "mdi:thermostat"

    def __init__(self, engine: HeatingEngine, entry_id: str, room_id: str, room_name: str) -> None:
        self._engine = engine
        self.room_id = room_id
        self._attr_unique_id = f"{entry_id}_target_{room_id}"
        self._attr_name = f"Heating {room_name} target"
        self.entity_id = f"sensor.heating_{room_id}_target"
        self._attr_device_info = engine_device(entry_id)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self._refresh))

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        status = self._engine.rooms.get(self.room_id)
        return status.target if status else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self._engine.rooms.get(self.room_id)
        if status is None:
            return {"room": self.room_id, "mode": None, "reason_text": None, "confirmed": False}
        return {
            "room": status.room_id,
            "room_name": status.room_name,
            "climate_entity": status.climate,
            "mode": status.mode,
            "rule": status.rule,
            "reason_code": status.reason_code,
            "reason_text": status.reason_text,
            "next_change": status.until.isoformat() if status.until else None,
            "confirmed": status.confirmed,
            "available": status.available,
            "current_setpoint": status.current_setpoint,
            "current_mode": status.current_mode,
            "current_temperature": status.current_temperature,
            "would_write": status.would_write,
            "pending_write": status.pending_write,
            "last_write": status.last_write.isoformat() if status.last_write else None,
            "problem": status.problem,
            "boost_until": status.boost_until.isoformat() if status.boost_until else None,
            "hvac_action": status.hvac_action,
            "hold_kind": status.hold.kind if status.hold else None,
            "hold_until": status.hold.until.isoformat() if status.hold and status.hold.until else None,
        }
