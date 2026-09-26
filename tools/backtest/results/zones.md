# Price forecast backtest per bidding zone

Extra cost compared with knowing all prices in advance (oracle), over all sessions. Every zone uses the same all-in formula as the Dutch contract, (market + 0.10967) x 1.21, so only the market prices differ. Next-day prices become known at 15:00 local time.

| Zone | Period | Sessions | Oracle cost per session (EUR) | Cheapest known | Threshold 0.20 | Profile forecast | Weather forecast | Weather model error, first day (EUR/kWh) |
| --- | --- | --: | --: | --: | --: | --: | --: | --: |
| NL | 2024-06-01 to 2026-09-23 | 17736 | 4.08 | 7.3% | 5.0% | 3.8% | 1.8% | 0.0254 |
| BE | 2024-06-01 to 2026-09-23 | 17736 | 4.09 | 7.3% | 5.3% | 3.4% | 2.0% | 0.0245 |
| DE-LU | 2024-06-01 to 2026-09-23 | 17736 | 4.14 | 7.2% | 4.8% | 3.6% | 1.8% | 0.0266 |
| FR | 2024-06-01 to 2026-09-23 | 17736 | 3.80 | 5.8% | 3.7% | 2.9% | 1.8% | 0.0240 |
| AT | 2024-06-01 to 2026-09-23 | 17736 | 4.47 | 7.2% | 5.4% | 3.1% | 1.9% | 0.0264 |
| CH | 2024-06-01 to 2026-09-23 | 17736 | 4.78 | 6.4% | 4.8% | 2.3% | 1.8% | 0.0205 |
| PL | 2024-06-01 to 2026-09-23 | 17736 | 4.45 | 7.8% | 5.2% | 3.5% | 2.0% | 0.0284 |
| DK1 | 2024-06-01 to 2026-09-23 | 17736 | 4.17 | 5.7% | 3.6% | 3.6% | 1.4% | 0.0278 |
| DK2 | 2024-06-01 to 2026-09-23 | 17736 | 4.19 | 5.6% | 3.4% | 3.7% | 1.5% | 0.0305 |
| SE3 | 2024-06-01 to 2026-09-23 | 17736 | 3.70 | 3.8% | 2.8% | 3.6% | 1.2% | 0.0233 |
| SE4 | 2024-06-01 to 2026-09-23 | 17736 | 3.82 | 4.6% | 3.1% | 4.1% | 1.6% | 0.0315 |
| FI | 2024-06-01 to 2026-09-23 | 17736 | 3.56 | 3.7% | 2.5% | 4.0% | 1.3% | 0.0305 |
