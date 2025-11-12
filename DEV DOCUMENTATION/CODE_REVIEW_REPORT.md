# Kiln Controller Code Review Report
**Date:** November 6, 2025  
**Reviewed By:** AI Code Reviewer  
**Scope:** Complete codebase logical error analysis

---

## Executive Summary

This report documents logical errors, potential bugs, and design issues found in the kiln-controller codebase. The issues range from critical safety concerns to minor inconsistencies. Issues are categorized by severity:

- **CRITICAL**: Could cause safety issues or system failure
- **HIGH**: Likely to cause incorrect behavior or crashes
- **MEDIUM**: Could cause issues under certain conditions
- **LOW**: Minor issues or inconsistencies

---

## CRITICAL Issues

### 1. Race Condition in Temperature Conversion (config.py lines 302-316)
**Severity:** CRITICAL  
**File:** `config.py`  
**Lines:** 302-316

**Issue:**
The temperature conversion functions `convert_to_c()` and `convert_to_f()` mutate profile data but don't update the `temp_units` field, potentially causing double conversions.

```python
def convert_to_c(profile):
    newdata=[]
    for (secs,temp) in profile["data"]:
        temp = (5/9)*(temp-32)
        newdata.append((secs,temp))
    profile["data"]=newdata
    return profile
```

**Problem:**
- If `convert_to_c()` is called twice on the same profile, it will incorrectly convert already-converted data
- The function in `add_temp_units()` (line 299) calls `convert_to_c()` but the profile might already be in Celsius
- This could cause incorrect firing temperatures, potentially damaging ceramics or the kiln

**Impact:** Incorrect temperature targets could lead to firing failures or safety issues.

---

### 2. Division by Zero in Profile.find_x_given_y_on_line_from_two_points() 
**Severity:** CRITICAL  
**File:** `lib/oven.py`  
**Lines:** 742-747

**Issue:**
The function returns 0 for error conditions but 0 is also a valid time value.

```python
@staticmethod
def find_x_given_y_on_line_from_two_points(y, point1, point2):
    if point1[0] > point2[0]: return 0  # time2 before time1 makes no sense
    if point1[1] >= point2[1]: return 0 # Zero will crach. Negative temperature slope
    x = (y - point1[1]) * (point2[0] - point1[0]) / (point2[1] - point1[1]) + point1[0]
    return x
```

**Problems:**
1. The comment says "Zero will crach" but it returns 0 anyway
2. Returning 0 for error vs. valid time=0 is ambiguous
3. If `point1[1] == point2[1]`, division by zero would occur, but the check uses `>=` instead of `==`
4. For cooling segments (negative slope), this incorrectly returns 0

**Impact:** The seek_start feature may fail silently or incorrectly position the schedule start.

---

### 3. Unhandled None Values in Profile.get_target_temperature()
**Severity:** CRITICAL  
**File:** `lib/oven.py`  
**Lines:** 778-786

**Issue:**
When profile time equals duration exactly, `get_surrounding_points()` returns `(None, None)`, which crashes the temperature calculation.

```python
def get_target_temperature(self, time):
    if time > self.get_duration():
        return 0
    
    (prev_point, next_point) = self.get_surrounding_points(time)
    
    incl = float(next_point[1] - prev_point[1]) / float(next_point[0] - prev_point[0])
    temp = prev_point[1] + (time - prev_point[0]) * incl
    return temp
```

**Problem:**
- When `time == get_duration()`, the loop in `get_surrounding_points()` (lines 770-774) never executes
- `prev_point` and `next_point` remain None
- Line 784 attempts `next_point[1]` which raises `TypeError: 'NoneType' object is not subscriptable`

**Impact:** Program crashes when schedule completes, preventing clean shutdown.

---

### 4. Missing Observer Removal During Iteration
**Severity:** HIGH  
**File:** `lib/ovenWatcher.py`  
**Lines:** 79-91

**Issue:**
Modifying a list while iterating over it causes skipped elements and potential crashes.

