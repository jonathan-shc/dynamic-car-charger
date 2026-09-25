# Price forecast backtest: DK2

Period 2024-06-01 to 2026-09-23, 17736 sessions, charging power 9.9 kW, energies 10.0, 22.5, 40.0 kWh. Prices are DK2 market prices with the all-in formula of the Dutch NextEnergy contract; next-day prices become known at 15:00 local time.

Average oracle cost per session: EUR 4.19.

## All sessions

| Strategy | Extra cost per session (EUR) | Extra cost (%) | Sessions short | Average shortfall (kWh) |
| --- | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.0 | 0 | 0.00 |
| charge immediately | 2.481 | 59.2 | 0 | 0.00 |
| cheapest known, no threshold | 0.233 | 5.6 | 0 | 0.00 |
| threshold 0.20 | 0.144 | 3.4 | 0 | 0.00 |
| profile forecast, margin 0.00 | 0.156 | 3.7 | 0 | 0.00 |
| weather forecast, margin 0.00 | 0.062 | 1.5 | 0 | 0.00 |

## Extra cost per session by scenario (EUR)

| Strategy | evening to next morning | morning to next morning | evening to second morning | evening to second late morning | evening to third morning | evening to fourth morning | evening to fifth morning |
| --- | --: | --: | --: | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| charge immediately | 1.561 | 1.051 | 2.706 | 2.770 | 2.751 | 3.199 | 3.331 |
| cheapest known, no threshold | 0.000 | 0.053 | 0.052 | 0.098 | 0.305 | 0.493 | 0.632 |
| threshold 0.20 | 0.000 | 0.083 | 0.106 | 0.098 | 0.184 | 0.244 | 0.294 |
| profile forecast, margin 0.00 | 0.000 | 0.041 | 0.057 | 0.084 | 0.198 | 0.309 | 0.400 |
| weather forecast, margin 0.00 | 0.000 | 0.030 | 0.036 | 0.045 | 0.091 | 0.111 | 0.123 |

## Forecast accuracy (mean absolute error, EUR/kWh)

| Model | Horizon | Error |
| --- | --- | --: |
| profile | 0-24 h past known prices | 0.0386 |
| profile | 24-48 h | 0.0489 |
| weather | 0-24 h past known prices | 0.0305 |
| weather | 24-48 h | 0.0334 |
