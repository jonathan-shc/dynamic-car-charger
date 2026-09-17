"""Integration constants."""

DOMAIN = "dynamic_car_charger"
NAME = "Dynamic Car Charger"
PLATFORMS = ["sensor", "number", "datetime", "switch"]
DEFAULTS = {
    "capacity_kwh": 60.0,
    "power_kw": 7.4,
    "efficiency": 0.9,
    "interval_minutes": 60,
    "price_adjustment": 0.0,
    "soc_max_age_minutes": 60,
}