```python
def notify_all(self, message):
    message_json = json.dumps(message)
    log.debug("sending to %d clients: %s"%(len(self.observers),message_json))
    
    for wsock in self.observers:
        if wsock:
            try:
                wsock.send(message_json)
            except:
                log.error("could not write to socket %s"%wsock)
                self.observers.remove(wsock)  # DANGEROUS!
        else:
            self.observers.remove(wsock)  # DANGEROUS!
```

**Problem:**
- Calling `list.remove()` during iteration causes the iterator to skip elements
- If multiple consecutive websockets fail, only alternating ones are removed
- This is a well-known Python antipattern

**Impact:** Failed websocket connections accumulate, causing memory leaks and wasted CPU cycles.

---

## HIGH Priority Issues

### 5. Inconsistent Error Handling in API Endpoint
**Severity:** HIGH  
**File:** `kiln-controller.py`  
**Lines:** 59-115

**Issue:**
The `/api` POST endpoint has multiple if statements without elif, and most don't return a value.

```python
@app.post('/api')
def handle_api():
    log.info("/api is alive")
    
    if bottle.request.json['cmd'] == 'run':
        # ... handling code ...
        # No return statement
    
    if bottle.request.json['cmd'] == 'pause':
        log.info("api pause command received")
        oven.state = 'PAUSED'
        # No return statement
    
    # ... more if statements ...
    
    return { "success" : True }
```

**Problems:**
1. Using multiple `if` instead of `elif` means all conditions are checked even after a match
2. Missing return statements after error conditions (line 83)
3. Direct state mutation (`oven.state = 'PAUSED'`) without validation
4. No exception handling for missing or malformed JSON

**Impact:** 
- Multiple commands could be processed in one request
- Errors aren't properly reported to client
- System could enter invalid state

---

### 6. State File Race Condition
**Severity:** HIGH  
**File:** `lib/oven.py`  
**Lines:** 489-510

**Issue:**
The state file is read and written without file locking, creating race conditions.

**Problem:**
- `save_state()` (line 489) and `should_i_automatic_restart()` (line 512) access the same file
- No file locking mechanism prevents concurrent access
- If the file is partially written during a power failure, JSON parsing will fail
- If the file is being written when checked for age, results are unpredictable

**Impact:** Automatic restart feature could fail or read corrupted data.

---

### 7. Incorrect Integer Division in Kiln-Tuner
**Severity:** HIGH  
**File:** `kiln-tuner.py`  
**Lines:** 195-196

**Issue:**
Temperature conversion assumes Fahrenheit input when temp_scale is Celsius.

```python
target = args.target_temp
if config.temp_scale.lower() == "c":
    target = (target - 32)*5/9
```

**Problem:**
- If `config.temp_scale` is "c", it converts the target FROM Fahrenheit TO Celsius
- This is backwards - if the scale is already Celsius, no conversion should happen
- The logic should be: if scale is "f", convert input TO Celsius for internal use

**Impact:** PID tuning will target wrong temperature, producing incorrect PID values.

---

### 8. Profile Normalization Doesn't Preserve Original Units
**Severity:** HIGH  
**File:** `kiln-controller.py`  
**Lines:** 318-326

**Issue:**
The `normalize_temp_units()` function modifies profiles without preserving originals.

```python
def normalize_temp_units(profiles):
    normalized = []
    for profile in profiles:
        if "temp_units" in profile:
            if config.temp_scale == "f" and profile["temp_units"] == "c": 
                profile = convert_to_f(profile)
                profile["temp_units"] = "f"
        normalized.append(profile)
    return normalized
```

**Problems:**
1. Modifies the profile object in-place, then changes temp_units field
2. If profile doesn't have temp_units, it's returned as-is (assumption it matches config?)
3. Only handles c->f conversion, not f->c
4. Modified profiles are sent to clients but never saved back to disk consistently

**Impact:** Profile files on disk may become inconsistent with displayed values.

---

## MEDIUM Priority Issues

