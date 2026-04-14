# Multi-Zone PID Control

The kiln controller supports independent PID control for kilns with multiple heating zones. Each zone gets its own thermocouple, relay output, and individually tuned PID parameters while all zones follow a single shared firing profile.

## Configuration

Define zones in `config.py`:

```python
zones = [
    {
        "name": "Top",
        "spi_cs": board.D22,              # Chip Select pin for thermocouple
        "gpio_heat": board.D23,            # GPIO pin for SSR relay
        "gpio_heat_invert": False,
        "pid_kp": 9.84,                    # Zone-specific PID tuning
        "pid_ki": 7,
        "pid_kd": 171,
        "thermocouple_offset": 0,          # Calibration offset (degrees)
        "temp_offset": 0,                  # Offset applied to profile target
        "critical": True,                  # Emergency stop on TC failure
    },
    {
        "name": "Bottom",
        "spi_cs": board.D24,
        "gpio_heat": board.D25,
        "gpio_heat_invert": False,
        "pid_kp": 10.2,
        "pid_ki": 6.5,
        "pid_kd": 180,
        "thermocouple_offset": 0,
        "temp_offset": 0,
        "critical": True,
    },
]

# Which zone(s) drive schedule progression
zone_control_strategy = "coldest"
```

### Backward Compatibility

If `zones` is empty or not defined, the system automatically creates a single zone from the legacy scalar config values (`spi_cs`, `gpio_heat`, `pid_kp`, etc.). No migration is needed for single-zone setups.

## Zone Fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Display name shown in UI and MQTT topics |
| `spi_cs` | pin | Chip-select pin for this zone's thermocouple board |
| `gpio_heat` | pin | GPIO pin controlling this zone's SSR |
| `gpio_heat_invert` | bool | Invert relay logic (default: False) |
| `pid_kp` | float | Proportional gain for this zone |
| `pid_ki` | float | Integral gain for this zone |
| `pid_kd` | float | Derivative gain for this zone |
| `thermocouple_offset` | float | Calibration offset added to raw TC reading |
| `temp_offset` | float | Offset applied to the profile target for this zone |
| `critical` | bool | If True, TC failure triggers emergency stop. If False, zone is advisory only. |

## Control Strategy

The `zone_control_strategy` setting determines which zone's temperature drives profile progression (segment transitions, hold timing, etc.). Only **critical** zones participate.

| Strategy | Behavior |
|----------|----------|
| `"coldest"` | Use the minimum temperature across critical zones. The profile won't advance until the coldest zone catches up. This is the safest default. |
| `"hottest"` | Use the maximum temperature. Profile advances as soon as the fastest zone reaches target. |
| `"average"` | Use the mean temperature of critical zones. Good middle ground for well-matched zones. |
| `0`, `1`, etc. | Use a specific zone index. Useful when one zone is the reference point. |

### Advisory Zones

Zones with `critical: False` are monitored and displayed but never drive schedule progression. A thermocouple failure on an advisory zone logs a warning instead of triggering an emergency stop. Use this for monitoring zones where you have no heating element control.

## Hardware Setup

Each zone requires:

1. **Thermocouple + breakout board** (MAX31855 or MAX31856) with its own chip-select pin
2. **Solid state relay (SSR)** controlled by a dedicated GPIO pin
3. **Heating elements** wired through the SSR

All zones share the same SPI bus (MISO, MOSI, CLK). Only the chip-select (CS) pin differs per zone. The controller serializes SPI reads using a shared lock, so there are no bus conflicts.

### Example: 2-Zone Wiring

```
Raspberry Pi GPIO:
  D22 (CS)  --> MAX31856 #1 (Top zone thermocouple)
  D24 (CS)  --> MAX31856 #2 (Bottom zone thermocouple)
  D23       --> SSR #1 (Top zone heating elements)
  D25       --> SSR #2 (Bottom zone heating elements)

Shared SPI:
  MISO, MOSI, CLK --> Both MAX31856 boards
```

## How It Works

### Independent PID Loops

Each zone runs its own PID controller independently:

1. Zone reads its thermocouple temperature
2. Zone calculates its target: `profile_target + zone.temp_offset`
3. Zone's PID computes duty cycle based on its own Kp/Ki/Kd
4. Zone actuates its SSR for the computed on/off time

### Time-Sliced Heat Actuation

The total duty cycle period is divided among zones:

```
zone_time_step = sensor_time_wait / number_of_zones
```

For a 2-second cycle with 2 zones, each zone gets a 1-second window. Within that window, the zone's SSR turns on for `heat * zone_time_step` seconds, then off for the remainder.

### Shared Profile

All zones follow the same firing profile (segments with rates, targets, holds). The control temperature — determined by `zone_control_strategy` — drives segment transitions. Individual zones may be hotter or cooler than the control temperature, but they all target the same profile setpoint (adjusted by `temp_offset`).

## Safety Features

Each zone is independently monitored for:

### Stall Detection
If a zone's heater runs at >95% duty for a configurable period (default 1800 seconds) without the temperature rising by at least `stall_min_temp_rise` degrees, the system triggers an emergency stop. This detects failed heating elements. Stall detection is paused during hold phases where the kiln is at target temperature.

### Runaway / Stuck Relay Detection
If a zone's heater is at <5% duty but temperature keeps rising (by more than `runaway_min_temp_rise` degrees over `runaway_detect_time` seconds), the system triggers an emergency stop. This detects a stuck-on SSR. Detection is paused during cooling segments where thermal redistribution is normal.

### Thermocouple Failure
- **Critical zone**: Immediate emergency stop
- **Advisory zone**: Warning logged, firing continues

### Over-Temperature
Uses the maximum temperature across **all** zones (critical and advisory). If any zone exceeds the safety limit, all heaters shut off.

## UI Display

When multiple zones are configured, the dashboard shows:

- **Zone Summary Panel**: A card per zone showing name, current temperature, target, heat duty %, and deviation from target
- **Graph Traces**: Each zone gets a distinct colored trace on the firing curve
- **Zone Spread**: The difference between the hottest and coldest zone temperatures

Zone colors follow this palette: red, teal, yellow, purple, pink, cyan (up to 6 zones).

## MQTT Topics

With MQTT enabled, per-zone data is published:

```
kiln/zone/0/name           "Top"
kiln/zone/0/temperature    "2060.0"
kiln/zone/0/target         "2167.0"
kiln/zone/0/heat           "0.72"
kiln/zone/1/name           "Bottom"
kiln/zone/1/temperature    "2040.0"
kiln/zone/1/target         "2167.0"
kiln/zone/1/heat           "0.68"
kiln/zone_spread           "20.0"
kiln/zone_max_deviation    "78.0"
```

See [MQTT Documentation](mqtt.md) for the full topic reference.

## Simulation

Multi-zone simulation applies ~5-10% random variation in heating power and thermal resistance per zone, creating realistic temperature differences. Use `sim_speedup_factor` for fast testing.

To set a specific zone's simulated temperature:

```bash
curl -X POST http://localhost:8081/api \
  -H "Content-Type: application/json" \
  -d '{"cmd": "set_sim_temp", "temp": 500, "zone": 0}'
```

## Tuning Tips

1. **Tune each zone independently** using the Ziegler-Nichols autotuner, then configure the resulting Kp/Ki/Kd per zone
2. **Start with `"coldest"` strategy** — it's the safest default since the profile won't advance until all critical zones are ready
3. **Use `temp_offset`** if you know a zone consistently runs hotter or cooler than the measurement point
4. **Monitor zone spread** via the UI or MQTT — large spreads (>100 degrees) may indicate a failed element or thermocouple issue
