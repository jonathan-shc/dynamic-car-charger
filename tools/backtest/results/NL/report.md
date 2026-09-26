# Price forecast backtest: NL

Period 2024-06-01 to 2026-09-23, 17736 sessions, charging power 9.9 kW, energies 10.0, 22.5, 40.0 kWh. Prices are NL market prices with the all-in formula of the Dutch NextEnergy contract; next-day prices become known at 15:00 local time.

Average oracle cost per session: EUR 4.08.

## All sessions

| Strategy | Extra cost per session (EUR) | Extra cost (%) | Sessions short | Average shortfall (kWh) |
| --- | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.0 | 0 | 0.00 |
| charge immediately | 2.748 | 67.3 | 0 | 0.00 |
| cheapest known, no threshold | 0.297 | 7.3 | 0 | 0.00 |
| threshold 0.18 | 0.210 | 5.1 | 0 | 0.00 |
| threshold 0.20 | 0.206 | 5.0 | 0 | 0.00 |
| threshold 0.25 | 0.268 | 6.6 | 0 | 0.00 |
| threshold 0.30 | 0.293 | 7.2 | 0 | 0.00 |
| profile forecast, margin 0.00 | 0.154 | 3.8 | 0 | 0.00 |
| profile forecast, margin 0.02 | 0.198 | 4.8 | 0 | 0.00 |
| weather forecast, margin 0.00 | 0.074 | 1.8 | 0 | 0.00 |
| weather forecast, margin 0.02 | 0.092 | 2.3 | 0 | 0.00 |
| weather forecast, margin 0.04 | 0.144 | 3.5 | 0 | 0.00 |

## Extra cost per session by scenario (EUR)

| Strategy | evening to next morning | morning to next morning | evening to second morning | evening to second late morning | evening to third morning | evening to fourth morning | evening to fifth morning |
| --- | --: | --: | --: | --: | --: | --: | --: |
| oracle (all prices known) | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| charge immediately | 1.459 | 1.193 | 2.932 | 3.002 | 3.202 | 3.623 | 3.827 |
| cheapest known, no threshold | 0.000 | 0.053 | 0.046 | 0.094 | 0.378 | 0.649 | 0.862 |
| threshold 0.18 | 0.000 | 0.117 | 0.151 | 0.128 | 0.256 | 0.356 | 0.460 |
| threshold 0.20 | 0.000 | 0.077 | 0.103 | 0.099 | 0.240 | 0.395 | 0.532 |
| threshold 0.25 | 0.000 | 0.047 | 0.052 | 0.085 | 0.332 | 0.578 | 0.782 |
| threshold 0.30 | 0.000 | 0.053 | 0.048 | 0.092 | 0.371 | 0.637 | 0.850 |
| profile forecast, margin 0.00 | 0.000 | 0.026 | 0.044 | 0.069 | 0.196 | 0.320 | 0.424 |
| profile forecast, margin 0.02 | 0.000 | 0.035 | 0.042 | 0.077 | 0.247 | 0.423 | 0.560 |
| weather forecast, margin 0.00 | 0.000 | 0.022 | 0.033 | 0.041 | 0.093 | 0.141 | 0.187 |
| weather forecast, margin 0.02 | 0.000 | 0.024 | 0.024 | 0.044 | 0.122 | 0.187 | 0.245 |
| weather forecast, margin 0.04 | 0.000 | 0.034 | 0.032 | 0.064 | 0.190 | 0.297 | 0.390 |

## Forecast accuracy (mean absolute error, EUR/kWh)

| Model | Horizon | Error |
| --- | --- | --: |
| profile | 0-24 h past known prices | 0.0332 |
| profile | 24-48 h | 0.0403 |
| weather | 0-24 h past known prices | 0.0254 |
| weather | 24-48 h | 0.0278 |
