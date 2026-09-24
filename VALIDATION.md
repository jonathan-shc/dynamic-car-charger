# Validation

What has been checked for Dynamic Car Charger 0.6.0, and what still needs checking on real hardware.

## Automated checks

Run on every push and pull request by [the Validate workflow](.github/workflows/validate.yml):

| Check | Tool | Result for 0.6.0 |
| --- | --- | --- |
| Unit and integration tests | `pytest` against Home Assistant 2026.9.2, Python 3.14 | 83 passed |
| Lint | `ruff check` (rules ASYNC, B, E, F, I, SIM, UP, W) | Clean |
| Formatting | `ruff format --check` | Clean |
| Python 3.13 syntax | `compileall` on Python 3.13, for older Home Assistant versions | See the workflow run for this release |
| Integration manifest, translations and services | Hassfest | See the workflow run for this release |
| HACS repository structure | HACS action (brands check excluded) | See the workflow run for this release |

The tests run the real coordinator against Home Assistant's state machine and service registry. The charger, lock, battery, power and price sources are simulated entities.

### Covered by tests

- Least-cost allocation, fractional and partial intervals, charging losses, negative prices, equal prices.
- Price gaps, overlapping or conflicting rows, naive timestamps, unit checks, quarter-hour and hourly prices.
- Daylight-saving changes in plans and in the deadline preset buttons.
- Price threshold: provisional plans, full coverage, close-to-deadline safety mode, kept across restarts.
- Pause/resume commands, slow confirmation, retries after failures and after the 5-minute confirmation timeout.
- Stable continuous runs across hourly price boundaries; replanning after price or deadline changes.
- Energy estimate between battery reports; waiting for measured SOC for at most 30 minutes.
- Old battery reports are flagged but do not stop the plan; unavailable inputs pause.
- Deadline grace (`charging_overtime`).
- Charge now to target.
- Wallbox unlock before charging (with retries) and lock when driving.
- Car connected event.
- Session cost, including missing prices.
- Repair issues that appear after 30 minutes and clear themselves.
- `set_session` action.
- Price forecast: holidays, calibration to all-in prices, estimates on synthetic data with a known weather relation, no training on unpublished days, API parsing, fetch schedule and failure handling, switching between forecast and threshold, fallback, and estimated hours never starting charging.
- Configuration validation, the interval selector with stored numeric values, and the options flow unique ID update.

## Hardware checks still to do

Not verified by this project. Tick these off with your own setup and report results in an issue (without credentials or vehicle identifiers).

- [ ] Leapmotor B05 state of charge entity updates while charging, and how often.
- [ ] Leapmotor vehicle state reports `Driving` after the cable is removed.
- [ ] Wallbox Pulsar Max pause/resume switch confirms within 5 minutes through the Wallbox cloud.
- [ ] Wallbox lock and unlock through Home Assistant.
- [ ] Wallbox status sensor reports `Locked, car connected` when plugged in.
- [ ] Wallbox power sensor unit (W or kW) and update rate.
- [ ] Enever NextEnergy prices match the NextEnergy app for a sample day.
- [ ] A complete overnight session reaches the target by the deadline.
- [ ] Session charging cost compared with the NextEnergy invoice for the same session.
- [ ] Behavior after a Home Assistant restart during a charging session.
- [ ] Price forecast: estimates for the next days compared with the prices once published.
- [ ] Price forecast training time on a Raspberry Pi 4 (about 0.4 s on a laptop).
