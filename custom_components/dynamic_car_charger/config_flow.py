"""Configure existing Home Assistant entities through the UI."""

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import DEFAULTS, DOMAIN, NAME


def schema(values):
    fields = {}
    for key in ("charger_entity", "soc_entity", "price_entity", "power_entity"):
        marker = vol.Required(key, default=values[key]) if key in values else vol.Required(key)
        fields[marker] = selector.EntitySelector()
    for key, domain in (
        ("status_entity", "sensor"),
        ("lock_entity", "lock"),
        ("vehicle_state_entity", "sensor"),
    ):
        marker = (
            vol.Optional(key, default=values[key])
            if values.get(key)
            else vol.Optional(key)
        )
        fields[marker] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain=domain)
        )
    for key, lo, hi, step, unit in [
        ("capacity_kwh", 1, 300, 0.1, "kWh"),
        ("power_kw", 0.1, 50, 0.1, "kW"),
        ("efficiency", 0.5, 1, 0.01, None),
        ("price_adjustment", -2, 2, 0.001, "EUR/kWh"),
        ("max_price_eur_kwh", 0, 5, 0.01, "EUR/kWh"),
        ("soc_max_age_minutes", 5, 240, 1, "min"),
    ]:
        selector_config = {"min": lo, "max": hi, "step": step, "mode": "box"}
        fields[vol.Required(key, default=values.get(key, DEFAULTS[key]))] = selector.NumberSelector(
            selector.NumberSelectorConfig(**selector_config)
        )
    fields[vol.Required("interval_minutes", default=int(values.get("interval_minutes", 60)))] = selector.NumberSelector(
        selector.NumberSelectorConfig(min=15, max=60, step=45, mode="box")
    )
    return vol.Schema(fields)


def validate(hass, data):
    """Only accept actual entities and explicitly understood units."""
    expected_domains = {
        "charger_entity": {"switch"},
        "soc_entity": {"sensor", "input_number"},
        "price_entity": {"sensor"},
        "power_entity": {"sensor"},
        "status_entity": {"sensor"},
        "lock_entity": {"lock"},
        "vehicle_state_entity": {"sensor"},
    }
    for key in ("charger_entity", "soc_entity", "price_entity", "power_entity"):
        entity_id = data.get(key)
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
        if key == "price_entity" and unit not in ("EUR/kWh", "€/kWh"):
            return {key: "price_unit"}
    for key in ("status_entity", "lock_entity", "vehicle_state_entity"):
        entity_id = data.get(key)
        if not entity_id:
            continue
        if hass.states.get(entity_id) is None:
            return {key: "entity_missing"}
        if entity_id.split(".", 1)[0] not in expected_domains[key]:
            return {key: "wrong_domain"}
    return {}


class DynamicCarChargerFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """One scheduler per charger."""

    VERSION = 1

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
            step_id="user", data_schema=schema(user_input or {}), errors=errors
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
                return self.async_create_entry(title="", data=user_input)
        values = user_input or {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=schema(values), errors=errors)
