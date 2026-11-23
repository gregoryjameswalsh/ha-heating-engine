"""Heating Engine: rota-aware room heating for Home Assistant.

Thin shell. All decisions are made in `heating_core`, which has no Home
Assistant imports; this package feeds it and acts on it.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from . import http_views, websocket
from .const import DOMAIN
from .engine import HeatingEngine
from .services import async_register_services, async_unregister_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]

type HeatingEngineConfigEntry = ConfigEntry[HeatingEngine]


async def async_setup_entry(hass: HomeAssistant, entry: HeatingEngineConfigEntry) -> bool:
    engine = HeatingEngine(hass, entry.entry_id)
    await engine.async_start()
    entry.runtime_data = engine
    hass.data.setdefault(DOMAIN, {})["engine"] = engine

    websocket.async_register(hass)
    http_views.async_register_views(hass)
    async_register_services(hass, engine)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HeatingEngineConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        async_unregister_services(hass)
        await entry.runtime_data.async_stop()
        hass.data.get(DOMAIN, {}).pop("engine", None)
    return ok
