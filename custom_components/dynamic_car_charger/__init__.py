"""Dynamic Car Charger integration."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .config_flow import migrate_settings
from .const import DOMAIN, PLATFORMS, SERVICE_GET_SESSIONS, SERVICE_SET_SESSION
from .coordinator import ChargerCoordinator

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SET_SESSION_SCHEMA = vol.Schema(
    {
        vol.Optional("config_entry_id"): cv.string,
        vol.Optional("target_percentage"): vol.All(vol.Coerce(float), vol.Range(0, 100)),
        vol.Optional("energy_to_charge"): vol.All(vol.Coerce(float), vol.Range(0, 200)),
        vol.Optional("ready_by"): cv.datetime,
        vol.Optional("automatic_charging"): cv.boolean,
        vol.Optional("charge_now"): cv.boolean,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def set_session(call: ServiceCall) -> None:
        coordinator = _coordinator_for(hass, call.data.get("config_entry_id"))
        changes = {}
        if "target_percentage" in call.data:
            changes["target"] = call.data["target_percentage"]
        if "energy_to_charge" in call.data:
            changes["energy_goal"] = call.data["energy_to_charge"]
        if "ready_by" in call.data:
            ready_by = call.data["ready_by"]
            if ready_by.tzinfo is None:
                ready_by = ready_by.replace(tzinfo=dt_util.get_default_time_zone())
            changes["deadline"] = dt_util.as_utc(ready_by)
        if "automatic_charging" in call.data:
            changes["enabled"] = call.data["automatic_charging"]
        if "charge_now" in call.data:
            changes["immediate_charging"] = call.data["charge_now"]
        if not changes:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_changes")
        await coordinator.async_change(**changes)

    hass.services.async_register(DOMAIN, SERVICE_SET_SESSION, set_session, SET_SESSION_SCHEMA)

    async def get_sessions(call: ServiceCall) -> ServiceResponse:
        """Every finished charging session, oldest first, and the running one."""
        coordinator = _coordinator_for(hass, call.data.get("config_entry_id"))
        return coordinator.sessions_response()

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_SESSIONS,
        get_sessions,
        vol.Schema({vol.Optional("config_entry_id"): cv.string}),
        supports_response=SupportsResponse.ONLY,
    )
    return True


def _coordinator_for(hass: HomeAssistant, entry_id: str | None) -> ChargerCoordinator:
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    if entry_id is not None:
        entries = [entry for entry in entries if entry.entry_id == entry_id]
        if not entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="entry_not_loaded"
            )
    elif len(entries) != 1:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="entry_required")
    return entries[0].runtime_data


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = ChargerCoordinator(hass, entry)
    entry.runtime_data = coordinator
    await coordinator.async_start()
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await coordinator.async_stop()
        raise
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_stop()
        return True
    return False


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Bring settings from older versions to the current form; see migrate_settings."""
    if entry.version < 4:
        hass.config_entries.async_update_entry(
            entry,
            data=migrate_settings(entry.data),
            options=migrate_settings(entry.options),
            version=4,
        )
    return entry.version <= 4
