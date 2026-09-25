# Price forecast backtest: FR

Period 2024-06-01 to 2026-09-23, 17736 sessions, charging power 9.9 kW, energies 10.0, 22.5, 40.0 kWh. Prices are FR market prices with the all-in formula of the Dutch NextEnergy contract; next-day prices become known at 15:00 local time.

Average oracle cost per session: EUR 3.80.

## All sessions

| Strategy | Extra cost per session (EUR) | Extra cost (%) | Sessions short | Average shortfall (kWh) |
| --- | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.0 | 0 | 0.00 |
| charge immediately | 2.084 | 54.9 | 0 | 0.00 |
| cheapest known, no threshold | 0.220 | 5.8 | 0 | 0.00 |
| threshold 0.20 | 0.142 | 3.7 | 0 | 0.00 |
| profile forecast, margin 0.00 | 0.112 | 2.9 | 0 | 0.00 |
| weather forecast, margin 0.00 | 0.069 | 1.8 | 0 | 0.00 |

## Extra cost per session by scenario (EUR)

| Strategy | evening to next morning | morning to next morning | evening to second morning | evening to second late morning | evening to third morning | evening to fourth morning | evening to fifth morning |
| --- | --: | --: | --: | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| charge immediately | 1.254 | 0.978 | 2.181 | 2.220 | 2.519 | 2.643 | 2.797 |
| cheapest known, no threshold | 0.000 | 0.073 | 0.064 | 0.084 | 0.264 | 0.448 | 0.606 |
| threshold 0.20 | 0.000 | 0.044 | 0.062 | 0.063 | 0.165 | 0.280 | 0.378 |
| profile forecast, margin 0.00 | 0.000 | 0.025 | 0.044 | 0.057 | 0.136 | 0.221 | 0.300 |
| weather forecast, margin 0.00 | 0.000 | 0.016 | 0.030 | 0.037 | 0.085 | 0.135 | 0.179 |

## Forecast accuracy (mean absolute error, EUR/kWh)

| Model | Horizon | Error |
| --- | --- | --: |
| profile | 0-24 h past known prices | 0.0275 |
| profile | 24-48 h | 0.0356 |
| weather | 0-24 h past known prices | 0.0240 |
| weather | 24-48 h | 0.0288 |
