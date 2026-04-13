# Multi-Zone Kiln Control — Design Spec

**Date:** 2026-04-13
**Branch:** `feature/multi-zone` (to be created from `main`)
**Status:** Reviewed

## Overview

Add configurable multi-zone support to the kiln controller. Each zone has its own thermocouple, PID controller, and relay output. All zones follow a single shared firing profile. The system remains fully backward-compatible with single-zone configurations.

## Decisions

| Decision | Choice |
|----------|--------|
| Zone count | Configurable N-zone via `config.zones` list |
| Profile strategy | Same profile for all zones, per-zone temp offset |
| Thermocouple failure | Per-zone `critical` flag; critical failure = emergency stop, advisory = log & continue |
| Graph layout | Overlaid traces on one chart + zone summary panel |
| SPI wiring | Shared SPI bus, per-zone chip-select (CS) pin |
| MQTT structure | Flat per-zone topics + full JSON blob |
| Schedule control temp | Configurable strategy (`coldest`/`hottest`/`average`/index), default `coldest` |
| PID control | Independent PID loop per zone |
| Architecture | Zone object composition (Approach 1) |

## 1. Config Schema

### Zone List

Each zone is a dict in a `zones` list in `config.py`. The shared SPI bus pins (`spi_sclk`, `spi_miso`, `spi_mosi`) remain top-level scalars.

```python
zones = [
    {
        "name": "Top",
        "spi_cs": board.D22,
        "gpio_heat": board.D23,
        "gpio_heat_invert": False,
        "pid_kp": 9.84,
        "pid_ki": 7,
        "pid_kd": 171,
        "thermocouple_offset": 0,
        "temp_offset": 0,          # offset from profile target (degrees)
        "critical": True,          # TC failure = emergency stop
    },
    {
        "name": "Bottom",
        "spi_cs": board.D24,
        "gpio_heat": board.D25,
        "gpio_heat_invert": False,
        "pid_kp": 9.84,
        "pid_ki": 7,
        "pid_kd": 171,
        "thermocouple_offset": 0,
        "temp_offset": 0,
        "critical": True,
    },
]

zone_control_strategy = "coldest"  # "coldest" | "hottest" | "average" | int (zone index)
# Example: zone_control_strategy = 1  # always use zone index 1 as the control zone
```

### Backward Compatibility

If `zones` is undefined or empty, the system auto-synthesizes a single zone from legacy scalar config values (`spi_cs`, `gpio_heat`, `pid_kp`, etc.). No migration required.

```python
def get_zone_configs():
    if hasattr(config, 'zones') and config.zones:
        return config.zones
    return [{
        "name": "Kiln",
        "spi_cs": config.spi_cs,
        "gpio_heat": config.gpio_heat,
        "gpio_heat_invert": config.gpio_heat_invert,
        "pid_kp": config.pid_kp,
        "pid_ki": config.pid_ki,
        "pid_kd": config.pid_kd,
        "thermocouple_offset": config.thermocouple_offset,
        "temp_offset": 0,
        "critical": True,
    }]
```

## 2. Zone Class & Board Refactor

### Zone Class

New class in `lib/oven.py` that encapsulates a single heating zone:

```python
class Zone:
    def __init__(self, index, zone_config, temp_sensor, output):
        self.index = index
        self.name = zone_config["name"]
        self.temp_offset = zone_config.get("temp_offset", 0)
        self.critical = zone_config.get("critical", True)
        self.thermocouple_offset = zone_config.get("thermocouple_offset", 0)

        self.temp_sensor = temp_sensor
        self.output = output
        self.pid = PID(
            kp=zone_config.get("pid_kp", config.pid_kp),
            ki=zone_config.get("pid_ki", config.pid_ki),
            kd=zone_config.get("pid_kd", config.pid_kd),
        )
        self.temperature = 0
        self.target = 0
        self.heat = 0
        self.heat_rate = 0
        self.heat_rate_temps = []

        # Per-zone safety tracking (mirrors former Oven-level singletons)
        self.stall_start_time = None
        self.stall_start_temp = None
        self.runaway_start_time = None
        self.runaway_start_temp = None
```

### Board Changes

- `RealBoard` creates a **single SPI bus object** once in `__init__()` and passes it as a constructor argument to each zone's `TempSensorReal`. This avoids conflicts — the Pi's hardware SPI peripheral can only have one active `board.SPI()` at a time, and even software SPI should share one `bitbangio.SPI` instance.
- `RealBoard` also creates a **shared `threading.Lock`** (`self.spi_lock`) and injects it into each `TempSensorReal`. Every SPI transaction (in `raw_temp()`) must acquire this lock. This is required because each `TempSensorReal` runs in its own daemon thread — without serialization, concurrent SPI transactions will corrupt reads.
- `SimulatedBoard` creates N `TempSensorSimulated` instances with independent thermal state. No SPI lock needed.
- Both board types expose `self.zones: list[Zone]` instead of `self.temp_sensor`.

