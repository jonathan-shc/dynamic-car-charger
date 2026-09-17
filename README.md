# Dynamic Car Charger

A Home Assistant custom integration for deadline-based EV charging. Set **80% by Friday at 07:30**, inspect the charging plan, and let the integration pause and resume your Wallbox during the cheapest published price intervals.

Designed for a Leapmotor B05, Wallbox Pulsar Max and a NextEnergy dynamic contract. It connects to **existing Home Assistant entities**; it does not log into the car, charger or energy provider itself. Hardware compatibility must be checked with your actual devices. Initial release: 0.1.0.

## What you get

- UI setup and editable options for the car, charger, battery capacity, power and tariff.
- Target battery percentage and a full date/time deadline, editable on a dashboard.
- A plan showing start/end times, EUR/kWh, grid energy and estimated remaining cost.
- Automatic pause/resume with a 15-second control loop and entity-change updates.
- Replanning as prices, battery telemetry and measured charging power change.
- Negative prices, fractional charging intervals, hourly or quarter-hourly prices and timezone-aware daylight-saving handling.
- Explicit provisional plans, energy shortfalls, stale-input errors and command errors.
- Persisted target, deadline, control setting and estimated energy credit across restarts. Fresh battery/power reports are required before resuming after restart.

## Before installation

Configure these source integrations in Home Assistant first; their entities are selected during setup:

