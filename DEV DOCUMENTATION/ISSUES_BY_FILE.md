# Issues by File - Developer Reference

## Issues Organized by File

This document helps you quickly see which files need attention and what issues they contain.

---

## 🔥 lib/oven.py (Most Issues - 10 total)

| Line(s) | Severity | Issue | 
|---------|----------|-------|
| 172 | MEDIUM | TempTracker initializes with zeros, skewing early readings |
| 742-747 | **CRITICAL** | Division by zero risk, returns 0 for errors (ambiguous) |
| 763-774 | **CRITICAL** | get_surrounding_points returns (None, None) at edge case |
| 778-786 | **CRITICAL** | get_target_temperature crashes when time == duration |
| 360-376 | MEDIUM | Heat rate calculation wrong for simulations |
| 404-422 | MEDIUM | Catching up recalculates start_time incorrectly |
| 489-510 | **HIGH** | State file has no locking (race condition) |
| 805-865 | MEDIUM | PID integral accumulates without bounds (windup) |
| 828-831 | MEDIUM | Throttle checks setpoint instead of actual temp |
| 833-834 | LOW | Duplicate icomp calculation (unused variable) |

**Priority:** Fix CRITICAL issues first, then state file locking.

---

## 🔥 kiln-controller.py (7 issues)

| Line(s) | Severity | Issue |
|---------|----------|-------|
| 59-115 | **HIGH** | API endpoint uses multiple if instead of elif |
| 85 | LOW | FIXME comment - JSON juggling should be in Profile class |
| 134 | LOW | Excessive debug logging for static files |
| 146-229 | LOW | WebSocket handlers don't explicitly close connections |
| 302-316 | **CRITICAL** | Temperature conversion can be applied twice |
| 318-326 | **HIGH** | Profile normalization doesn't preserve originals |
| 328-334 | MEDIUM | delete_profile has no error handling |

**Priority:** Fix temperature conversion (#302-316) immediately.

---

## 🔥 lib/ovenWatcher.py (1 issue)

| Line(s) | Severity | Issue |
|---------|----------|-------|
| 79-91 | **CRITICAL** | List modification during iteration (memory leak) |

**Priority:** Fix immediately - straightforward fix.

---

## 🔥 kiln-tuner.py (1 issue)

| Line(s) | Severity | Issue |
|---------|----------|-------|
| 195-196 | **HIGH** | Temperature conversion logic is backwards |

**Priority:** Fix before using tuner for PID values.

---

## config.py (1 issue)

| Line(s) | Severity | Issue |
|---------|----------|-------|
| 179, multiple | LOW | Inconsistent temp_scale string comparison (.lower() sometimes missing) |

**Priority:** Low - standardize on .lower() everywhere.

---

## Multiple Files (Cross-cutting concerns)

### Issue: Inconsistent Exception Handling
**Severity:** LOW  
**Files:** kiln-controller.py, lib/oven.py, watcher.py

Many bare `except:` statements that catch all exceptions including KeyboardInterrupt.

**Locations:**
- kiln-controller.py: lines 198, 227, 240, 255, 264-265
- lib/oven.py: line 465
- lib/ovenWatcher.py: lines 74, 87
- watcher.py: lines 37, 44, 66

**Fix:** Use specific exception types or at least `except Exception:`.

---

### Issue: Magic Numbers
**Severity:** LOW  
**Files:** lib/oven.py (primarily)

Hard-coded values without explanation:
- Line 193: `self.limit = 30` - error percentage threshold
- Line 365: `numtemps = 60` - heat rate sample size  
- Line 808: `window_size = 100` - PID calculation window
- Line 353: `if temp > target_temp + 5` - seek start tolerance

**Fix:** Extract to named constants or config parameters.

---

## Test Files (Coverage Gaps)

### Test/test_Profile.py
✅ **Good coverage of Profile class methods**
- Tests get_target_temperature()
- Tests find_next_time_from_temperature()
- Tests find_x_given_y_on_line_from_two_points()

### ❌ Missing Test Files
No tests exist for:
- Oven class (RealOven, SimulatedOven)
- PID controller
- Temperature conversion functions
- OvenWatcher
- API endpoints
- WebSocket handlers
- Automatic restart logic
- Error handling paths

**Recommendation:** Add pytest fixtures for Oven and write integration tests.

---

## Files With No Issues Found

These files appear to have no logical errors (though they haven't been extensively tested):

✅ **kiln-logger.py** - Clean code, good exception handling  
✅ **watcher.py** - Simple monitoring script, well-structured  
✅ **test-output.py** - Simple test script  
✅ **test-thermocouple.py** - Simple test script  
✅ **gpioreadall.py** - Not reviewed (GPIO utility)

---

## Issue Priority Matrix

```
                 lib/oven.py  kiln-controller.py  ovenWatcher.py  kiln-tuner.py  config.py
CRITICAL              3               1                  1              0            0
HIGH                  1               2                  0              1            0  
MEDIUM                4               1                  0              0            0
LOW                   2               3                  0              0            1
──────────────────────────────────────────────────────────────────────────────────────
TOTAL                10               7                  1              1            1
```

---

## Recommended Fix Order

### Phase 1: Critical Fixes (Do First)
1. ✅ **ovenWatcher.py line 79-91** - Fix list modification (10 min fix)
2. ✅ **oven.py line 778-786** - Handle edge case in get_target_temperature (15 min)
3. ✅ **oven.py line 742-747** - Return None for errors instead of 0 (20 min)
4. ✅ **kiln-controller.py line 302-316** - Fix temperature conversion logic (30 min)

**Estimated time: 1.5 hours**

### Phase 2: High Priority (Do Next)
1. ✅ **oven.py line 489-510** - Add file locking to state file (45 min)
2. ✅ **kiln-controller.py line 59-115** - Change if to elif in API handler (15 min)
3. ✅ **kiln-controller.py line 318-326** - Fix profile normalization (30 min)
4. ✅ **kiln-tuner.py line 195-196** - Fix temperature conversion (10 min)

**Estimated time: 1.5 hours**

### Phase 3: Medium Priority (After Testing)
1. Fix PID integral windup
2. Fix throttle logic
3. Add error handling to delete_profile
4. Fix TempTracker initialization
5. Fix catching up logic

**Estimated time: 3 hours**

### Phase 4: Add Tests
1. Write tests for temperature conversion
2. Write tests for PID controller
3. Write integration tests for API
4. Add error case testing

**Estimated time: 8+ hours**

---

## Quick Stats

- **Total Issues Found:** 24
- **Files with Issues:** 5
- **Most Problematic File:** lib/oven.py (10 issues)
- **Critical Issues:** 4
- **High Priority Issues:** 4
- **Estimated Fix Time (Critical+High):** 3 hours
- **Test Coverage:** ~5% (only Profile class)

---

**Last Updated:** November 6, 2025

