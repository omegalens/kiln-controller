# Thermocouple Interface - Do Not Modify

## Overview

The thermocouple interface is **working correctly** and should **NOT be modified** as part of the bug fixes.

## How It Works

### Hardware Layer
- **MAX31855** or **MAX31856** thermocouple amplifier chips
- These chips read temperature in **Celsius** by default (hardware specification)
- Connected via SPI interface to Raspberry Pi (or other Blinka-compatible board)

### Software Layer

Located in `lib/oven.py`, lines 138-154:

```python
def get_temperature(self):
    '''read temp from tc and convert if needed'''
    try:
        temp = self.raw_temp()  # Returns Celsius from hardware
        if config.temp_scale.lower() == "f":
            temp = (temp*9/5)+32  # Convert C to F if config requires
        self.status.good()
        return temp
    except ThermocoupleError as tce:
        # ... error handling ...
```

### Conversion Flow

```
Hardware (MAX31855/56) 
    → Reads Celsius
        → get_temperature()
            → Checks config.temp_scale
                → If "f": Convert C→F
                → If "c": Return as-is
                    → Returns temperature in config scale
```

## Why This Is Correct

1. **Hardware Standard**: Thermocouple amplifiers output Celsius
2. **Single Conversion Point**: Temperature converted once, at the source
3. **Config-Aware**: Respects user's preferred temperature scale
4. **Used Everywhere**: All oven code calls `board.temp_sensor.temperature()` which returns temp in config scale

## What Gets Returned

- If `config.temp_scale = "c"`: Returns **Celsius** (no conversion)
- If `config.temp_scale = "f"`: Returns **Fahrenheit** (converted once)

## Where Temperature Is Used

All these locations receive temperature in the **config scale** (already converted):

1. **PID Controller** (`oven.py` line 636, 700):
   ```python
   temp = self.board.temp_sensor.temperature() + config.thermocouple_offset
   pid.compute(self.target, temp, now)
   ```

2. **Emergency Shutdown** (`oven.py` line 437):
   ```python
   if (self.board.temp_sensor.temperature() + config.thermocouple_offset >= 
       config.emergency_shutoff_temp):
   ```

3. **State Reporting** (`oven.py` line 464):
   ```python
   temp = self.board.temp_sensor.temperature() + config.thermocouple_offset
   ```

4. **Seek Start** (`oven.py` line 384):
   ```python
   temp = self.board.temp_sensor.temperature()
   runtime += self.get_start_from_temperature(profile, temp)
   ```

## Relationship to Profiles

### Current State (Your System)
- **Thermocouple**: Reads Celsius, converts to Fahrenheit (because `config.temp_scale = "f"`)
- **Profiles**: Stored in Fahrenheit (1888°F, 1708°F, etc.)
- **Config**: `temp_scale = "f"`

Everything matches! No problems here.

### The Bug We're Fixing

The bug is in **profile loading/saving**, not thermocouple reading:

**Problem**: When loading a profile without `temp_units` field, the code doesn't know what units it's in
- Could assume Celsius and convert (WRONG if it's already F)
- Could assume Fahrenheit and keep as-is (CORRECT for your profiles)

**Solution**: Assume profiles are in Fahrenheit (matches your existing data)

## Important Notes

### DO NOT CHANGE:
- ❌ Thermocouple reading logic
- ❌ The `get_temperature()` method
- ❌ The hardware SPI interface
- ❌ The conversion in `get_temperature()`

### DO CHANGE:
- ✅ Profile loading assumptions (assume F)
- ✅ Profile saving (save in F)
- ✅ Profile conversion functions (make idempotent)
- ✅ Display conversion (use deep copies)

## Testing Thermocouple Interface

To verify thermocouple is working correctly:

```python
# With config.temp_scale = "f"
temp = oven.board.temp_sensor.temperature()
print(f"Temperature: {temp}°F")  # Should show Fahrenheit

# Change config temporarily to test
config.temp_scale = "c"
temp = oven.board.temp_sensor.temperature()
print(f"Temperature: {temp}°C")  # Should show Celsius
```

Both should be reasonable room temperature or kiln temperature values.

## Summary

✅ **Thermocouple interface is correct - leave it alone**
✅ **Hardware reads Celsius - correct**
✅ **Software converts to config scale - correct**  
✅ **All code receives temp in config scale - correct**

❌ **Profile loading was assuming wrong units - FIX THIS**
❌ **Profile conversion not idempotent - FIX THIS**

---

**Status:** Interface verified correct  
**Action Required:** None for thermocouple  
**Last Updated:** November 8, 2025

