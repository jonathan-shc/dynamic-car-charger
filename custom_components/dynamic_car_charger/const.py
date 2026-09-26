"""Integration constants."""

DOMAIN = "dynamic_car_charger"
NAME = "Dynamic Car Charger"
EVENT_CAR_CONNECTED = f"{DOMAIN}_car_connected"
SERVICE_SET_SESSION = "set_session"
SERVICE_GET_SESSIONS = "get_sessions"
SERVICE_ADD_SESSIONS = "add_sessions"
PLATFORMS = ["sensor", "number", "datetime", "switch", "button"]
DEFAULTS = {
    "capacity_kwh": 60.0,
    "power_kw": 7.4,
    "efficiency": 0.9,
    "interval_minutes": "60",
    "price_adjustment": 0.0,
    "max_price_eur_kwh": 0.20,
    "soc_max_age_minutes": 60,
    "deadline_grace_minutes": 60,
}
