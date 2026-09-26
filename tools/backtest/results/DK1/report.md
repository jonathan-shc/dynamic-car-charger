# Price forecast backtest: DK1

Period 2024-06-01 to 2026-09-23, 17736 sessions, charging power 9.9 kW, energies 10.0, 22.5, 40.0 kWh. Prices are DK1 market prices with the all-in formula of the Dutch NextEnergy contract; next-day prices become known at 15:00 local time.

Average oracle cost per session: EUR 4.17.

## All sessions

| Strategy | Extra cost per session (EUR) | Extra cost (%) | Sessions short | Average shortfall (kWh) |
| --- | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.0 | 0 | 0.00 |
| charge immediately | 2.433 | 58.3 | 0 | 0.00 |
| cheapest known, no threshold | 0.238 | 5.7 | 0 | 0.00 |
| threshold 0.20 | 0.151 | 3.6 | 0 | 0.00 |
| profile forecast, margin 0.00 | 0.148 | 3.6 | 0 | 0.00 |
| weather forecast, margin 0.00 | 0.057 | 1.4 | 0 | 0.00 |

## Extra cost per session by scenario (EUR)

| Strategy | evening to next morning | morning to next morning | evening to second morning | evening to second late morning | evening to third morning | evening to fourth morning | evening to fifth morning |
| --- | --: | --: | --: | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| charge immediately | 1.420 | 1.008 | 2.644 | 2.714 | 2.781 | 3.165 | 3.302 |
| cheapest known, no threshold | 0.000 | 0.050 | 0.046 | 0.095 | 0.312 | 0.509 | 0.655 |
| threshold 0.20 | 0.000 | 0.091 | 0.109 | 0.097 | 0.187 | 0.259 | 0.313 |
| profile forecast, margin 0.00 | 0.000 | 0.042 | 0.050 | 0.078 | 0.183 | 0.296 | 0.390 |
| weather forecast, margin 0.00 | 0.000 | 0.027 | 0.033 | 0.041 | 0.076 | 0.101 | 0.119 |

## Forecast accuracy (mean absolute error, EUR/kWh)

| Model | Horizon | Error |
| --- | --- | --: |
| profile | 0-24 h past known prices | 0.0371 |
| profile | 24-48 h | 0.0469 |
| weather | 0-24 h past known prices | 0.0278 |
| weather | 24-48 h | 0.0307 |
