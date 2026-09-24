# Price forecast backtest

Period 2024-06-01 to 2026-09-22, 17715 sessions, charging power 9.9 kW, energies 10.0, 22.5, 40.0 kWh. Prices are all-in NextEnergy prices; next-day prices become known at 15:00.

Average oracle cost per session: EUR 4.08.

## All sessions

| Strategy | Extra cost per session (EUR) | Extra cost (%) | Sessions short | Average shortfall (kWh) |
| --- | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.0 | 0 | 0.00 |
| charge immediately | 2.745 | 67.3 | 0 | 0.00 |
| cheapest known, no threshold | 0.297 | 7.3 | 0 | 0.00 |
| threshold 0.18 | 0.209 | 5.1 | 0 | 0.00 |
| threshold 0.20 | 0.206 | 5.1 | 0 | 0.00 |
| threshold 0.25 | 0.268 | 6.6 | 0 | 0.00 |
| threshold 0.30 | 0.293 | 7.2 | 0 | 0.00 |
| profile forecast, margin 0.00 | 0.154 | 3.8 | 0 | 0.00 |
| profile forecast, margin 0.02 | 0.197 | 4.8 | 0 | 0.00 |
| weather forecast, margin 0.00 | 0.074 | 1.8 | 0 | 0.00 |
| weather forecast, margin 0.02 | 0.092 | 2.3 | 0 | 0.00 |
| weather forecast, margin 0.04 | 0.144 | 3.5 | 0 | 0.00 |

## Extra cost per session by scenario (EUR)

| Strategy | evening to next morning | morning to next morning | evening to second morning | evening to second late morning | evening to third morning | evening to fourth morning | evening to fifth morning |
| --- | --: | --: | --: | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| charge immediately | 1.458 | 1.192 | 2.930 | 3.000 | 3.201 | 3.614 | 3.825 |
| cheapest known, no threshold | 0.000 | 0.053 | 0.046 | 0.094 | 0.379 | 0.650 | 0.861 |
| threshold 0.18 | 0.000 | 0.116 | 0.150 | 0.127 | 0.255 | 0.356 | 0.460 |
| threshold 0.20 | 0.000 | 0.076 | 0.103 | 0.099 | 0.240 | 0.395 | 0.532 |
| threshold 0.25 | 0.000 | 0.047 | 0.052 | 0.085 | 0.333 | 0.578 | 0.782 |
| threshold 0.30 | 0.000 | 0.053 | 0.048 | 0.092 | 0.371 | 0.637 | 0.849 |
| profile forecast, margin 0.00 | 0.000 | 0.026 | 0.044 | 0.069 | 0.196 | 0.320 | 0.422 |
| profile forecast, margin 0.02 | 0.000 | 0.035 | 0.042 | 0.077 | 0.247 | 0.423 | 0.558 |
| weather forecast, margin 0.00 | 0.000 | 0.022 | 0.033 | 0.041 | 0.094 | 0.141 | 0.188 |
| weather forecast, margin 0.02 | 0.000 | 0.024 | 0.024 | 0.044 | 0.122 | 0.187 | 0.245 |
| weather forecast, margin 0.04 | 0.000 | 0.034 | 0.032 | 0.064 | 0.190 | 0.297 | 0.389 |

## Forecast accuracy (mean absolute error, EUR/kWh)

| Model | Horizon | Error |
| --- | --- | --: |
| profile | 0-24 h past known prices | 0.0332 |
| profile | 24-48 h | 0.0403 |
| weather | 0-24 h past known prices | 0.0254 |
| weather | 24-48 h | 0.0278 |
