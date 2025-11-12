# Cost Calculation Issues - Analysis Report

## Summary
There are **critical bugs** in the cost calculation code that make the actual cost tracking inaccurate for real kiln operations. The estimation also uses incorrect/outdated values.

---

## Issue 1: RealOven Cost Calculation Bug (CRITICAL)

**Location:** `lib/oven.py`, lines 460-465, 775-777

### Current Implementation:
```python
# In RealOven.heat_then_cool() (line 775-777):
self.heat = 0.0
if heat_on > 0:
    self.heat = 1.0

# In update_cost() (line 460-465):
def update_cost(self):
    if self.heat:
        cost = (config.kwh_rate * config.kw_elements) * ((self.heat)/3600)
    else:
        cost = 0
    self.cost = self.cost + cost
```

### The Problem:
- `self.heat` is set to **0.0 or 1.0** (binary on/off flag)
- The formula calculates: `(kwh_rate * kw_elements) * (1.0/3600)`
- This means each cycle adds: **cost per hour divided by 3600**
- But `update_cost()` is called every **2 seconds** (config.sensor_time_wait)
- **Result:** Cost is calculated as if each 2-second cycle is 1 second, making the cost **underestimated by 50%**

### Mathematical Analysis:
- Config: kwh_rate = $0.1319, kw_elements = 9.460 kW, sensor_time_wait = 2 seconds
- Current (wrong): `cost_per_cycle = 0.1319 * 9.460 * (1.0/3600) = $0.0003461`
- Should be: `cost_per_cycle = 0.1319 * 9.460 * (2.0/3600) = $0.0006922`
- **The cost is being underreported by exactly 50%**

### Correct Formula:
```python
def update_cost(self):
    if self.heat:
        cost = (config.kwh_rate * config.kw_elements) * (self.time_step/3600)
    else:
        cost = 0
    self.cost = self.cost + cost
```

---

## Issue 2: SimulatedOven Works Correctly (No Bug)

**Location:** `lib/oven.py`, lines 708, 715-717

### Current Implementation:
```python
# In SimulatedOven.heat_then_cool() (line 708, 715-717):
heat_on = float(self.time_step * pid)  # pid is 0.0 to 1.0
self.heat = 0.0
if heat_on > 0:
    self.heat = heat_on  # heat_on is in SECONDS
```

### Analysis:
- `self.heat` is set to `heat_on` which equals `time_step * pid` (in seconds)
- The formula calculates: `(kwh_rate * kw_elements) * (heat_on/3600)`
- This is **CORRECT** because `heat_on` represents actual heating time in seconds
- **No fix needed for SimulatedOven**

---

## Issue 3: JavaScript Estimation Uses Wrong Values

**Location:** `public/assets/js/picoreflow.js`, lines 54-55

### Current Implementation:
```javascript
var kwh = (3850*job_seconds/3600/1000).toFixed(2);
var cost = (kwh*kwh_rate).toFixed(2);
```

### Problems:
1. **Hardcoded wattage:** Uses `3850` watts instead of configured `9460` watts
2. **Assumes 100% duty cycle:** Multiplies full duration by full power
3. **Outdated value:** 3850W doesn't match the current config

### Config Values:
- `config.kw_elements = 9.460` kW (9460 watts)
- JavaScript uses: 3850 watts
- **Difference:** Using 40.7% of actual power rating

### Impact:
- For a 10-hour firing:
  - Current estimate: `3850W * 10h / 1000 = 38.5 kWh * $0.1319 = $5.08`
  - Should estimate: `9460W * 10h / 1000 = 94.6 kWh * $0.1319 = $12.48`
- **Estimate is 59% too low** (even assuming 100% duty cycle)

### Note on 100% Duty Cycle Assumption:
The estimation assumes the kiln runs at full power for the entire duration. While this is unrealistic (actual duty cycle varies with PID control), it could be intentional as a "worst-case" or "maximum cost" estimate. However, using the wrong wattage makes this meaningless.

### Correct Formula (Option 1 - Use Config Values):
```javascript
// Should get kw_elements from server config
var kwh = (kw_elements * 1000 * job_seconds / 3600 / 1000).toFixed(2);
// Simplifies to:
var kwh = (kw_elements * job_seconds / 3600).toFixed(2);
var cost = (kwh * kwh_rate).toFixed(2);
```

### Correct Formula (Option 2 - Realistic Estimate):
Estimate with average duty cycle (e.g., 70% for typical firing):
```javascript
var estimated_duty_cycle = 0.70; // 70% average
var kwh = (kw_elements * job_seconds / 3600 * estimated_duty_cycle).toFixed(2);
var cost = (kwh * kwh_rate).toFixed(2);
```

---

## Verification of `kwh_rate` and `kw_elements` Sync

**Location:** `kiln-controller.py`, line 393 and `picoreflow.js`, lines 14, 607

### Good News:
- `kwh_rate` is properly synced from server to client
- `kiln-controller.py` sends it via websocket
- JavaScript receives and updates it: `kwh_rate = x.kwh_rate;`

### Missing:
- `kw_elements` is **NOT** sent to the client
- This is why the JavaScript uses the hardcoded 3850 value
- **Need to add `kw_elements` to the server response**

---

## Recommended Fixes

### Fix 1: RealOven Cost Calculation (HIGH PRIORITY)
**File:** `lib/oven.py`, line 460-465

```python
def update_cost(self):
    if self.heat:
        cost = (config.kwh_rate * config.kw_elements) * (self.time_step/3600)
    else:
        cost = 0
    self.cost = self.cost + cost
```

### Fix 2: Send kw_elements to Client
**File:** `kiln-controller.py`, around line 393

Add to the config data sent to client:
```python
"kw_elements": config.kw_elements,
```

### Fix 3: Update JavaScript Estimation
**File:** `public/assets/js/picoreflow.js`

Add global variable (near line 14):
```javascript
var kw_elements = 9.460; // Default, will be updated from server
```

Update config handler (near line 607):
```javascript
kw_elements = x.kw_elements;
```

Update estimation function (line 54-55):
```javascript
// Option 1: Maximum cost (100% duty cycle)
var kwh = (kw_elements * job_seconds / 3600).toFixed(2);

// Option 2: Realistic estimate (70% duty cycle)
// var estimated_duty_cycle = 0.70;
// var kwh = (kw_elements * job_seconds / 3600 * estimated_duty_cycle).toFixed(2);

var cost = (kwh * kwh_rate).toFixed(2);
```

---

## Testing Plan

### Test 1: Verify RealOven Cost
1. Run a known profile (e.g., 1 hour at constant temperature)
2. Monitor heat on/off cycles
3. Calculate expected cost manually
4. Compare with reported cost

### Test 2: Verify Estimation
1. Select a profile
2. Check estimated kWh matches: `kw_elements * duration_seconds / 3600`
3. Check estimated cost matches: `kWh * kwh_rate`

### Test 3: Integration Test
1. Run a complete firing cycle
2. Compare estimated cost (pre-run) with actual cost (post-run)
3. Actual should be less than estimate (due to PID duty cycle)
4. Ratio should be realistic (60-80% of estimate)

---

## Impact Assessment

### Current State:
- **RealOven actual cost:** Underreported by ~50%
- **Estimated cost:** Underreported by ~59% (wrong wattage)
- **SimulatedOven:** Working correctly

### After Fixes:
- **RealOven actual cost:** Accurate
- **Estimated cost:** Accurate for maximum cost (100% duty cycle)
- **User experience:** Better cost awareness and budgeting