### 9. TempTracker Initial Values Are Zero
**Severity:** MEDIUM  
**File:** `lib/oven.py`  
**Lines:** 166-184

**Issue:**
The temperature tracker initializes with zeros, skewing early temperature readings.

```python
def __init__(self):
    self.size = config.temperature_average_samples
    self.temps = [0 for i in range(self.size)]
```

**Problem:**
- First N samples include zeros in median calculation
- If `temperature_average_samples` is 10, the first ~10 readings are artificially low
- For a kiln at room temperature (70°F), median of [0,0,0,0,0,0,0,70,70,70] is 0

**Impact:** Initial temperature readings are incorrect, potentially affecting start behavior.

---

### 10. Throttle Logic Has No Temperature Tracking
**Severity:** MEDIUM  
**File:** `lib/oven.py`  
**Lines:** 828-831

**Issue:**
Throttling is based on setpoint, not actual temperature.

```python
if config.throttle_below_temp and config.throttle_percent:
    if setpoint <= config.throttle_below_temp:
        output = config.throttle_percent/100
        log.info("max heating throttled...")
```

**Problem:**
- Throttle is designed to prevent overshoot at low temperatures
- But it checks `setpoint` (target) not `ispoint` (actual temperature)
- If schedule starts at high temperature and ramps down, throttling won't engage appropriately

**Impact:** Throttling feature may not prevent overshoot as intended.

---

### 11. Catching Up Resets Time Incorrectly
**Severity:** MEDIUM  
**File:** `lib/oven.py`  
**Lines:** 404-422

**Issue:**
The `kiln_must_catch_up()` function recalculates start_time every iteration while catching up.

```python
def kiln_must_catch_up(self):
    if config.kiln_must_catch_up == True:
        temp = self.board.temp_sensor.temperature() + config.thermocouple_offset
        if self.target - temp > config.pid_control_window:
            log.info("kiln must catch up, too cold, shifting schedule")
            self.start_time = self.get_start_time()  # Recalculated every time
            self.catching_up = True
            return
```

**Problem:**
- `get_start_time()` is calculated from current runtime
- This gets recalculated every iteration while catching up
- The schedule time doesn't truly "pause", it just oscillates
- Multiple log messages spam the log file

**Impact:** Schedule timing becomes unpredictable during catch-up periods.

---

### 12. Heat Rate Calculation Using Runtime Not Wall Time
**Severity:** MEDIUM  
**File:** `lib/oven.py`  
**Lines:** 360-376

**Issue:**
Heat rate calculation could be affected by simulation speedup factor.

```python
def set_heat_rate(self,runtime,temp):
    numtemps = 60
    self.heat_rate_temps.append((runtime,temp))
    # ...
    if time2 > time1:
        self.heat_rate = ((temp2 - temp1) / (time2 - time1))*3600
```

**Problem:**
- Uses `runtime` which in SimulatedOven is sped up
- For RealOven this is fine, but in simulation heat_rate display is incorrect
- The *3600 converts to per-hour, but this is runtime-hours not wall-clock-hours

**Impact:** Heat rate display is incorrect during simulations.

---

### 13. Profile Delete Has No Error Handling
**Severity:** MEDIUM  
**File:** `kiln-controller.py`  
**Lines:** 328-334

**Issue:**
`delete_profile()` calls `os.remove()` without try/except.

```python
def delete_profile(profile):
    profile_json = json.dumps(profile)
    filename = profile['name']+".json"
    filepath = os.path.join(profile_path, filename)
    os.remove(filepath)
    log.info("Deleted %s" % filepath)
    return True
```

**Problems:**
1. If file doesn't exist, raises `FileNotFoundError`
2. If permissions insufficient, raises `PermissionError`
3. Always returns True, even if deletion fails
4. `profile_json` is created but never used

**Impact:** Client receives success message even when delete fails.

---

### 14. PID Integral Accumulates Indefinitely
**Severity:** MEDIUM  
**File:** `lib/oven.py`  
**Lines:** 805-865

**Issue:**
The PID integral term accumulates without bounds when inside control window.

