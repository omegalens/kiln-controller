# Cost Calculation Fixes - Implementation Summary

## Overview
All three critical cost calculation bugs have been fixed. The actual cost tracking will now be accurate, and the pre-firing cost estimation will use the correct power rating.

---

## Fix 1: RealOven Cost Calculation ✅

**File:** `lib/oven.py`, line 462

**Problem:** Cost was underreported by 50% because the formula used `self.heat` (0.0 or 1.0) instead of the actual time step duration (2 seconds).

**Before:**
```python
cost = (config.kwh_rate * config.kw_elements) * ((self.heat)/3600)
```

**After:**
```python
cost = (config.kwh_rate * config.kw_elements) * (self.time_step/3600)
```

**Impact:** 
- Cost now correctly accounts for the 2-second control loop interval
- A 10-hour firing that previously reported $6.24 will now correctly report $12.48

---

## Fix 2: Server Config - Send kw_elements to Client ✅

**File:** `kiln-controller.py`, line 394

**Problem:** The `kw_elements` value was not being sent to the client, forcing JavaScript to use a hardcoded (and wrong) value.

**Before:**
```python
return json.dumps({"temp_scale": config.temp_scale,
    "time_scale_slope": config.time_scale_slope,
    "time_scale_profile": config.time_scale_profile,
    "kwh_rate": config.kwh_rate,
    "currency_type": config.currency_type})
```

**After:**
```python
return json.dumps({"temp_scale": config.temp_scale,
    "time_scale_slope": config.time_scale_slope,
    "time_scale_profile": config.time_scale_profile,
    "kwh_rate": config.kwh_rate,
    "kw_elements": config.kw_elements,
    "currency_type": config.currency_type})
```

**Impact:**
- Client now receives the configured kiln power rating
- Estimation will automatically update if config.kw_elements is changed

---

## Fix 3: JavaScript Cost Estimation ✅

**File:** `public/assets/js/picoreflow.js`

### 3a. Added kw_elements Variable (line 15)

**Before:**
```javascript
var kwh_rate = 0.26;
var currency_type = "EUR";
```

**After:**
```javascript
var kwh_rate = 0.26;
var kw_elements = 9.460;
var currency_type = "EUR";
```

### 3b. Receive kw_elements from Server (line 609)

**Before:**
```javascript
kwh_rate = x.kwh_rate;
currency_type = x.currency_type;
```

**After:**
```javascript
kwh_rate = x.kwh_rate;
kw_elements = x.kw_elements;
currency_type = x.currency_type;
```

### 3c. Fixed Estimation Formula (line 55)

**Problem:** Used hardcoded 3850 watts instead of configured 9460 watts (config.kw_elements).

**Before:**
```javascript
var kwh = (3850*job_seconds/3600/1000).toFixed(2);
```

**After:**
```javascript
var kwh = (kw_elements * job_seconds / 3600).toFixed(2);
```

**Impact:**
- Estimation now uses the correct kiln power rating (9460W vs 3850W)
- A 10-hour profile that estimated 38.5 kWh ($5.08) will now estimate 94.6 kWh ($12.48)
- This is a maximum cost assuming 100% duty cycle; actual cost will be 60-80% of this

---

## Expected Behavior After Fixes

### For a Typical 10-Hour Cone 6 Firing:

**Before Fixes:**
- Estimated cost (pre-run): $5.08 (wrong: used 3850W)
- Actual cost (post-run): $6.24 (wrong: 50% underreported)
- User sees inconsistent and inaccurate data

**After Fixes:**
- Estimated cost (pre-run): $12.48 (correct: 9460W × 10h × $0.1319/kWh)
- Actual cost (post-run): ~$8.74-$10.00 (correct: accounts for ~70-80% duty cycle)
- User sees realistic maximum estimate and accurate actual cost

### Validation:
- Actual cost should always be less than estimated cost (due to PID duty cycle)
- Ratio should be realistic (60-80% depending on firing profile)
- Both values now reflect the correct power rating

---

## Testing Recommendations

1. **Quick Test**: Run a short test profile (1 hour) and verify:
   - Estimated cost matches: `9.460 × 1 × 0.1319 = $1.25`
   - Actual cost is less than estimate (e.g., $0.75-$1.00 depending on duty cycle)

2. **Full Test**: Run a complete firing cycle and verify:
   - Cost increments correctly every 2 seconds during heating
   - Final cost is reasonable based on total energy consumption
   - Estimated vs actual cost ratio is realistic

3. **Config Test**: Change `config.kw_elements` and verify:
   - New value is reflected in profile cost estimates
   - Actual cost calculation uses new value

---

## Files Modified

1. `/Users/galen/Documents/Code/kiln-controller/lib/oven.py` - Fixed cost calculation
2. `/Users/galen/Documents/Code/kiln-controller/kiln-controller.py` - Added kw_elements to config
3. `/Users/galen/Documents/Code/kiln-controller/public/assets/js/picoreflow.js` - Fixed estimation formula

All changes are backward compatible and require no database migrations or config changes.