### Output Changes

- `Output` takes a GPIO pin and invert flag as constructor args (currently reads from config globals).
- `SimulatedOutput` — lightweight no-op class that stores heat state without GPIO or sleeping. See Section 5.

### Oven Control Loop

The existing `heat_then_cool()` method is refactored to iterate over zones. Each zone gets its own PID compute and duty-cycle actuation:

```
heat_then_cool():
    n = len(self.zones)
    for zone in self.zones:
        zone_time_step = self.time_step / n     # divide cycle among zones
        heat_on = zone.heat * zone_time_step    # duty cycle on-time
        heat_off = zone_time_step - heat_on     # duty cycle off-time
        zone.output.heat(heat_on)
        zone.output.cool(heat_off)
```

Note: Each zone's duty cycle is `time_step / N` so that the total wall time for all zones stays within one `time_step`. With 2 zones and `time_step = 2s`, each zone gets a 1s duty cycle window. With 3 zones, each gets ~0.67s. This keeps the control loop period constant regardless of zone count. For typical 2-3 zone kilns this is fine — the relay switching frequency is still well within SSR capabilities.

Cooling-segment overrides (`is_natural_cool`, `is_cooling_segment`) apply using the shared `target_heat_rate` since all zones follow one profile — when the profile says "cool", all zones cool.

```
each cycle:
    for zone in self.zones:
        zone.temperature = zone.temp_sensor.temperature() + zone.thermocouple_offset

    control_temp = self.get_control_temperature()  # applies strategy
    self.target = profile.get_target(...)           # shared target

    for zone in self.zones:
        zone.target = self.target + zone.temp_offset
        zone.heat = zone.pid.compute(zone.target, zone.temperature, now)

    self.heat_then_cool()  # iterates zones internally
```

### Control Temperature Strategy

The strategy only considers **critical** zones. Advisory zones are monitoring-only and do not influence schedule progression — they exist to observe temperature in non-controlled regions of the kiln without affecting when segments advance or holds begin.

```python
def get_control_temperature(self):
    valid_temps = [z.temperature for z in self.zones
                   if z.critical and z.temperature is not None]

    if not valid_temps:
        self._emergency_shutdown("All critical zone sensors failed")
        return 0

    strategy = getattr(config, 'zone_control_strategy', 'coldest')

    if isinstance(strategy, int):
        if 0 <= strategy < len(self.zones):
            zone = self.zones[strategy]
            if zone.temp_sensor.status.over_error_limit():
                log.error("zone_control_strategy zone %d ('%s') has TC errors, falling back to coldest" %
                          (strategy, zone.name))
                return min(valid_temps)
            return zone.temperature
        log.error("zone_control_strategy index %d out of range, falling back to coldest" % strategy)
        return min(valid_temps)
    elif strategy == "coldest":
        return min(valid_temps)
    elif strategy == "hottest":
        return max(valid_temps)
    elif strategy == "average":
        return sum(valid_temps) / len(valid_temps)
    else:
        return min(valid_temps)  # fallback
```

### Replacing `self.board.temp_sensor` References

The current codebase references `self.board.temp_sensor.temperature()` in many places throughout `lib/oven.py` (in `run_profile()`, `get_state()`, `reset_if_emergency()`, seek-start logic, etc.). **All** of these must be replaced:

- **Temperature reads for the control loop** → `self.get_control_temperature()` (uses the strategy)
- **Temperature reads for display/state** → `self.get_control_temperature()` (top-level `temperature` field)
- **Seek-start** → use `self.get_control_temperature()` to determine starting position
- **Per-zone temperatures** are accessed via `zone.temperature` in the zones loop

A grep for `self.board.temp_sensor` across `lib/oven.py` must yield zero results after the refactor. This is a mechanical find-and-replace with behavioral review at each site.

### Per-Zone Safety Checks

The existing `reset_if_emergency()` method checks a single thermocouple tracker and runs stall/runaway detection. This must be refactored to iterate all zones:

**Thermocouple error detection:**
```python
for zone in self.zones:
    if zone.temp_sensor.status.over_error_limit():
        if zone.critical:
            self._emergency_shutdown("Critical zone '%s' thermocouple error limit exceeded" % zone.name)
            return
        else:
            log.warning("Advisory zone '%s' thermocouple errors over limit — continuing" % zone.name)
```