```python
else:
    icomp = (error * timeDelta * (1/self.ki))
    self.iterm += (error * timeDelta * (1/self.ki))
    dErr = (error - self.lastErr) / timeDelta
    output = self.kp * error + self.iterm + self.kd * dErr
    output = sorted([-1 * window_size, output, window_size])[1]
```

**Problem:**
- `self.iterm` accumulates as long as error exists
- Output is clamped but `self.iterm` is not
- If kiln can't reach temperature (broken element), integral grows infinitely
- When temperature finally reached, huge negative integral causes undershoot

**Impact:** May cause temperature oscillations and poor control, especially during long firings.

---

### 15. Duplicate icomp Calculation
**Severity:** LOW  
**File:** `lib/oven.py`  
**Lines:** 833-834

**Issue:**
The integral component is calculated twice identically.

```python
icomp = (error * timeDelta * (1/self.ki))
self.iterm += (error * timeDelta * (1/self.ki))
```

**Problem:**
- `icomp` is calculated but never used
- Same calculation happens twice
- Wasteful and confusing

**Impact:** Minor performance impact and code readability.

---

## LOW Priority Issues

### 16. Inconsistent String Comparison
**Severity:** LOW  
**Multiple Files**

**Issue:**
Temperature scale is checked inconsistently:
- Line 179 config.py: `temp_scale = "f"` 
- Line 142 oven.py: `if config.temp_scale.lower() == "f"`
- Line 296 kiln-controller.py: `if config.temp_scale=="c"`
- Line 322 kiln-controller.py: `if config.temp_scale == "f"`

**Problem:**
- Some use `.lower()`, some don't
- If user sets `temp_scale = "F"` (uppercase), behavior is inconsistent

**Impact:** Minor - inconsistent handling of uppercase temperature scale values.

---

### 17. Magic Numbers Throughout Code
**Severity:** LOW  
**Multiple Locations**

**Issue:**
Numerous magic numbers without explanation:
- Line 193 oven.py: `self.limit = 30` (error percentage limit)
- Line 365 oven.py: `numtemps = 60` (heat rate samples)
- Line 808 oven.py: `window_size = 100` (PID window)
- Line 353 oven.py: `if temp > target_temp + 5` (seek tolerance)

**Problem:**
- Hard to understand intent
- Difficult to tune or modify
- Some should be config options

**Impact:** Code maintainability and flexibility.

---

### 18. Unused Variables
**Severity:** LOW  
**Multiple Locations**

**Issue:**
Several variables are assigned but never used:
- Line 329 kiln-controller.py: `profile_json = json.dumps(profile)` in delete_profile
- Line 71 kiln-controller.py: `startat = 0` initialized then possibly reassigned
- Line 833 oven.py: `icomp` calculated but not used

**Impact:** Code clarity and minor memory waste.

---

### 19. Inconsistent Exception Handling
**Severity:** LOW  
**Multiple Files**

**Issue:**
Exception handling varies widely:
- Some use bare `except:` (bad practice)
- Some catch specific exceptions
- Some have no exception handling where needed

Examples:
- Line 198 kiln-controller.py: `except:` (bare except)
- Line 264 oven.py: `except:` (bare except)
- Line 332 kiln-controller.py: No exception handling in delete_profile

**Problem:**
- Bare excepts catch KeyboardInterrupt and SystemExit
- Makes debugging difficult
- Hides unexpected errors

**Impact:** Harder to debug issues, may mask serious errors.

---

### 20. Comment Says "FIXME" But Not Fixed
**Severity:** LOW  
**File:** `kiln-controller.py`  
**Line:** 85

**Issue:**
```python
# FIXME juggling of json should happen in the Profile class
profile_json = json.dumps(profile)
profile = Profile(profile_json)
```

**Problem:**
- Profile class expects JSON string, not a dict
- This is awkward API design
- Comment acknowledges this but it's not fixed

**Impact:** Code quality and API design.

---

