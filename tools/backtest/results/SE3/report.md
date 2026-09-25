# Price forecast backtest: SE3

Period 2024-06-01 to 2026-09-23, 17736 sessions, charging power 9.9 kW, energies 10.0, 22.5, 40.0 kWh. Prices are SE3 market prices with the all-in formula of the Dutch NextEnergy contract; next-day prices become known at 15:00 local time.

Average oracle cost per session: EUR 3.70.

## All sessions

| Strategy | Extra cost per session (EUR) | Extra cost (%) | Sessions short | Average shortfall (kWh) |
| --- | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.0 | 0 | 0.00 |
| charge immediately | 1.388 | 37.5 | 0 | 0.00 |
| cheapest known, no threshold | 0.142 | 3.8 | 0 | 0.00 |
| threshold 0.20 | 0.105 | 2.8 | 0 | 0.00 |
| profile forecast, margin 0.00 | 0.135 | 3.6 | 0 | 0.00 |
| weather forecast, margin 0.00 | 0.045 | 1.2 | 0 | 0.00 |

## Extra cost per session by scenario (EUR)

| Strategy | evening to next morning | morning to next morning | evening to second morning | evening to second late morning | evening to third morning | evening to fourth morning | evening to fifth morning |
| --- | --: | --: | --: | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| charge immediately | 1.064 | 0.827 | 1.472 | 1.496 | 1.308 | 1.740 | 1.810 |
| cheapest known, no threshold | 0.000 | 0.055 | 0.050 | 0.065 | 0.182 | 0.284 | 0.357 |
| threshold 0.20 | 0.000 | 0.036 | 0.051 | 0.059 | 0.135 | 0.201 | 0.256 |
| profile forecast, margin 0.00 | 0.000 | 0.036 | 0.064 | 0.074 | 0.176 | 0.264 | 0.332 |
| weather forecast, margin 0.00 | 0.000 | 0.028 | 0.042 | 0.038 | 0.061 | 0.070 | 0.079 |

## Forecast accuracy (mean absolute error, EUR/kWh)

| Model | Horizon | Error |
| --- | --- | --: |
| profile | 0-24 h past known prices | 0.0284 |
| profile | 24-48 h | 0.0375 |
| weather | 0-24 h past known prices | 0.0233 |
| weather | 24-48 h | 0.0257 |
