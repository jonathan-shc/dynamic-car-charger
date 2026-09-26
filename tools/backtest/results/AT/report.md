# Price forecast backtest: AT

Period 2024-06-01 to 2026-09-23, 17736 sessions, charging power 9.9 kW, energies 10.0, 22.5, 40.0 kWh. Prices are AT market prices with the all-in formula of the Dutch NextEnergy contract; next-day prices become known at 15:00 local time.

Average oracle cost per session: EUR 4.47.

## All sessions

| Strategy | Extra cost per session (EUR) | Extra cost (%) | Sessions short | Average shortfall (kWh) |
| --- | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.0 | 0 | 0.00 |
| charge immediately | 2.629 | 58.8 | 0 | 0.00 |
| cheapest known, no threshold | 0.323 | 7.2 | 0 | 0.00 |
| threshold 0.20 | 0.241 | 5.4 | 0 | 0.00 |
| profile forecast, margin 0.00 | 0.138 | 3.1 | 0 | 0.00 |
| weather forecast, margin 0.00 | 0.087 | 1.9 | 0 | 0.00 |

## Extra cost per session by scenario (EUR)

| Strategy | evening to next morning | morning to next morning | evening to second morning | evening to second late morning | evening to third morning | evening to fourth morning | evening to fifth morning |
| --- | --: | --: | --: | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| charge immediately | 1.468 | 1.003 | 2.817 | 2.921 | 2.937 | 3.518 | 3.745 |
| cheapest known, no threshold | 0.000 | 0.057 | 0.047 | 0.134 | 0.407 | 0.693 | 0.928 |
| threshold 0.20 | 0.000 | 0.116 | 0.154 | 0.135 | 0.295 | 0.434 | 0.555 |
| profile forecast, margin 0.00 | 0.000 | 0.026 | 0.046 | 0.079 | 0.179 | 0.278 | 0.357 |
| weather forecast, margin 0.00 | 0.000 | 0.023 | 0.042 | 0.061 | 0.118 | 0.166 | 0.195 |

## Forecast accuracy (mean absolute error, EUR/kWh)

| Model | Horizon | Error |
| --- | --- | --: |
| profile | 0-24 h past known prices | 0.0314 |
| profile | 24-48 h | 0.0390 |
| weather | 0-24 h past known prices | 0.0264 |
| weather | 24-48 h | 0.0294 |
