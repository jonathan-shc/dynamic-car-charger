"""Diagnostics must never expose identity, credentials or location."""

from types import SimpleNamespace

import pytest

from custom_components.dynamic_car_charger.diagnostics import async_get_config_entry_diagnostics


@pytest.mark.asyncio
async def test_diagnostics_allowlist_excludes_personal_data():
    forecaster = SimpleNamespace(
        status="ready", market_source="kept", market={}, model=None, estimated_at=None
    )
    coordinator = SimpleNamespace(
        forecaster=forecaster,
        data={
            "status": "charging",
            "error_code": None,
            "setup": {"name": "SECRET"},
            "route": [[52, 5]],
            "token": "SECRET",
        },
        energy_mode=False,
        currency="EUR",
        forecast_warning=None,
        _saved_forecast_calibration=None,
    )
    result = await async_get_config_entry_diagnostics(
        None, SimpleNamespace(runtime_data=coordinator)
    )
    assert "SECRET" not in str(result)
    assert "route" not in result and "setup" not in result
    assert result["schema_version"] == 1 and result["status"] == "charging"