### 21. WebSocket Close Not Handled Explicitly
**Severity:** LOW  
**File:** `kiln-controller.py`  
**Lines:** 146-182, 185-229

**Issue:**
WebSocket handlers break on `WebSocketError` but don't explicitly close connection.

**Problem:**
- Connection may not be properly closed
- No cleanup code in finally block
- May leak file descriptors

**Impact:** Minor resource leaks in long-running processes.

---

### 22. Log Level Mismatch
**Severity:** LOW  
**File:** `kiln-controller.py`  
**Lines:** 134

**Issue:**
```python
log.debug("serving %s" % filename)
```

**Problem:**
- Static file serving is logged at DEBUG level
- But this happens on every file request (CSS, JS, images)
- Should probably not be logged at all, or use TRACE level

**Impact:** Log files grow quickly, making debugging harder.

---

## Design Concerns (Not Bugs, But Questionable Choices)

### 23. Thermocouple Offset Applied Inconsistently
**File:** `lib/oven.py`  
**Locations:** Lines 409, 437, 464, 699, 636

The thermocouple offset is added at different points in different code paths:
- Sometimes added when reading temperature
- Sometimes added in emergency check
- Sometimes added in PID calculation
- Not clear if it's sometimes double-applied

**Recommendation:** Apply offset once, at the source (when reading from sensor).

---

### 24. Global State Management
**Multiple Files**

The application uses global state extensively:
- `config` module is imported globally
- Single `oven` and `ovenWatcher` instances
- State persisted to single file

**Concerns:**
- Not testable without mocking globals
- Can't run multiple kilns from one controller
- State file could be corrupted by multiple processes

**Recommendation:** Consider dependency injection pattern.

---

### 25. No Input Validation
**File:** `kiln-controller.py`  
**Multiple endpoints**

API endpoints don't validate inputs:
- No check if profile exists before running
- No validation of startat parameter
- No bounds checking on temperature values
- No sanitization of profile names (could contain path separators)

**Security Concern:** Path traversal vulnerability in profile names.

---

## Test Coverage Gaps

Based on the test files reviewed:

1. **No tests for temperature conversion logic** - Critical functionality untested
2. **No tests for PID controller** - Complex algorithm untested
3. **No tests for automatic restart** - Important feature untested
4. **No tests for websocket handlers** - Integration points untested
5. **No tests for error conditions** - Exception paths untested
6. **Only Profile class has tests** - Very limited coverage

---

## Summary Statistics

- **Critical Issues:** 4
- **High Priority Issues:** 4
- **Medium Priority Issues:** 6
- **Low Priority Issues:** 7
- **Design Concerns:** 3
- **Total Issues Found:** 24

---

## Recommendations

### Immediate Actions (Fix Before Production Use)
1. Fix CRITICAL issues #1-4 immediately
2. Add comprehensive error handling to all websocket code
3. Add input validation to all API endpoints
4. Implement proper file locking for state file
5. Add integration tests for safety-critical code

### Short Term (Fix Soon)
1. Fix temperature conversion logic and add tests
2. Implement PID integral windup protection
3. Fix list modification during iteration
4. Add proper exception handling throughout

### Long Term (Technical Debt)
1. Refactor to use dependency injection
2. Improve test coverage to >80%
3. Add type hints (Python 3.5+)
4. Document all magic numbers as named constants
5. Create comprehensive API documentation

---

## Positive Findings

Despite the issues found, the code has several strengths:
- Generally well-structured with clear separation of concerns
- Simulation mode is excellent for testing
- Good use of threading for concurrent operations
- Comprehensive configuration system
- Active logging throughout

---

## Conclusion

The kiln-controller has several logical errors that should be addressed, particularly the critical issues related to temperature conversion and profile parsing. The code is generally well-structured but would benefit from:

1. More comprehensive testing
2. Better error handling
3. Input validation
4. Consistent coding patterns

The most concerning issues are those that could cause incorrect firing temperatures, as these could damage expensive ceramics or the kiln itself. These should be fixed before using the controller with a real kiln.

---

**Report End**

