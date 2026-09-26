# Price forecast backtest: FI

Period 2024-06-01 to 2026-09-23, 17736 sessions, charging power 9.9 kW, energies 10.0, 22.5, 40.0 kWh. Prices are FI market prices with the all-in formula of the Dutch NextEnergy contract; next-day prices become known at 15:00 local time.

Average oracle cost per session: EUR 3.56.

## All sessions

| Strategy | Extra cost per session (EUR) | Extra cost (%) | Sessions short | Average shortfall (kWh) |
| --- | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.0 | 0 | 0.00 |
| charge immediately | 1.403 | 39.4 | 0 | 0.00 |
| cheapest known, no threshold | 0.130 | 3.7 | 0 | 0.00 |
| threshold 0.20 | 0.087 | 2.5 | 0 | 0.00 |
| profile forecast, margin 0.00 | 0.142 | 4.0 | 0 | 0.00 |
| weather forecast, margin 0.00 | 0.047 | 1.3 | 0 | 0.00 |

## Extra cost per session by scenario (EUR)

| Strategy | evening to next morning | morning to next morning | evening to second morning | evening to second late morning | evening to third morning | evening to fourth morning | evening to fifth morning |
| --- | --: | --: | --: | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| charge immediately | 1.160 | 1.221 | 1.421 | 1.426 | 1.323 | 1.617 | 1.654 |
| cheapest known, no threshold | 0.000 | 0.081 | 0.064 | 0.067 | 0.173 | 0.241 | 0.283 |
| threshold 0.20 | 0.000 | 0.036 | 0.053 | 0.056 | 0.118 | 0.157 | 0.191 |
| profile forecast, margin 0.00 | 0.000 | 0.039 | 0.086 | 0.089 | 0.198 | 0.269 | 0.313 |
| weather forecast, margin 0.00 | 0.000 | 0.019 | 0.039 | 0.039 | 0.060 | 0.077 | 0.093 |

## Forecast accuracy (mean absolute error, EUR/kWh)

| Model | Horizon | Error |
| --- | --- | --: |
| profile | 0-24 h past known prices | 0.0397 |
| profile | 24-48 h | 0.0493 |
| weather | 0-24 h past known prices | 0.0305 |
| weather | 24-48 h | 0.0328 |
