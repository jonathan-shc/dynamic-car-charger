# Price forecast backtest

Period 2024-06-01 to 2026-09-22, 12660 sessions, charging power 9.9 kW, energies 10.0, 22.5, 40.0 kWh. Prices are all-in NextEnergy prices; next-day prices become known at 15:00.

Average oracle cost per session: EUR 4.37.

## All sessions

| Strategy | Extra cost per session (EUR) | Extra cost (%) | Sessions short | Average shortfall (kWh) |
| --- | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.0 | 0 | 0.00 |
| cheapest known, no threshold | 0.114 | 2.6 | 0 | 0.00 |
| threshold 0.18 | 0.130 | 3.0 | 0 | 0.00 |
| threshold 0.20 | 0.103 | 2.4 | 0 | 0.00 |
| threshold 0.25 | 0.103 | 2.4 | 0 | 0.00 |
| threshold 0.30 | 0.113 | 2.6 | 0 | 0.00 |
| profile forecast, margin 0.00 | 0.067 | 1.5 | 0 | 0.00 |
| profile forecast, margin 0.02 | 0.080 | 1.8 | 0 | 0.00 |
| profile forecast, margin 0.04 | 0.099 | 2.3 | 0 | 0.00 |
| weather forecast, margin 0.00 | 0.038 | 0.9 | 0 | 0.00 |
| weather forecast, margin 0.02 | 0.043 | 1.0 | 0 | 0.00 |
| weather forecast, margin 0.03 | 0.053 | 1.2 | 0 | 0.00 |
| weather forecast, margin 0.04 | 0.064 | 1.5 | 0 | 0.00 |
| weather forecast, margin 0.06 | 0.085 | 1.9 | 0 | 0.00 |

## Extra cost per session by scenario (EUR)

| Strategy | evening to next morning | morning to next morning | evening to second morning | evening to second late morning | evening to third morning |
| --- | --: | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| cheapest known, no threshold | 0.000 | 0.053 | 0.046 | 0.094 | 0.379 |
| threshold 0.18 | 0.000 | 0.116 | 0.150 | 0.127 | 0.255 |
| threshold 0.20 | 0.000 | 0.076 | 0.103 | 0.099 | 0.240 |
| threshold 0.25 | 0.000 | 0.047 | 0.052 | 0.085 | 0.333 |
| threshold 0.30 | 0.000 | 0.053 | 0.048 | 0.092 | 0.371 |
| profile forecast, margin 0.00 | 0.000 | 0.026 | 0.044 | 0.069 | 0.196 |
| profile forecast, margin 0.02 | 0.000 | 0.035 | 0.042 | 0.077 | 0.247 |
| profile forecast, margin 0.04 | 0.000 | 0.045 | 0.046 | 0.088 | 0.317 |
| weather forecast, margin 0.00 | 0.000 | 0.022 | 0.033 | 0.041 | 0.094 |
| weather forecast, margin 0.02 | 0.000 | 0.024 | 0.024 | 0.044 | 0.122 |
| weather forecast, margin 0.03 | 0.000 | 0.027 | 0.028 | 0.053 | 0.155 |
| weather forecast, margin 0.04 | 0.000 | 0.034 | 0.032 | 0.064 | 0.190 |
| weather forecast, margin 0.06 | 0.000 | 0.043 | 0.040 | 0.081 | 0.259 |

## Forecast accuracy (mean absolute error, EUR/kWh)

| Model | Horizon | Error |
| --- | --- | --: |
| profile | 0-24 h past known prices | 0.0332 |
| profile | 24-48 h | 0.0403 |
| weather | 0-24 h past known prices | 0.0254 |
| weather | 24-48 h | 0.0277 |
