"""Plan and cost entities."""

from homeassistant.components.sensor import SensorEntity

from .entity import ChargerEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([PlanSensor(entry.runtime_data), CostSensor(entry.runtime_data)])


class PlanSensor(ChargerEntity, SensorEntity):
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator):
        super().__init__(coordinator, "plan", "Charging plan")

    @property
    def native_value(self):
        return self.coordinator.data["status"]

    @property
    def extra_state_attributes(self):
        return self.coordinator.data


class CostSensor(ChargerEntity, SensorEntity):
    _attr_native_unit_of_measurement = "EUR"
    _attr_icon = "mdi:currency-eur"
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator):
        super().__init__(coordinator, "cost", "Remaining charging cost")

    @property
    def native_value(self):
        return self.coordinator.data.get("estimated_cost_eur")
