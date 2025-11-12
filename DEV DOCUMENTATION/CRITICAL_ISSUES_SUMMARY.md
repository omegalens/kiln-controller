# Critical Issues Summary - Quick Reference

## 🔴 CRITICAL - Fix Immediately

### 1. Temperature Conversion Double-Application Bug
**Location:** `config.py` lines 302-316  
**Risk:** Could fire kiln at wrong temperature, damaging ceramics or equipment

The `convert_to_c()` and `convert_to_f()` functions don't check if conversion already happened. If called twice, temperatures are converted twice:
- 400°F → 204°C → -88°C (incorrect!)

**Fix:** Add temp_units check before converting, or make conversions idempotent.

---

### 2. Program Crash at Schedule Completion
**Location:** `lib/oven.py` lines 778-786  
**Risk:** Kiln elements stay on if program crashes at end of schedule

When runtime equals schedule duration, `get_surrounding_points()` returns `(None, None)`, causing `TypeError` when accessing `next_point[1]`.

**Fix:** Handle edge case when time equals duration:
```python
if time >= self.get_duration():
    return self.data[-1][1]  # Return final temperature
```

---

### 3. Division by Zero in Seek Start Feature
**Location:** `lib/oven.py` lines 742-747  
**Risk:** Seek start silently fails; schedule starts at wrong time

Returns 0 for errors, but 0 is also a valid time. Also returns 0 for negative slopes (cooling), which may be legitimate.

**Fix:** Return None for errors, check for None in callers.

---

### 4. WebSocket List Modification During Iteration
**Location:** `lib/ovenWatcher.py` lines 83-91  
**Risk:** Memory leak, websocket connections not properly cleaned up

Removing items from list while iterating skips elements:
```python
for wsock in self.observers:
    # ...
    self.observers.remove(wsock)  # BUG!
```

**Fix:** Collect items to remove, then remove after iteration, or use list comprehension.

---

## 🟠 HIGH PRIORITY - Fix Soon

### 5. Backwards Temperature Conversion in Tuner
**Location:** `kiln-tuner.py` lines 195-196  
**Risk:** PID tuning uses wrong target temperature

Logic is backwards - converts TO Celsius when config says Celsius, should convert FROM input scale TO Celsius.

---

### 6. No File Locking on State File
**Location:** `lib/oven.py` lines 489-510  
**Risk:** Automatic restart could read corrupted data after power failure

State file written every 2 seconds without locking. If power fails during write, file is corrupted.

---

### 7. API Endpoint Uses Multiple `if` Instead of `elif`
**Location:** `kiln-controller.py` lines 59-115  
**Risk:** Multiple commands could execute from single request

All conditions checked even after match. Could pause and resume in same request.

---

### 8. Profile Normalization Doesn't Preserve Originals
**Location:** `kiln-controller.py` lines 318-326  
**Risk:** Profile files become inconsistent with displayed temperatures

Converts temperatures in-place but doesn't save back to disk or preserve originals.

---

## 🟡 MEDIUM PRIORITY

- **Throttle logic checks setpoint not actual temperature** (line 828-831 oven.py)
- **PID integral accumulates without bounds** (line 833-834 oven.py)
- **TempTracker initializes with zeros** (line 172 oven.py) - affects first readings
- **Catching up recalculates time every iteration** (line 413 oven.py)
- **No error handling in delete_profile** (line 332 kiln-controller.py)

---

## Testing Gaps

⚠️ **NO TESTS FOR:**
- Temperature conversion (critical!)
- PID controller (critical!)
- Automatic restart
- WebSocket handlers
- Error conditions
- Integration tests

Only the Profile class has unit tests.

---

## Immediate Action Items

1. ✅ Add validation to temperature conversion to prevent double-conversion
2. ✅ Fix schedule completion edge case
3. ✅ Fix websocket observer list modification
4. ✅ Add file locking to state file I/O
5. ✅ Fix kiln-tuner temperature conversion logic
6. ✅ Add tests for all critical paths
7. ✅ Add comprehensive error handling

---

## Safe Use Recommendations

**Until these issues are fixed:**

1. **Test with simulated oven first** - Always run in simulation mode before using with real kiln
2. **Monitor first firing closely** - Don't leave unattended until verified correct
3. **Verify temperatures with external thermometer** - Cross-check controller readings
4. **Don't rely on automatic restart** - Could start with wrong state after power failure
5. **Keep backups of profiles** - They might get corrupted during conversion
6. **Watch for schedule completion** - Manually verify kiln turns off at end

---

**Generated:** November 6, 2025

