# Price forecast backtest: BE

Period 2024-06-01 to 2026-09-23, 17736 sessions, charging power 9.9 kW, energies 10.0, 22.5, 40.0 kWh. Prices are BE market prices with the all-in formula of the Dutch NextEnergy contract; next-day prices become known at 15:00 local time.

Average oracle cost per session: EUR 4.09.

## All sessions

| Strategy | Extra cost per session (EUR) | Extra cost (%) | Sessions short | Average shortfall (kWh) |
| --- | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.0 | 0 | 0.00 |
| charge immediately | 2.543 | 62.2 | 0 | 0.00 |
| cheapest known, no threshold | 0.300 | 7.3 | 0 | 0.00 |
| threshold 0.20 | 0.217 | 5.3 | 0 | 0.00 |
| profile forecast, margin 0.00 | 0.139 | 3.4 | 0 | 0.00 |
| weather forecast, margin 0.00 | 0.083 | 2.0 | 0 | 0.00 |

## Extra cost per session by scenario (EUR)

| Strategy | evening to next morning | morning to next morning | evening to second morning | evening to second late morning | evening to third morning | evening to fourth morning | evening to fifth morning |
| --- | --: | --: | --: | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| charge immediately | 1.359 | 1.127 | 2.667 | 2.735 | 3.001 | 3.353 | 3.566 |
| cheapest known, no threshold | 0.000 | 0.060 | 0.053 | 0.097 | 0.372 | 0.651 | 0.871 |
| threshold 0.20 | 0.000 | 0.075 | 0.100 | 0.100 | 0.266 | 0.422 | 0.559 |
| profile forecast, margin 0.00 | 0.000 | 0.022 | 0.042 | 0.065 | 0.175 | 0.289 | 0.382 |
| weather forecast, margin 0.00 | 0.000 | 0.018 | 0.035 | 0.044 | 0.108 | 0.166 | 0.213 |

## Forecast accuracy (mean absolute error, EUR/kWh)

| Model | Horizon | Error |
| --- | --- | --: |
| profile | 0-24 h past known prices | 0.0309 |
| profile | 24-48 h | 0.0387 |
| weather | 0-24 h past known prices | 0.0245 |
| weather | 24-48 h | 0.0279 |
