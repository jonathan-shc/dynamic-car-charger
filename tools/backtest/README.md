# Price forecast backtest

Checks whether forecasting unpublished prices gives cheaper charging than the integration's fixed price threshold. It uses real prices and archived weather forecasts. Nothing here is used by the integration itself.

## Run

```sh
pip install -r requirements-test.txt -r tools/backtest/requirements.txt
python tools/backtest/fetch.py      # download data once, into tools/backtest/data/
python tools/backtest/backtest.py   # writes tools/backtest/results/report.md and sessions.csv
```

`backtest.py --start 2025-01-01 --end 2025-01-31` runs a shorter period.

## Data

| Data | Source | Notes |
| --- | --- | --- |
| Dutch day-ahead prices | [Energy-Charts API](https://api.energy-charts.info/) (Fraunhofer ISE), CC BY 4.0, source Bundesnetzagentur / SMARD.de | Quarter hours (from October 2025) are averaged per hour. |
| Weather forecasts | [Open-Meteo previous-runs API](https://open-meteo.com/en/docs/previous-runs-api) | The forecast as it was known 1 to 6 days before each hour. Complete from March 2024. |

No API keys are needed. Energy-Charts rate-limits quick successive requests; `fetch.py` waits and retries.

Market prices are converted to all-in NextEnergy prices with `all-in = (market + 0.10967) × 1.21`. This was fitted exactly on 48 hours of Enever NextEnergy prices from September 2026. Refit it if the energy tax or the supplier's purchase fee changes.

## Method

Each session is simulated hour by hour. At every hour a strategy only sees the prices that were published by then: the next day's prices appear at 15:00. It then decides how much to charge in that hour, using the integration's own `planner.make_plan`. The same safety rule as the integration applies near the deadline.

| Strategy | Meaning |
| --- | --- |
| Oracle | Knows all prices in advance: the lowest possible cost. |
| Charge immediately | No smart charging: full power from plug-in until done. |
| Cheapest known, no threshold | Always uses the cheapest published hours. |
| Threshold X | The integration today: while prices are incomplete, only charge at or below X. |
| Profile forecast | Forecast = last known day's average + the typical hourly shape of the last 4 weeks. |
| Weather forecast | Ridge regression on hour, weekday or holiday, recent prices, and forecast wind (4 points in NL and DE), solar radiation and temperature. Retrained weekly on the past year. |

Forecast strategies plan over published and forecast prices together, but only charge in published hours. The margin is added to forecast prices, so the plan only waits for an unpublished hour when the forecast is clearly cheaper.

The weather model only uses weather forecasts that existed at the decision time, and it is only trained on prices published before the decision.

Sessions: seven plug-in/deadline patterns (1 to 5 days) × 10, 22.5 and 40 kWh at 9.9 kW, starting every day.
