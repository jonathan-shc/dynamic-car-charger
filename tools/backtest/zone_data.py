"""The integration's bidding zones and planner, loaded without Home Assistant."""

import importlib.util
import sys
from pathlib import Path

INTEGRATION = Path(__file__).resolve().parents[2] / "custom_components" / "dynamic_car_charger"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"dcc_{name}", INTEGRATION / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up
    spec.loader.exec_module(module)
    return module


zones = _load("zones")
planner = _load("planner")
WEATHER_POINTS = zones.WEATHER_POINTS
ZONES = zones.ZONES
