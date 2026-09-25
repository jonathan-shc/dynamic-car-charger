# Price forecast backtest: SE4

Period 2024-06-01 to 2026-09-23, 17736 sessions, charging power 9.9 kW, energies 10.0, 22.5, 40.0 kWh. Prices are SE4 market prices with the all-in formula of the Dutch NextEnergy contract; next-day prices become known at 15:00 local time.

Average oracle cost per session: EUR 3.82.

## All sessions

| Strategy | Extra cost per session (EUR) | Extra cost (%) | Sessions short | Average shortfall (kWh) |
| --- | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.0 | 0 | 0.00 |
| charge immediately | 2.001 | 52.4 | 0 | 0.00 |
| cheapest known, no threshold | 0.174 | 4.6 | 0 | 0.00 |
| threshold 0.20 | 0.118 | 3.1 | 0 | 0.00 |
| profile forecast, margin 0.00 | 0.158 | 4.1 | 0 | 0.00 |
| weather forecast, margin 0.00 | 0.059 | 1.6 | 0 | 0.00 |

## Extra cost per session by scenario (EUR)

| Strategy | evening to next morning | morning to next morning | evening to second morning | evening to second late morning | evening to third morning | evening to fourth morning | evening to fifth morning |
| --- | --: | --: | --: | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| charge immediately | 1.514 | 0.991 | 2.170 | 2.205 | 2.045 | 2.499 | 2.583 |
| cheapest known, no threshold | 0.000 | 0.070 | 0.057 | 0.080 | 0.223 | 0.349 | 0.439 |
| threshold 0.20 | 0.000 | 0.056 | 0.070 | 0.070 | 0.151 | 0.215 | 0.268 |
| profile forecast, margin 0.00 | 0.000 | 0.046 | 0.068 | 0.085 | 0.202 | 0.311 | 0.396 |
| weather forecast, margin 0.00 | 0.000 | 0.034 | 0.048 | 0.048 | 0.083 | 0.097 | 0.107 |

## Forecast accuracy (mean absolute error, EUR/kWh)

| Model | Horizon | Error |
| --- | --- | --: |
| profile | 0-24 h past known prices | 0.0372 |
| profile | 24-48 h | 0.0487 |
| weather | 0-24 h past known prices | 0.0315 |
| weather | 24-48 h | 0.0350 |
