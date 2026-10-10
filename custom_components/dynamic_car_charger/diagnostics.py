"""Privacy-preserving integration diagnostics."""

from homeassistant.core import HomeAssistant

from .const import DOMAIN


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry) -> dict:
    """Use an allowlist: names, entity IDs, tokens and routes never leave HA."""
    coordinator = entry.runtime_data
    forecaster = coordinator.forecaster
    data = coordinator.data
    return {
        "domain": DOMAIN,
        "schema_version": 1,
        "status": data.get("status"),
        "error_code": data.get("error_code"),
        "mode": "energy" if coordinator.energy_mode else "battery",
        "currency": coordinator.currency,
        "planning_method": data.get("planning_method"),
        "coverage_complete": data.get("coverage_complete"),
        "slot_count": len(data.get("slots") or []),
        "forecast": {
            "status": forecaster.status,
            "market_source": forecaster.market_source,
            "market_hours": len(forecaster.market),
            "last_market_day": (
                forecaster.model.last_known_day.isoformat()
                if forecaster.model and forecaster.model.last_known_day
                else None
            ),
            "estimated_at": forecaster.estimated_at.isoformat()
            if forecaster.estimated_at
            else None,
            "calibration_reused": coordinator.forecast_warning is not None,
            "calibration_validated_at": (
                (coordinator._saved_forecast_calibration or {}).get("validated_at")
            ),
        },
    }