**Stall detection (per-zone):**
Any single zone with heater >95% duty cycle for `stall_detect_time` seconds without `stall_min_temp_rise` temperature increase triggers emergency stop. Each zone tracks its own `stall_start_time` and `stall_start_temp`.

**Runaway detection (per-zone):**
Any single zone with heater <5% duty cycle for `runaway_detect_time` seconds but temperature rising by more than `runaway_min_temp_rise` triggers emergency stop. Each zone tracks its own `runaway_start_time` and `runaway_start_temp`.

Both checks use `zone.pid.pidstats['out']` and `zone.temperature` instead of the former singleton values.

### Zone Temperature During TC Failure

When a `TempSensorReal` experiences a thermocouple error, its `temperature()` method returns the last valid median from `TempTracker`. The value is never `None` — it holds the stale cached value. The `ThermocoupleTracker.over_error_limit()` mechanism is the failure signal, not a `None` temperature. The `get_control_temperature()` guard against `None` is defensive — the primary failure path is the per-zone TC error check in `reset_if_emergency()`.

## 3. State Broadcasting & Frontend

### get_state() Output

The state dict adds a `zones` array. Top-level fields remain for backward compatibility:

```python
{
    "state": "RUNNING",
    "temperature": 2145,          # control temperature (per strategy)
    "target": 2167,               # shared profile target
    "heat": 0.72,                 # average heat across zones
    # ... all existing fields ...

    "zones": [
        {
            "index": 0,
            "name": "Top",
            "temperature": 2156,
            "target": 2167,
            "heat": 0.72,
            "heat_rate": 95.2,
            "deviation": -11,
            "critical": True,
            "pidstats": {...}
        },
        ...
    ],
    "zone_spread": 17,            # max(temps) - min(temps)
    "zone_max_deviation": -28,    # worst deviation from target
    "zone_control_strategy": "coldest",
    "control_zone_index": 1       # which zone is driving the schedule
}
```

### Frontend Changes (picoreflow.js)

- **Graph:** Flot plots one series per zone with distinct colors. Target/profile line unchanged. Legend shows zone names.
- **Zone summary panel:** New panel beside the graph. Each zone gets a card: current temp, target, deviation, heat %, colored border matching graph line.
- **Single-zone fallback:** If `zones` is absent or length 1, UI renders identically to current single-zone view. No panel, single trace.

### OvenWatcher

Minimal changes. Already calls `self.oven.get_state()` and broadcasts — the richer dict flows through automatically. `last_log` entries will contain zone data for firing log replay.

The `record()` method's adjusted profile curve continues to use the control temperature (`first_state['temperature']`) as the anchor point, since that is the temperature that drives schedule progression. This is consistent — the profile line on the graph represents what the schedule is tracking.

### Cost Calculation

The existing `update_cost()` uses `self.pid.pidstats['out']` (single duty cycle) and `config.kw_elements`. With multi-zone, `config.kw_elements` remains a top-level scalar representing **total kiln wattage** across all elements (unchanged from current config). `update_cost()` computes the average duty cycle across all zones:

```python
avg_heat = sum(z.heat for z in self.zones) / len(self.zones)
cost_increment = avg_heat * config.kw_elements * config.kwh_rate * time_delta / 3600
```

This is an approximation (assumes equal wattage per zone). Per-zone wattage config is out of scope — it can be added later as an optional `kw_elements` key in the zone dict.

### Divergence Tracking

The existing `track_divergence()` and `calculate_avg_divergence()` track `abs(target - temp)` for the firing log summary. With multi-zone, `divergence_samples` tracks the worst-case deviation across all zones:

```python
max_deviation = max(abs(z.target - z.temperature) for z in self.zones)
self.divergence_samples.append(max_deviation)
```

This preserves the firing log's meaning of "how far was the kiln from its target" while reflecting the worst-performing zone.

### Resume State

The `save_resume_state()` method is extended to persist per-zone temperatures:

```python
{
    "profile_name": "...",
    "current_segment": 2,
    "segment_phase": "ramp",
    "temperature": 1850,           # control temperature
    "zone_temperatures": [1860, 1845, 1838],  # per-zone temps at abort
    ...
}
```

On resume, `resume_last_firing()` uses `zone_temperatures` (if present) to determine per-zone seek positions. If `zone_temperatures` is absent (old state file), the system falls back to the single `temperature` field and applies it to all zones — same as current single-zone behavior.

## 4. MQTT Multi-Zone Publishing

### Existing Topics (Unchanged)

