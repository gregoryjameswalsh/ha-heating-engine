"""Services (plan section 8.5): boost, cancel_boost, hold, cancel_hold, set_exception, clear_exception.

Every service takes an optional `source`, a short tag saying who is asking (the
heating page sends `heating_page`); it goes in the activity log with the name of
the Home Assistant user behind the call, when there is one.
"""
from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    SERVICE_BOOST,
    SERVICE_CANCEL_BOOST,
    SERVICE_CANCEL_HOLD,
    SERVICE_CLEAR_EXCEPTION,
    SERVICE_HOLD,
    SERVICE_SET_EXCEPTION,
)
from .engine import HeatingEngine, UnknownPerson, UnknownRoom
from .heating_core import HOLD_KINDS
from .heating_core.validate import ValidationError

# who is asking: 1 to 32 characters of a-z and _ (the heating page sends heating_page)
SOURCE_FIELD = vol.All(cv.string, vol.Match(r"^[a-z_]{1,32}$", msg="source must be 1 to 32 characters of a-z or _"))

BOOST_SCHEMA = vol.Schema(
    {
        vol.Required("room"): cv.string,
        vol.Optional("temp"): vol.All(vol.Coerce(float), vol.Range(min=5, max=30)),
        vol.Optional("minutes"): vol.All(vol.Coerce(int), vol.Range(min=1, max=720)),
        vol.Optional("source"): SOURCE_FIELD,
    }
)
CANCEL_BOOST_SCHEMA = vol.Schema({vol.Required("room"): cv.string, vol.Optional("source"): SOURCE_FIELD})
HOLD_SCHEMA = vol.Schema(
    {
        vol.Required("room"): cv.string,
        vol.Required("kind"): vol.In(HOLD_KINDS),
        vol.Optional("temp"): vol.All(vol.Coerce(float), vol.Range(min=5, max=30)),
        vol.Optional("until"): cv.string,  # next_change | indefinite | ISO 8601 datetime
        vol.Optional("source"): SOURCE_FIELD,
    }
)
CANCEL_HOLD_SCHEMA = vol.Schema({vol.Required("room"): cv.string, vol.Optional("source"): SOURCE_FIELD})
SET_EXCEPTION_SCHEMA = vol.Schema(
    {
        vol.Required("person"): cv.string,
        vol.Required("night"): cv.date,
        vol.Required("here"): cv.boolean,
        vol.Optional("source"): SOURCE_FIELD,
    }
)
CLEAR_EXCEPTION_SCHEMA = vol.Schema({vol.Required("person"): cv.string, vol.Required("night"): cv.date, vol.Optional("source"): SOURCE_FIELD})


async def _user_name(hass: HomeAssistant, call: ServiceCall) -> str | None:
    """The name of the Home Assistant user behind a service call, when there is one."""
    user_id = call.context.user_id
    if not user_id:
        return None
    user = await hass.auth.async_get_user(user_id)
    return user.name if user is not None else None


@callback
def async_register_services(hass: HomeAssistant, engine: HeatingEngine) -> None:
    async def boost(call: ServiceCall) -> None:
        try:
            await engine.async_boost(
                call.data["room"], call.data.get("temp"), call.data.get("minutes"), source=call.data.get("source"), user=await _user_name(hass, call)
            )
        except UnknownRoom as exc:
            raise ServiceValidationError(f"Unknown room: {exc}") from exc

    async def cancel_boost(call: ServiceCall) -> None:
        try:
            await engine.async_cancel_boost(call.data["room"], source=call.data.get("source"), user=await _user_name(hass, call))
        except UnknownRoom as exc:
            raise ServiceValidationError(f"Unknown room: {exc}") from exc

    async def hold(call: ServiceCall) -> None:
        try:
            await engine.async_hold(
                call.data["room"],
                call.data["kind"],
                call.data.get("temp"),
                call.data.get("until"),
                source=call.data.get("source"),
                user=await _user_name(hass, call),
            )
        except UnknownRoom as exc:
            raise ServiceValidationError(f"Unknown room: {exc}") from exc
        except ValidationError as exc:
            raise ServiceValidationError("; ".join(exc.errors)) from exc

    async def cancel_hold(call: ServiceCall) -> None:
        try:
            await engine.async_cancel_hold(call.data["room"], source=call.data.get("source"), user=await _user_name(hass, call))
        except UnknownRoom as exc:
            raise ServiceValidationError(f"Unknown room: {exc}") from exc

    async def set_exception(call: ServiceCall) -> None:
        try:
            await engine.async_set_exception(
                call.data["person"], call.data["night"], call.data["here"], source=call.data.get("source"), user=await _user_name(hass, call)
            )
        except UnknownPerson as exc:
            raise ServiceValidationError(f"Unknown person: {exc}") from exc
        except ValidationError as exc:
            raise ServiceValidationError("; ".join(exc.errors)) from exc

    async def clear_exception(call: ServiceCall) -> None:
        try:
            await engine.async_clear_exception(call.data["person"], call.data["night"], source=call.data.get("source"), user=await _user_name(hass, call))
        except UnknownPerson as exc:
            raise ServiceValidationError(f"Unknown person: {exc}") from exc

    hass.services.async_register(DOMAIN, SERVICE_BOOST, boost, schema=BOOST_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CANCEL_BOOST, cancel_boost, schema=CANCEL_BOOST_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_HOLD, hold, schema=HOLD_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CANCEL_HOLD, cancel_hold, schema=CANCEL_HOLD_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SET_EXCEPTION, set_exception, schema=SET_EXCEPTION_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CLEAR_EXCEPTION, clear_exception, schema=CLEAR_EXCEPTION_SCHEMA)


@callback
def async_unregister_services(hass: HomeAssistant) -> None:
    for name in (SERVICE_BOOST, SERVICE_CANCEL_BOOST, SERVICE_HOLD, SERVICE_CANCEL_HOLD, SERVICE_SET_EXCEPTION, SERVICE_CLEAR_EXCEPTION):
        hass.services.async_remove(DOMAIN, name)
