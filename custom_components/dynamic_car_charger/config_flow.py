"""Configure existing Home Assistant entities through the UI."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import DEFAULTS, DOMAIN, NAME
from .planner import price_unit
from .zones import ZONES, zone_for_location


def interval_default(values: dict[str, Any]) -> str:
    """Return the configured interval as a select option.

    Older entries stored the interval from a number field, such as 60.0.
    """
    return str(int(float(values.get("interval_minutes", DEFAULTS["interval_minutes"]))))


def schema(values: dict[str, Any], default_zone: str = "nl") -> vol.Schema:
    fields = {}
    for key in ("charger_entity", "price_entity", "power_entity"):
        marker = vol.Required(key, default=values[key]) if key in values else vol.Required(key)
        fields[marker] = selector.EntitySelector()
    # Without a battery sensor the integration charges an amount of energy.
    for key, domain in (
        ("soc_entity", ["sensor", "input_number"]),
        ("connected_entity", ["sensor", "binary_sensor"]),
        ("lock_entity", ["lock"]),
    ):
        marker = vol.Optional(key, default=values[key]) if values.get(key) else vol.Optional(key)
        fields[marker] = selector.EntitySelector(selector.EntitySelectorConfig(domain=domain))
    # One entry per state: states such as "Locked, car connected" contain commas.
    default = values.get("connected_states")
    if isinstance(default, str):
        default = [part.strip() for part in default.split(",") if part.strip()]
    marker = (
        vol.Optional("connected_states", default=default)
        if default
        else vol.Optional("connected_states")
    )
    fields[marker] = selector.SelectSelector(
        selector.SelectSelectorConfig(options=[], multiple=True, custom_value=True)
    )
    # Units are part of the field labels; see strings.json.
    for key, lo, hi, step in [
        ("capacity_kwh", 1, 300, 0.1),
        ("power_kw", 0.1, 50, 0.1),
        ("efficiency", 0.5, 1, 0.01),
        ("price_adjustment", -2, 2, 0.001),
        ("max_price_eur_kwh", 0, 5, 0.01),
        ("soc_max_age_minutes", 5, 240, 1),
        ("deadline_grace_minutes", 0, 720, 15),
    ]:
        selector_config = {"min": lo, "max": hi, "step": step, "mode": "box"}
        fields[vol.Required(key, default=values.get(key, DEFAULTS[key]))] = selector.NumberSelector(
            selector.NumberSelectorConfig(**selector_config)
        )
    fields[vol.Required("interval_minutes", default=interval_default(values))] = (
        selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=["60", "15"],
                mode=selector.SelectSelectorMode.DROPDOWN,
                translation_key="interval_minutes",
            )
        )
    )
    # The market the price forecast learns from; see zones.py.
    # Option values are lowercase, as Home Assistant's translations require.
    zone = (values.get("bidding_zone") or default_zone).lower()
    fields[vol.Required("bidding_zone", default=zone)] = selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[code.lower() for code in ZONES],
            mode=selector.SelectSelectorMode.DROPDOWN,
            translation_key="bidding_zone",
        )
    )
    return vol.Schema(fields)


def validate(hass, data: dict[str, Any]) -> dict[str, str]:
    """Only accept actual entities and explicitly understood units."""
    expected_domains = {
        "charger_entity": {"switch"},
        "soc_entity": {"sensor", "input_number"},
        "price_entity": {"sensor"},
        "power_entity": {"sensor"},
        "connected_entity": {"sensor", "binary_sensor"},
        "lock_entity": {"lock"},
    }
    for key in ("charger_entity", "soc_entity", "price_entity", "power_entity"):
        entity_id = data.get(key)
        if key == "soc_entity" and not entity_id:
            continue  # energy mode
        state = hass.states.get(entity_id)
        if state is None:
            return {key: "entity_missing"}
        if entity_id.split(".", 1)[0] not in expected_domains[key]:
            return {key: "wrong_domain"}
        unit = state.attributes.get("unit_of_measurement")
        if key == "soc_entity" and unit != "%":
            return {key: "soc_unit"}
        if key == "power_entity" and unit not in ("W", "kW"):
            return {key: "power_unit"}
        if key == "price_entity":
            try:
                price_unit(state.attributes)
            except ValueError:
                return {key: "price_unit"}
    for key in ("connected_entity", "lock_entity"):
        entity_id = data.get(key)
        if not entity_id:
            continue
        if hass.states.get(entity_id) is None:
            return {key: "entity_missing"}
        if entity_id.split(".", 1)[0] not in expected_domains[key]:
            return {key: "wrong_domain"}
    # A sensor other than a binary sensor needs the states that mean connected.
    entity_id = data.get("connected_entity")
    given = data.get("connected_states") or []
    if isinstance(given, str):
        given = [part for part in given.split(",") if part.strip()]
    if entity_id and not entity_id.startswith("binary_sensor.") and not given:
        return {"connected_states": "states_required"}
    return {}


def home_zone(hass) -> str:
    """The bidding zone that fits the country and location set in Home Assistant."""
    config = hass.config
    return zone_for_location(config.country, config.latitude, config.longitude)


def migrate_settings(values: dict[str, Any]) -> dict[str, Any]:
    """Settings from older versions in the current form.

    Version 1 had a Wallbox-specific status field. Version 2 had a vehicle state
    sensor for locking the charger when driving; the plan now decides the lock.
    """
    values = dict(values)
    status = values.pop("status_entity", None)
    if status and not values.get("connected_entity"):
        values["connected_entity"] = status
        values.setdefault("connected_states", ["Locked, car connected"])
    values.pop("vehicle_state_entity", None)
    values.pop("driving_states", None)
    return values


class DynamicCarChargerFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """One scheduler per charger."""

    VERSION = 3

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            errors = validate(self.hass, user_input)
            for entry in self.hass.config_entries.async_entries(DOMAIN):
                if (
                    entry.options.get("charger_entity", entry.data["charger_entity"])
                    == user_input["charger_entity"]
                ):
                    return self.async_abort(reason="already_configured")
            if not errors:
                await self.async_set_unique_id(user_input["charger_entity"])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=NAME, data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=schema(user_input or {}, home_zone(self.hass)),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlow()


class OptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            errors = validate(self.hass, user_input)
            for entry in self.hass.config_entries.async_entries(DOMAIN):
                if entry.entry_id != self.config_entry.entry_id and (
                    entry.options.get("charger_entity", entry.data["charger_entity"])
                    == user_input["charger_entity"]
                ):
                    errors["charger_entity"] = "charger_in_use"
            if not errors:
                # The entry is identified by its charger. Keep that in step
                # when a different charger switch is selected.
                if self.config_entry.unique_id != user_input["charger_entity"]:
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, unique_id=user_input["charger_entity"]
                    )
                return self.async_create_entry(title="", data=user_input)
        values = user_input or {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init", data_schema=schema(values, home_zone(self.hass)), errors=errors
        )