| Topic | Value | Retained |
|-------|-------|----------|
| `kiln/status` | Full JSON blob (now includes `zones`) | No |
| `kiln/state` | RUNNING/IDLE/PAUSED | Yes |
| `kiln/temperature` | Control temperature | Yes |
| `kiln/target` | Profile target | Yes |
| `kiln/heat` | Average heat | Yes |

### New Per-Zone Topics

| Topic Pattern | Value | Retained |
|---------------|-------|----------|
| `kiln/zone/{i}/temperature` | Zone temp | Yes |
| `kiln/zone/{i}/target` | Zone target (incl. offset) | Yes |
| `kiln/zone/{i}/heat` | Zone heat output 0-1 | Yes |
| `kiln/zone/{i}/name` | Zone name string | Yes (on connect) |

### New Aggregate Topics

| Topic | Value | Retained |
|-------|-------|----------|
| `kiln/zone_spread` | max - min across zones | Yes |
| `kiln/zone_max_deviation` | Worst deviation from target | Yes |

### Implementation

Extend `MQTTClient.publish_state()` to loop over `state["zones"]` and publish per-zone subtopics. The existing `_last_values` change-only logic extends naturally, keyed as `zone/0/temperature` etc.

Zone name topics are published once in `_on_connect()` after the `available = "online"` announcement, not in `publish_state()`. This ensures names survive broker restarts via retained messages without redundant per-cycle publishes.

## 5. Simulation Mode

### Per-Zone Thermal Models

Each simulated zone gets its own thermal model using the existing sim params as defaults, with slight randomized variation (5-10% on heating power and thermal resistance). This exercises multi-zone code paths with realistic zone-to-zone temperature differences.

### SimulatedOutput

Lightweight no-op class that stores heat state without GPIO and **without sleeping**. In `SimulatedOven`, the thermal model is driven by `heating_energy(pid)` and `temp_changes()` which receive the PID value directly — the duty-cycle sleep that `Output.heat()` does in `RealOven` is not needed and would break `sim_speedup_factor`.

```python
class SimulatedOutput:
    def __init__(self):
        self.active = False

    def heat(self, sleepfor):
        self.active = True
        # No sleep — SimulatedOven thermal math handles timing

    def cool(self, sleepfor):
        self.active = False
        # No sleep
```

### set_sim_temp API

Extended to accept optional zone index:
- `set_sim_temp 1500` — sets all zones to 1500
- `set_sim_temp 1500 0` — sets zone 0 only

### Development Workflow

Run multi-zone simulation on Mac with `sim_speedup_factor = 100`. Define 2-3 zones in config.py (with `spi_cs`/`gpio_heat` set to `None` in simulation). All zones track independently on the graph.

## 6. Backward Compatibility

| Concern | Handling |
|---------|----------|
| No `zones` in config | Auto-synthesize single zone from legacy scalars |
| Frontend single-zone | No zone panel, single trace, identical to today |
| Existing firing logs | No `zones` key — gallery renders as single-zone |
| New firing logs | Include zone data — gallery shows per-zone history |
| state.json / resume_state.json | Extended with per-zone state; old files without zone data resume as single-zone |
| Profile format | Unchanged — profiles are zone-agnostic |
| WebSocket protocol | Additive fields only |
| API endpoints | Additive params only |
| MQTT | Legacy topics unchanged; per-zone topics are additive |

## Files Affected

| File | Change |
|------|--------|
| `config.py` | Add `zones` list, `zone_control_strategy` |
| `lib/oven.py` | Add `Zone`, `SimulatedOutput`, `get_zone_configs()`, `get_control_temperature()`. Refactor `Oven`, `RealOven`, `SimulatedOven`, `Board`, `RealBoard`, `SimulatedBoard`, `Output` to support N zones |
| `lib/ovenWatcher.py` | Minimal — state dict flows through |
| `lib/mqtt.py` | Extend `publish_state()` for per-zone topics |
| `kiln-controller.py` | Extend `set_sim_temp` API, pass zone data through WebSocket |
| `public/assets/js/picoreflow.js` | Multi-trace Flot graph, zone summary panel, zone-aware crosshair |
| `public/assets/css/components.css` | Zone panel styling |
| `public/assets/css/responsive.css` | Zone panel responsive layout |
| `public/index.html` | Zone panel HTML container |
| `Test/test_Profile.py` | May need updates if Oven instantiation changes |

## Out of Scope (Future Work)

- Independent profiles per zone
- Zone-specific firing logs / history
- `zone_spread_warning` config threshold with auto-abort
- Home Assistant MQTT auto-discovery config
- Per-zone sim param overrides in config
- Web UI for editing zone config (zones are config.py only)