| Input | Source and requirement |
| --- | --- |
| Pause/resume | Official [Wallbox integration](https://www.home-assistant.io/integrations/wallbox/). Choose the charging switch: **on = resume**, **off = pause**. Do not choose a mains power switch or lock. |
| Battery percentage | A live B05 state-of-charge entity in `%`. The unofficial [Leapmotor integration](https://github.com/kerniger/leapmotor-ha) is a possible source, but B05 support has not been verified by this project. Its own authentication requirements apply. |
| Actual charging power | Wallbox power entity, with unit `W` or `kW`. Used to account for energy delivered between battery percentage changes. |
| NextEnergy prices | [Enever Home Assistant integration](https://github.com/MvRens/ha-enever), with NextEnergy enabled. Select its EUR/kWh electricity sensor. [Enever](https://enever.nl/prijzenfeeds/) requires a personal API token. Alternatively use the documented price format below. |

An `input_number` helper in `%` is accepted as a manual battery input. Enter the actual percentage at the start of each session and keep it refreshed; this is an estimated fallback, not verified car telemetry. By default battery reports older than 60 minutes stop scheduling. Power reports older than five minutes also stop scheduling. A source must mark outdated cloud data unavailable: a fresh HA report does not prove the car supplied a fresh reading.

NextEnergy [currently describes hourly prices](https://www.nextenergy.nl/actuele-energieprijzen), based on underlying quarter-hour market prices. Select **60 minutes** unless your actual contract and source both use quarter-hour billing. Enever supplier prices include VAT and levies; do not add them again. The optional price adjustment is only for a known difference between your contract and the feed. Compare a sample day with your NextEnergy app: contract-specific purchase fees may differ.

## Install through HACS

1. In HACS, open **Custom repositories**.
2. Add `https://github.com/jonathan-shc/dynamic-car-charger`, category **Integration**.
3. Download **Dynamic Car Charger** and restart Home Assistant.
4. Open **Settings → Devices & services → Add integration → Dynamic Car Charger**.
5. Select the four source entities and enter your usable battery capacity, expected grid charging power and efficiency.

This is a HACS **custom repository**, not a listing in the default HACS catalog. The repository follows the [HACS integration structure](https://hacs.dev/docs/publish/integration/). A default-catalog submission and Home Assistant brands artwork are separate work.

Manual installation: copy `custom_components/dynamic_car_charger` into `/config/custom_components/`, then restart Home Assistant. Minimum declared Home Assistant version: 2025.3; see validation notes for tested versions.

## Configure your B05 and Pulsar Max

The example capacity and power are placeholders, **not verified B05 specifications**. Set the usable capacity for your exact battery variant and expected charging power to the lower of the car's AC limit and the configured Wallbox supply. A conservative power assumption helps when load balancing reduces the available power. Efficiency defaults to 0.90.

Keep the charger unlocked and connected, with permission to pause/resume. Disable competing Wallbox, car or NextEnergy smart-charging schedules while this integration owns charging. Configure the car's charge limit at or above the requested target. This integration leaves electrical limits, load balancing, charger locks and the car's own protection systems in place and does not change the maximum current.

## Daily use

1. Set **Target charge** (for example 80%).
2. Set **Ready by** to the required date and time in the Home Assistant UI.
3. Inspect **Charging plan** and **Remaining charging cost**.
4. Turn on **Automatic charging** to execute the plan. It starts disabled on first installation.
5. Turning it off requests a pause, retries if necessary, then releases control once off is confirmed. To charge manually, turn it off first and wait for the pause confirmation.

Copy [the dashboard example](examples/dashboard.yaml) into a manual dashboard card. Adjust entity IDs to the ones created in your installation. The example uses built-in cards; no custom frontend package is required.

Changing options reloads the integration and requests a pause. A completed deadline is one-off: choose another date to schedule another session. Multiple chargers are supported as separate entries; each needs its own sensor inputs.

## Planning and execution

Required grid energy is `(target - current %) / 100 × usable capacity / efficiency`. The scheduler allocates it to the cheapest known intervals before the deadline, allowing a partial final interval. Equal prices favor earlier charging. This allocation minimizes modeled energy cost at constant power and efficiency over the **published** intervals. It does not promise the globally cheapest price when future days are unknown, nor does it model solar export, demand charges or variable efficiency.

Between changes in the reported battery percentage, measured Wallbox power is integrated to estimate added battery energy. A changed percentage resets that estimate. Once the estimate reaches the target, charging pauses pending confirmation from the battery sensor. Integer-rounded or stale battery readings and power polling can introduce error. Set a suitable charge limit in the car and check the first real session. The cost sensor reports **remaining planned cost**, not the final bill or a historical total.

Prices beyond the published horizon remain unknown. A provisional plan uses available cheap periods now and is recomputed when new prices arrive; this can charge earlier than a future, as-yet-unpublished cheaper period. Gaps are never filled with invented prices. Insufficient priced time produces a visible shortfall and still schedules all useful known time. Invalid or unavailable inputs request a pause; there is no unpriced emergency-charge override.

The controller checks every 15 seconds and on source state changes. It uses cloud switch feedback and retries unconfirmed commands after 120 seconds; a changed on/off request bypasses this delay. Actual start/stop timing also depends on the Wallbox cloud and vehicle response. The official Wallbox integration polls at roughly 90 seconds for one charger. A requested start is not proof the vehicle is drawing power. Low power is reflected in slower estimated progress and eventual shortfall.

Home Assistant must remain running with working network access. Unloading requests a pause, but an outage, lost connectivity or failed cloud command can leave the charger in its last state. This integration cannot guarantee a deadline or an exact percentage during those failures.

### Plan states

| State | Meaning |
| --- | --- |
| `set_deadline` | Choose a ready-by date and time. |
| `preview` | Plan shown; automatic control is off. `plan_status` holds the underlying result. |
| `scheduled` | Waiting for a planned charging period. |
| `charging` | Charging is requested during a planned period. |
| `provisional_plan` | The plan uses published prices but the deadline extends beyond complete price coverage. |
| `waiting_for_prices` | Known prices cannot yet provide all required energy. |
| `insufficient_time` | Complete price coverage exists but there is not enough modeled charging time. |
| `target_reached` | Measured battery percentage meets the target. |
| `awaiting_soc_confirmation` | Estimated energy reaches the target; waiting for measured SOC. |
| `deadline_passed` | The deadline has expired; charging is paused. |
| `input_error` | A required input is missing, stale, malformed or has wrong units. |
| `control_error` | Command failed or the charger has not confirmed it yet. Inspect `error`. |

### Compatible price sensor format

The sensor unit must be `EUR/kWh` or `€/kWh`. Use either Enever `prices_today` / `prices_tomorrow` lists with `time` and `price`, or a `prices` attribute:

```yaml
prices:
  - start: "2026-09-18T01:00:00+02:00"
    end: "2026-09-18T02:00:00+02:00"
    price: 0.18
  - start: "2026-09-18T02:00:00+02:00"
    end: "2026-09-18T03:00:00+02:00"
    price: -0.02
```

Prices must be all-in import prices per kWh. Timestamps must include an offset. Explicit ends are preferred; missing ends use the configured interval. Overlapping intervals, contradictory duplicates, nonfinite prices and naive timestamps are rejected. A current-price-only sensor is insufficient for planning.

## Development and validation

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-test.txt
ruff check .
pytest -q
```

Tests cover cost allocation, fractional intervals, losses, negative prices, gaps, daylight-saving changes, malformed input, live Home Assistant state/service handling, stop/retry behavior and configuration validation. GitHub Actions runs tests, Hassfest and HACS validation. HACS brands validation is excluded for this custom repository.

See [VALIDATION.md](VALIDATION.md) for the actual results and remaining hardware checks. No credentials, vehicle identifiers or household consumption data belong in issues. Share only the relevant redacted configuration and plan attributes when reporting a problem.
