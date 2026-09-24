# Dynamic Car Charger

A Home Assistant custom integration for deadline-based EV charging. Set **80% by Friday at 07:30**, inspect the charging plan, and let the integration pause and resume your Wallbox during the cheapest published price intervals.

Designed for a Leapmotor B05, Wallbox Pulsar Max and a NextEnergy dynamic contract. It connects to **existing Home Assistant entities**; it does not log into the car, charger or energy provider itself. Hardware compatibility must be checked with your actual devices. Current version: 0.5.0 (see [releases](https://github.com/jonathan-shc/dynamic-car-charger/releases)).

## What you get

- UI setup and editable options for the car, charger, battery capacity, power and tariff.
- Target battery percentage and a full date/time deadline, editable on a dashboard, with one-press deadline presets.
- A plan showing start/end times, EUR/kWh, grid energy and estimated remaining cost.
- Automatic pause/resume with a 15-second control loop and entity-change updates.
- A price threshold that limits provisional plans to cheap periods while there is still enough time.
- **Charge now to target**, ignoring prices, for when you need the car soon.
- Optional Wallbox unlocking before charging and locking when the car drives away.
- A `set_session` action to set target, deadline and mode from automations, scripts or iOS Shortcuts.
- A measured **Session charging cost** from the actual Wallbox power and the price at the time.
- Repair issues in **Settings → System → Repairs** when inputs or the charger stay broken for 30 minutes.
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
| Wallbox status (optional) | The Wallbox status description sensor. When it changes to `Locked, car connected`, the integration fires the car-connected event. Without it, the event fires when the charging switch becomes available. |
| Wallbox lock (optional) | The Wallbox `lock` entity. It is unlocked before each start request and locked when the car starts driving. |
| Vehicle state (optional) | A car state sensor that reports `Driving`. Used to lock the Wallbox after you drive away, and to never unlock it while driving. |

An `input_number` helper in `%` is accepted as a manual battery input. Enter the actual percentage at the start of each session and keep it refreshed; this is an estimated fallback, not verified car telemetry.

A battery percentage that does not change for a long time is normal while the car is parked, so an old battery report **does not stop the plan**. When the last report is older than the configured age (60 minutes by default), the plan sensor sets `soc_report_old: true` so you can warn about it on a dashboard. A battery entity that is `unavailable` or `unknown` does stop scheduling. Power reports older than five minutes are treated as 0 kW: they are not used to estimate added energy, but they do not stop the plan either. A source must mark outdated cloud data unavailable: a fresh HA report does not prove the car supplied a fresh reading.

NextEnergy [currently describes hourly prices](https://www.nextenergy.nl/actuele-energieprijzen), based on underlying quarter-hour market prices. Select **60 minutes** unless your actual contract and source both use quarter-hour billing. Enever supplier prices include VAT and levies; do not add them again. The optional price adjustment is only for a known difference between your contract and the feed. Compare a sample day with your NextEnergy app: contract-specific purchase fees may differ.

## Install through HACS

1. In HACS, open **Custom repositories**.
2. Add `https://github.com/jonathan-shc/dynamic-car-charger`, category **Integration**.
3. Download **Dynamic Car Charger** and restart Home Assistant.
4. Open **Settings → Devices & services → Add integration → Dynamic Car Charger**.
5. Select the four required source entities, and optionally the Wallbox status, lock and vehicle state entities. Enter your usable battery capacity, expected grid charging power and efficiency.

This is a HACS **custom repository**, not a listing in the default HACS catalog. The repository follows the [HACS integration structure](https://hacs.dev/docs/publish/integration/). A default-catalog submission and Home Assistant brands artwork are separate work.

Manual installation: copy `custom_components/dynamic_car_charger` into `/config/custom_components/`, then restart Home Assistant. Minimum declared Home Assistant version: 2025.3; see [VALIDATION.md](VALIDATION.md) for the tested version.

## Configure your B05 and Pulsar Max

The example capacity and power are placeholders, **not verified B05 specifications**. Set the usable capacity for your exact battery variant and expected charging power to the lower of the car's AC limit and the configured Wallbox supply. A conservative power assumption helps when load balancing reduces the available power. Efficiency defaults to 0.90.

Keep the charger connected, with permission to pause/resume. Keep it unlocked, or select the Wallbox lock entity so the integration unlocks it before charging. Disable competing Wallbox, car or NextEnergy smart-charging schedules while this integration owns charging. Configure the car's charge limit at or above the requested target. This integration leaves electrical limits, load balancing, charger locks and the car's own protection systems in place and does not change the maximum current.

## Daily use

1. Set **Target charge** (for example 80%).
2. Set **Ready by** to the required date and time, or press one of the presets: **Tomorrow 07:00**, **Tomorrow 09:00** or **Day after tomorrow 09:00** (local time, also across daylight-saving changes).
3. Inspect **Charging plan** and **Remaining charging cost**.
4. Turn on **Automatic charging** to execute the plan. It starts disabled on first installation.
5. Turning it off requests a pause, retries if necessary, then releases control once off is confirmed. To charge manually, turn it off first and wait for the pause confirmation.

**Charge now to target** charges immediately until the measured battery percentage reaches the target, whatever the price. It works without automatic charging and turns itself off at the target. Turning it off requests a pause, unless automatic charging is on.

Copy [the dashboard example](examples/dashboard.yaml) into a manual dashboard card. Adjust entity IDs to the ones created in your installation. The example uses built-in cards; no custom frontend package is required.

Changing options reloads the integration and requests a pause. A completed deadline is one-off: choose another date to schedule another session. Multiple chargers are supported as separate entries; each needs its own sensor inputs.

### Price threshold

**Charging price threshold** (EUR/kWh, default 0.20) limits the plan to periods at or below that price while future prices are not yet published. This avoids charging at a mediocre price now when a cheaper night may still be announced. The threshold is ignored when:

- prices are published all the way to the deadline: then the cheapest known periods are always used, or
- the deadline is close: within 1.5 × the time needed to charge (at least one hour), the plan may use any known price to finish in time.

A change on the dashboard is kept across restarts. Changing the threshold in the integration options replaces the dashboard value.

### Set a session from an automation

The `dynamic_car_charger.set_session` action changes only the fields you provide:

```yaml
action: dynamic_car_charger.set_session
data:
  target_percentage: 80
  ready_by: "2026-09-25 07:30:00"
  automatic_charging: true
```

| Field | Meaning |
| --- | --- |
| `config_entry_id` | Which charger. Can be left out when only one is set up. |
| `target_percentage` | Target battery percentage. |
| `ready_by` | Deadline. A time without offset uses the Home Assistant time zone. |
| `automatic_charging` | Turn automatic charging on or off. |
| `charge_now` | Turn **Charge now to target** on or off. |

### Car connected event

When the car is plugged in, the integration fires `dynamic_car_charger_car_connected` with `config_entry_id`, `charger_entity`, `status_entity` and `status`. With a Wallbox status entity it fires when the status changes to `Locked, car connected`; otherwise when the charging switch becomes available. [This example](examples/car_connected_notification.yaml) sends an actionable phone notification. [This one](examples/iphone_charging_live_activity.yaml) shows progress as an iPhone Live Activity.

### Session charging cost

**Session charging cost** adds up the measured Wallbox power × the price of the interval at that moment. A session starts at the first charging request and ends when the target is reached, the deadline passes, or control is turned off. After a session ends, the sensor keeps showing the last session until the next one starts. Attributes: `active`, `started`, `ended`, `energy_kwh`, `average_price_eur_kwh` and `cost_complete` (false when a price was unknown for part of the energy). This is grid energy at the feed price, not your supplier bill.

## Planning and execution

Required grid energy is `(target - current %) / 100 × usable capacity / efficiency`. The scheduler allocates it to the cheapest known intervals before the deadline, allowing a partial final interval. Equal prices favor earlier charging. This allocation minimizes modeled energy cost at constant power and efficiency over the **published** intervals. It does not promise the globally cheapest price when future days are unknown, nor does it model solar export, demand charges or variable efficiency.

Between changes in the reported battery percentage, measured Wallbox power is integrated to estimate added battery energy. A changed percentage resets that estimate. Once the estimate reaches the target, charging continues until the car reports the target percentage, for **at most 30 minutes** (`soc_confirmation_until`). This covers a sensor that rounds down, without charging outside the plan until the deadline when the car's own limit is below the target. Integer-rounded or stale battery readings and power polling can introduce error. Set a suitable charge limit in the car and check the first real session. The cost sensor reports **remaining planned cost**, not the final bill or a historical total.

Prices beyond the published horizon remain unknown. A provisional plan uses available cheap periods now and is recomputed when new prices arrive; this can charge earlier than a future, as-yet-unpublished cheaper period. Gaps are never filled with invented prices. Insufficient priced time produces a visible shortfall and still schedules all useful known time. Invalid or unavailable inputs request a pause; there is no unpriced emergency-charge override.

The controller checks every 15 seconds and on source state changes. It uses cloud switch feedback and retries unconfirmed commands every 120 seconds; a changed on/off request bypasses this delay. A command that is still not confirmed after 5 minutes is shown as `control_error`, but it keeps being retried, so the charger recovers on its own once it responds. Actual start/stop timing also depends on the Wallbox cloud and vehicle response. The official Wallbox integration polls at roughly 90 seconds for one charger. A requested start is not proof the vehicle is drawing power. Low power is reflected in slower estimated progress and eventual shortfall.

If a started session has not reached the measured target at the deadline, charging continues for at most the configured **deadline grace** (default 60 minutes, `charging_overtime`). This never starts a new session after the deadline and stops when the car starts driving. Set it to 0 to stop exactly at the deadline.

Home Assistant must remain running with working network access. Unloading requests a pause, but an outage, lost connectivity or failed cloud command can leave the charger in its last state. This integration cannot guarantee a deadline or an exact percentage during those failures.

When automatic charging or charge now is on and an `input_error` or `control_error` lasts 30 minutes, a repair issue appears in **Settings → System → Repairs**. It clears itself once the problem is gone.

### Plan states

The **Charging plan** sensor state is one of:

| State | Meaning |
| --- | --- |
| `set_deadline` | Choose a ready-by date and time. |
| `preview` | Plan shown; automatic control is off. `plan_status` holds the underlying result. |
| `scheduled` | Waiting for a planned charging period. |
| `charging` | Charging is requested during a planned period, or by **Charge now to target**. |
| `charging_overtime` | The deadline passed but the started session continues within the deadline grace. |
| `provisional_plan` | The plan uses published prices but the deadline extends beyond complete price coverage. |
| `waiting_for_prices` | Known prices cannot yet provide all required energy. |
| `insufficient_time` | Complete price coverage exists but there is not enough modeled charging time. |
| `target_reached` | Measured battery percentage meets the target. |
| `awaiting_soc_confirmation` | Estimated energy reaches the target; waiting (at most 30 minutes of charging) for measured SOC. |
| `deadline_passed` | The deadline has expired; charging is paused. |
| `waiting_for_car` | The charger is unavailable, or the car is driving; charging cannot start yet. |
| `unlocking_charger` | The Wallbox unlock was requested and is not yet confirmed. |
| `starting_charge` | Resume was requested and is not yet confirmed. |
| `stopping_charge` | Pause was requested and is not yet confirmed. |
| `input_error` | A required input is missing, unavailable, malformed or has wrong units. Inspect `error`. |
| `control_error` | A command failed or has not been confirmed for 5 minutes. Retries continue. Inspect `error`. |

`plan_status` additionally uses `immediate_charging` while **Charge now to target** is active.

Useful attributes: `slots`, `estimated_cost_eur`, `required_grid_kwh`, `planned_grid_kwh`, `shortfall_kwh`, `coverage_complete`, `measured_soc`, `estimated_soc`, `soc_reported_at`, `soc_report_old`, `charging_requested`, `price_threshold_eur_kwh`, `threshold_safety_mode`, `deadline_extension_until` and `error`. To keep the history database small, `slots`, `estimated_soc` and `active_charge_until` are available on the live state but are not recorded in history.

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
python3.14 -m venv .venv
. .venv/bin/activate
pip install -r requirements-test.txt
pre-commit install  # optional: runs ruff on every commit
ruff check .
ruff format --check .
pytest -q
```

Tests cover cost allocation, fractional intervals, losses, negative prices, gaps, daylight-saving changes, malformed input, live Home Assistant state/service handling, stop/retry behavior, lock handling, the price threshold, session cost, repair issues, the `set_session` action and configuration validation. GitHub Actions runs tests, Hassfest and HACS validation. HACS brands validation is excluded for this custom repository.

### Releasing

1. Update `version` in `custom_components/dynamic_car_charger/manifest.json` and in this README.
2. Merge to `main`, then tag: `git tag v0.5.0 && git push origin v0.5.0`.
3. The release workflow checks that the tag matches the manifest version and publishes a GitHub release with generated notes. HACS offers that release to users.

See [VALIDATION.md](VALIDATION.md) for the actual results and remaining hardware checks. No credentials, vehicle identifiers or household consumption data belong in issues. Share only the relevant redacted configuration and plan attributes when reporting a problem.
