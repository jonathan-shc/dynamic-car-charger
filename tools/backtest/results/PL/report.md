# Price forecast backtest: PL

Period 2024-06-01 to 2026-09-23, 17736 sessions, charging power 9.9 kW, energies 10.0, 22.5, 40.0 kWh. Prices are PL market prices with the all-in formula of the Dutch NextEnergy contract; next-day prices become known at 15:00 local time.

Average oracle cost per session: EUR 4.45.

## All sessions

| Strategy | Extra cost per session (EUR) | Extra cost (%) | Sessions short | Average shortfall (kWh) |
| --- | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.0 | 0 | 0.00 |
| charge immediately | 3.058 | 68.6 | 0 | 0.00 |
| cheapest known, no threshold | 0.347 | 7.8 | 0 | 0.00 |
| threshold 0.20 | 0.230 | 5.2 | 0 | 0.00 |
| profile forecast, margin 0.00 | 0.156 | 3.5 | 0 | 0.00 |
| weather forecast, margin 0.00 | 0.091 | 2.0 | 0 | 0.00 |

## Extra cost per session by scenario (EUR)

| Strategy | evening to next morning | morning to next morning | evening to second morning | evening to second late morning | evening to third morning | evening to fourth morning | evening to fifth morning |
| --- | --: | --: | --: | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| charge immediately | 1.953 | 0.927 | 3.312 | 3.463 | 3.345 | 4.088 | 4.321 |
| cheapest known, no threshold | 0.000 | 0.043 | 0.036 | 0.165 | 0.441 | 0.754 | 0.991 |
| threshold 0.20 | 0.000 | 0.131 | 0.171 | 0.140 | 0.282 | 0.395 | 0.491 |
| profile forecast, margin 0.00 | 0.000 | 0.019 | 0.036 | 0.093 | 0.193 | 0.320 | 0.432 |
| weather forecast, margin 0.00 | 0.000 | 0.026 | 0.041 | 0.067 | 0.117 | 0.170 | 0.214 |

## Forecast accuracy (mean absolute error, EUR/kWh)

| Model | Horizon | Error |
| --- | --- | --: |
| profile | 0-24 h past known prices | 0.0360 |
| profile | 24-48 h | 0.0428 |
| weather | 0-24 h past known prices | 0.0284 |
| weather | 24-48 h | 0.0308 |
