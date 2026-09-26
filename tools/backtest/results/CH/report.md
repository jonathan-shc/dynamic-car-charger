# Price forecast backtest: CH

Period 2024-06-01 to 2026-09-23, 17736 sessions, charging power 9.9 kW, energies 10.0, 22.5, 40.0 kWh. Prices are CH market prices with the all-in formula of the Dutch NextEnergy contract; next-day prices become known at 15:00 local time.

Average oracle cost per session: EUR 4.78.

## All sessions

| Strategy | Extra cost per session (EUR) | Extra cost (%) | Sessions short | Average shortfall (kWh) |
| --- | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.0 | 0 | 0.00 |
| charge immediately | 1.986 | 41.5 | 0 | 0.00 |
| cheapest known, no threshold | 0.305 | 6.4 | 0 | 0.00 |
| threshold 0.20 | 0.228 | 4.8 | 0 | 0.00 |
| profile forecast, margin 0.00 | 0.112 | 2.3 | 0 | 0.00 |
| weather forecast, margin 0.00 | 0.085 | 1.8 | 0 | 0.00 |

## Extra cost per session by scenario (EUR)

| Strategy | evening to next morning | morning to next morning | evening to second morning | evening to second late morning | evening to third morning | evening to fourth morning | evening to fifth morning |
| --- | --: | --: | --: | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| charge immediately | 0.904 | 0.861 | 2.026 | 2.109 | 2.292 | 2.727 | 2.986 |
| cheapest known, no threshold | 0.000 | 0.048 | 0.033 | 0.092 | 0.361 | 0.670 | 0.933 |
| threshold 0.20 | 0.000 | 0.114 | 0.140 | 0.124 | 0.275 | 0.410 | 0.532 |
| profile forecast, margin 0.00 | 0.000 | 0.019 | 0.030 | 0.051 | 0.143 | 0.237 | 0.304 |
| weather forecast, margin 0.00 | 0.000 | 0.022 | 0.031 | 0.042 | 0.109 | 0.173 | 0.219 |

## Forecast accuracy (mean absolute error, EUR/kWh)

| Model | Horizon | Error |
| --- | --- | --: |
| profile | 0-24 h past known prices | 0.0222 |
| profile | 24-48 h | 0.0286 |
| weather | 0-24 h past known prices | 0.0205 |
| weather | 24-48 h | 0.0245 |
