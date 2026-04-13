# Rate-Based Profile Format (V2)

The kiln controller supports two profile formats:
- **V1 (Legacy)**: Time-based points `[time_seconds, temperature]`
- **V2 (Rate-Based)**: Segment-based with explicit heating rates

## Why V2?

Traditional kiln controllers use heat rate as the primary control mechanism:
- Heat rate is specified directly (e.g., 200°F/hr)
- Progress is measured by temperature achieved, not time elapsed
- More intuitive for ceramicists used to standard firing schedules

## V2 Profile Format

```json
{
  "name": "cone-05-fast-bisque",
  "type": "profile",
  "version": 2,
  "start_temp": 65,
  "temp_units": "f",
  "segments": [
    {"rate": 810, "target": 200, "hold": 0},
    {"rate": 121, "target": 250, "hold": 60},
    {"rate": 306, "target": 1733, "hold": 0},
    {"rate": 108, "target": 1888, "hold": 43}
  ]
}
```

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Profile name |
| `version` | number | Must be 2 for v2 format |
| `start_temp` | number | Starting temperature |
| `temp_units` | string | Temperature units: "f" or "c" |
| `segments` | array | Array of segment objects |

### Segment Fields

| Field | Type | Unit | Description |
|-------|------|------|-------------|
| `rate` | number or string | °/hour | Heat rate |
| `target` | number | ° | Target temperature for this segment |
| `hold` | number | minutes | Time to hold at target before next segment |

### Special Rate Values

| Value | Meaning | Behavior |
|-------|---------|----------|
| Positive number | Heat at this rate | Elements cycle to maintain rate |
| Negative number | Cool at this rate | Elements off, wait for temp to drop |
| `0` | Hold at current temperature | Maintain current temp |
| `"max"` | Heat as fast as possible | Full power until target reached |
| `"cool"` | Cool naturally | No power, wait for natural cooling |

## Examples

### Simple Bisque with Hold

```json
{
  "name": "simple-bisque",
  "version": 2,
  "start_temp": 70,
  "temp_units": "f",
  "segments": [
    {"rate": 100, "target": 200, "hold": 60},
    {"rate": 200, "target": 1000, "hold": 0},
    {"rate": 100, "target": 1800, "hold": 15}
  ]
}
```

This profile:
1. Heats at 100°F/hr to 200°F, then holds for 60 minutes (candling)
2. Heats at 200°F/hr to 1000°F
3. Heats at 100°F/hr to 1800°F, then holds for 15 minutes

### Glaze with Crash Cooling

```json
{
  "name": "glaze-with-crash",
  "version": 2,
  "start_temp": 70,
  "temp_units": "f",
  "segments": [
    {"rate": "max", "target": 1000, "hold": 0},
    {"rate": 150, "target": 2200, "hold": 10},
    {"rate": -400, "target": 1800, "hold": 0},
    {"rate": "cool", "target": 1000, "hold": 0}
  ]
}
```

This profile:
1. Heats at maximum rate to 1000°F
2. Heats at 150°F/hr to 2200°F, holds 10 minutes
3. Cools at 400°F/hr to 1800°F (controlled cooling)
4. Natural cooling to 1000°F

## Migration from V1

Use the migration script to convert existing profiles:

```bash
# Preview changes (dry run)
python scripts/migrate_profiles.py --dry-run

# Migrate with backup
python scripts/migrate_profiles.py --backup
```

The script:
- Creates a backup of your profiles directory
- Converts each v1 profile to v2 format
- Preserves the original data in `_original_data` field

## Configuration Options

Add to `config.py`:

```python
# Rate-Based Profile Control Settings
segment_complete_tolerance = 5        # Degrees within target to complete segment
rate_deviation_warning = 50           # Log warning if rate deviates this much
estimated_max_heating_rate = 500      # For "max" rate estimation
estimated_natural_cooling_rate = 100  # For "cool" rate estimation
use_rate_based_control = True         # Enable v2 control logic
allow_legacy_profiles = True          # Auto-convert v1 profiles on load
```

## Backward Compatibility

- V1 profiles are automatically converted to segments when loaded
- The legacy data format is preserved for graph display
- Both formats can coexist in the profiles directory
