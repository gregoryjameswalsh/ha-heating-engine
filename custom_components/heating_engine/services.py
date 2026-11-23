"""Services (plan section 8.5): boost, cancel_boost, set_exception, clear_exception."""
from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, SERVICE_BOOST, SERVICE_CANCEL_BOOST, SERVICE_CLEAR_EXCEPTION, SERVICE_SET_EXCEPTION
from .engine import HeatingEngine, UnknownPerson, UnknownRoom
from .heating_core.validate import ValidationError

BOOST_SCHEMA = vol.Schema(
    {
        vol.Required("room"): cv.string,
        vol.Optional("temp"): vol.All(vol.Coerce(float), vol.Range(min=5, max=30)),
        vol.Optional("minutes"): vol.All(vol.Coerce(int), vol.Range(min=1, max=720)),
    }
)
CANCEL_BOOST_SCHEMA = vol.Schema({vol.Required("room"): cv.string})
SET_EXCEPTION_SCHEMA = vol.Schema({vol.Required("person"): cv.string, vol.Required("night"): cv.date, vol.Required("here"): cv.boolean})
CLEAR_EXCEPTION_SCHEMA = vol.Schema({vol.Required("person"): cv.string, vol.Required("night"): cv.date})


@callback
def async_register_services(hass: HomeAssistant, engine: HeatingEngine) -> None:
    async def boost(call: ServiceCall) -> None:
        try:
            await engine.async_boost(call.data["room"], call.data.get("temp"), call.data.get("minutes"))
        except UnknownRoom as exc:
            raise ServiceValidationError(f"Unknown room: {exc}") from exc

    async def cancel_boost(call: ServiceCall) -> None:
        try:
            await engine.async_cancel_boost(call.data["room"])
        except UnknownRoom as exc:
            raise ServiceValidationError(f"Unknown room: {exc}") from exc

    async def set_exception(call: ServiceCall) -> None:
        try:
            await engine.async_set_exception(call.data["person"], call.data["night"], call.data["here"])
        except UnknownPerson as exc:
            raise ServiceValidationError(f"Unknown person: {exc}") from exc
        except ValidationError as exc:
            raise ServiceValidationError("; ".join(exc.errors)) from exc

    async def clear_exception(call: ServiceCall) -> None:
        try:
            await engine.async_clear_exception(call.data["person"], call.data["night"])
        except UnknownPerson as exc:
            raise ServiceValidationError(f"Unknown person: {exc}") from exc

    hass.services.async_register(DOMAIN, SERVICE_BOOST, boost, schema=BOOST_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CANCEL_BOOST, cancel_boost, schema=CANCEL_BOOST_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SET_EXCEPTION, set_exception, schema=SET_EXCEPTION_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CLEAR_EXCEPTION, clear_exception, schema=CLEAR_EXCEPTION_SCHEMA)


@callback
def async_unregister_services(hass: HomeAssistant) -> None:
    for name in (SERVICE_BOOST, SERVICE_CANCEL_BOOST, SERVICE_SET_EXCEPTION, SERVICE_CLEAR_EXCEPTION):
        hass.services.async_remove(DOMAIN, name)
