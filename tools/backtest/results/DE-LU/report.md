# Price forecast backtest: DE-LU

Period 2024-06-01 to 2026-09-23, 17736 sessions, charging power 9.9 kW, energies 10.0, 22.5, 40.0 kWh. Prices are DE-LU market prices with the all-in formula of the Dutch NextEnergy contract; next-day prices become known at 15:00 local time.

Average oracle cost per session: EUR 4.14.

## All sessions

| Strategy | Extra cost per session (EUR) | Extra cost (%) | Sessions short | Average shortfall (kWh) |
| --- | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.0 | 0 | 0.00 |
| charge immediately | 2.749 | 66.5 | 0 | 0.00 |
| cheapest known, no threshold | 0.299 | 7.2 | 0 | 0.00 |
| threshold 0.20 | 0.198 | 4.8 | 0 | 0.00 |
| profile forecast, margin 0.00 | 0.151 | 3.6 | 0 | 0.00 |
| weather forecast, margin 0.00 | 0.075 | 1.8 | 0 | 0.00 |

## Extra cost per session by scenario (EUR)

| Strategy | evening to next morning | morning to next morning | evening to second morning | evening to second late morning | evening to third morning | evening to fourth morning | evening to fifth morning |
| --- | --: | --: | --: | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| charge immediately | 1.498 | 1.128 | 2.953 | 3.027 | 3.150 | 3.644 | 3.846 |
| cheapest known, no threshold | 0.000 | 0.043 | 0.042 | 0.095 | 0.384 | 0.660 | 0.870 |
| threshold 0.20 | 0.000 | 0.083 | 0.108 | 0.099 | 0.233 | 0.369 | 0.494 |
| profile forecast, margin 0.00 | 0.000 | 0.033 | 0.045 | 0.072 | 0.191 | 0.312 | 0.405 |
| weather forecast, margin 0.00 | 0.000 | 0.026 | 0.040 | 0.048 | 0.099 | 0.139 | 0.174 |

## Forecast accuracy (mean absolute error, EUR/kWh)

| Model | Horizon | Error |
| --- | --- | --: |
| profile | 0-24 h past known prices | 0.0357 |
| profile | 24-48 h | 0.0445 |
| weather | 0-24 h past known prices | 0.0266 |
| weather | 24-48 h | 0.0293 |
