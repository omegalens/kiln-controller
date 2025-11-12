# Bug Fix Implementation Plan

## Overview
This document outlines a safe, systematic approach to fixing all identified bugs in the kiln-controller project. The plan prioritizes safety, maintains backward compatibility where possible, and follows Python best practices.

---

## Critical Bugs (Fix First - Safety Critical)

### 1. List Modification During Iteration - `lib/ovenWatcher.py:83-91`

**Priority:** CRITICAL - Can cause memory leaks and missed client notifications

**Root Cause:** Modifying `self.observers` list while iterating over it causes iterator to skip elements.

**Solution:** Iterate over a shallow copy of the list
```python
for wsock in self.observers[:]:  # Creates shallow copy
```

**Why This Solution:**
- Simplest fix (one character change)
- Most Pythonic approach
- No performance impact (shallow copy is O(n) but list is typically small)
- Clear intent to other developers
- Safe for concurrent scenarios

**Testing Strategy:**
```python
# Test with mock failing sockets
observers = [good_sock1, bad_sock, good_sock2, None, bad_sock2]
notify_all({"test": "msg"})
assert len(observers) == 2  # Only good sockets remain
```

**Files to Modify:**
- `lib/ovenWatcher.py` line 83

**Dependencies:** None

**Risk Assessment:** LOW - Simple change, well-tested pattern

---

### 2. Schedule Completion Crash - `lib/oven.py:763-786`

**Priority:** CRITICAL - Causes crash when schedule completes, kiln elements may stay on

**Root Cause:** When `time >= duration`, `get_surrounding_points()` returns `(None, None)`, then `get_target_temperature()` tries to access `next_point[1]` causing `TypeError`.

**Solution:** Handle edge case when time equals or exceeds duration

**Implementation:**
```python
def get_surrounding_points(self, time):
    if time > self.get_duration():
        return (None, None)
    
    # NEW: Handle time at or past final point
    if time >= self.data[-1][0]:
        if len(self.data) >= 2:
            return (self.data[-2], self.data[-1])
        else:
            return (self.data[0], self.data[0])  # Single point profile
    
    # ... rest of existing code

def get_target_temperature(self, time):
    if time > self.get_duration():
        return 0
    
    (prev_point, next_point) = self.get_surrounding_points(time)
    
    # NEW: Defensive check (should never happen with above fix)
    if prev_point is None or next_point is None:
        log.error("get_surrounding_points returned None for time=%s" % time)
        return 0
    
    # NEW: Handle identical points (flat segment at end)
    if next_point[0] == prev_point[0]:
        return prev_point[1]
    
    # ... rest of existing code
```

**Why This Solution:**
- Returns last segment for times at/past end
- Handles single-point profiles gracefully
- Adds defensive checks to prevent crashes
- Maintains existing behavior for normal cases

**Testing Strategy:**
```python
profile = Profile(json_data)
duration = profile.get_duration()

# Should not crash
temp1 = profile.get_target_temperature(duration)
temp2 = profile.get_target_temperature(duration + 1)

# Should return last segment temperature
assert temp1 == profile.data[-1][1]
assert temp2 == 0  # Over duration
```

**Files to Modify:**
- `lib/oven.py` lines 763-786

**Dependencies:** None

**Risk Assessment:** LOW - Pure defensive programming, doesn't change normal path

---

### 3. Ambiguous Return Value - `lib/oven.py:742-761`

**Priority:** CRITICAL - Seek start feature silently fails due to 0 being both error and valid time

**Root Cause:** Function returns 0 for both errors and time=0, making it impossible to distinguish

**Solution:** Return `None` for errors, update callers to check for `None`

**Implementation:**
```python
@staticmethod
def find_x_given_y_on_line_from_two_points(y, point1, point2):
    """
    Find x (time) given y (temperature) on a line defined by two points.
    
    Returns:
        float: Time value if successful
        None: If points are invalid, slope is non-positive, or y is out of range
    """
    # Validate point order
    if point1[0] > point2[0]:
        log.debug("Points in wrong order: time2 before time1")
        return None
    
    # Validate temperature slope (must be increasing)
    if point1[1] >= point2[1]:
        log.debug("Flat or negative temperature slope, cannot seek")
        return None
    
    # Check if y is in range
    if y < point1[1] or y > point2[1]:
        log.debug("Temperature %s outside segment range [%s, %s]" % 
                  (y, point1[1], point2[1]))
        return None
    
    # Calculate x
    x = (y - point1[1]) * (point2[0] - point1[0]) / (point2[1] - point1[1]) + point1[0]
    return x

def find_next_time_from_temperature(self, temperature):
    """Find the time when temperature is reached in the profile"""
    time = 0  # Default if no intersection found
    for index, point2 in enumerate(self.data):
        if point2[1] >= temperature:
            if index > 0:
                if self.data[index - 1][1] <= temperature:
                    result = self.find_x_given_y_on_line_from_two_points(
                        temperature, self.data[index - 1], point2)
                    
                    # CHANGED: Check for None instead of 0
                    if result is not None:
                        time = result
                        break
                    elif self.data[index - 1][1] == point2[1]:
                        # Flat segment that matches temperature
                        time = self.data[index - 1][0]
                        break
                    # else: result is None (error), keep time=0
    return time
```

**Why This Solution:**
- `None` is idiomatic Python for "no valid result"
- Makes error cases explicit
- Doesn't break existing behavior (0 is still returned for "not found")
- Adds logging for debugging

**Testing Strategy:**
```python
# Test error cases
assert find_x_given_y_on_line(100, (0,50), (10,50)) is None  # Flat
assert find_x_given_y_on_line(100, (10,100), (0,50)) is None  # Wrong order
assert find_x_given_y_on_line(200, (0,50), (10,100)) is None  # Out of range

# Test valid case
result = find_x_given_y_on_line(75, (0,50), (10,100))
assert result == 5.0
```

**Files to Modify:**
- `lib/oven.py` lines 742-761

**Dependencies:** None - only affects seek_start feature

**Risk Assessment:** LOW - Only affects optional seek feature, explicit error handling

---

### 4. Double Temperature Conversion - `kiln-controller.py:288-326`

**Priority:** CRITICAL - Can convert temperatures twice, causing wildly incorrect values

**Root Cause:** `add_temp_units()` assumes profiles without `temp_units` are in Fahrenheit if `config.temp_scale == "f"`, but they might already be in Celsius

**Current Behavior:**
1. Profile without `temp_units` loaded
2. `add_temp_units()` assumes it matches config
3. If called again, converts again

**Solution:** Establish clear conversion policy and make operations idempotent

**Policy Decision:**
1. **Storage format:** Store profiles in Fahrenheit by default (user's primary format)
2. **Display format:** Convert to user's preferred scale in memory only if needed
3. **Input assumption:** Profiles without `temp_units` are assumed to be in Fahrenheit (matches existing profiles)
4. **Thermocouple:** Reads in Celsius, converts to config scale automatically (leave unchanged)

**Implementation:**
```python
def add_temp_units(profile):
    """
    Ensures profile has temp_units field.
    
    Policy:
    - If profile already has temp_units, trust it (idempotent)
    - If profile lacks temp_units, assume it's in Fahrenheit (matches existing profiles)
    - This function only adds metadata, doesn't convert data
    """
    if "temp_units" not in profile:
        profile['temp_units'] = "f"  # Assume Fahrenheit (default storage format)
    return profile

def save_profile(profile, force=False):
    """Save profile to disk in Fahrenheit (standard format)"""
    # Ensure profile has temp_units
    profile = add_temp_units(profile)
    
    # Convert to Fahrenheit for storage if needed (maintain F as standard)
    if profile['temp_units'] == "c":
        profile = convert_to_f(profile)
        profile['temp_units'] = "f"
    
    profile_json = json.dumps(profile)
    filename = profile['name'] + ".json"
    filepath = os.path.join(profile_path, filename)
    
    if not force and os.path.exists(filepath):
        log.error("Could not write, %s already exists" % filepath)
        return False
    
    with open(filepath, 'w+') as f:
        f.write(profile_json)
    log.info("Wrote %s" % filepath)
    return True

def normalize_temp_units(profiles):
    """
    Convert profiles to display in config.temp_scale.
    Creates deep copies - doesn't modify originals.
    """
    import copy
    normalized = []
    
    for profile in profiles:
        # Deep copy to avoid modifying the original
        display_profile = copy.deepcopy(profile)
        
        # Ensure it has temp_units (defaults to F)
        display_profile = add_temp_units(display_profile)
        
        # Convert to match config for display if needed
        if config.temp_scale == "c" and display_profile["temp_units"] == "f":
            display_profile = convert_to_c(display_profile)
            display_profile["temp_units"] = "c"
        elif config.temp_scale == "f" and display_profile["temp_units"] == "c":
            display_profile = convert_to_f(display_profile)
            display_profile["temp_units"] = "f"
        
        normalized.append(display_profile)
    
    return normalized

def convert_to_c(profile):
    """Convert profile temperatures from Fahrenheit to Celsius"""
    # Only convert if not already in Celsius (idempotent)
    if profile.get("temp_units") == "c":
        return profile
    
    newdata = []
    for (secs, temp) in profile["data"]:
        temp_c = (temp - 32) * 5 / 9
        newdata.append((secs, temp_c))
    profile["data"] = newdata
    profile["temp_units"] = "c"
    return profile

def convert_to_f(profile):
    """Convert profile temperatures from Celsius to Fahrenheit"""
    # Only convert if not already in Fahrenheit (idempotent)
    if profile.get("temp_units") == "f":
        return profile
    
    newdata = []
    for (secs, temp) in profile["data"]:
        temp_f = (temp * 9 / 5) + 32
        newdata.append((secs, temp_f))
    profile["data"] = newdata
    profile["temp_units"] = "f"
    return profile
```

**Why This Solution:**
- Idempotent - calling multiple times is safe
- Clear separation: storage (always F) vs display (user preference)
- Uses deep copy to prevent accidental modification
- Backward compatible with existing profiles
- Matches user's existing Fahrenheit profiles

**Note on Thermocouple:**
The thermocouple hardware (MAX31855/MAX31856) reads in Celsius natively. The `get_temperature()` method in `lib/oven.py` (lines 138-154) automatically converts to Fahrenheit when `config.temp_scale == "f"`. This interface should NOT be changed - it's working correctly.

**Migration Plan:**
1. Add `temp_units` check to conversion functions (makes them idempotent)
2. Update `save_profile()` to always save in Fahrenheit (matches existing profiles)
3. Update `normalize_temp_units()` to use deep copies
4. Existing profiles without `temp_units` assumed to be Fahrenheit (matches current data)

**Testing Strategy:**
```python
# Test idempotency
profile = {"name": "test", "data": [(0, 400)], "temp_units": "c"}
profile1 = convert_to_c(profile)
profile2 = convert_to_c(profile1)
assert profile1["data"][0][1] == profile2["data"][0][1]

# Test double call safety
profile = {"name": "test", "data": [(0, 400)]}
profile = add_temp_units(profile)
temp1 = profile["data"][0][1]
profile = add_temp_units(profile)
temp2 = profile["data"][0][1]
assert temp1 == temp2
```

**Files to Modify:**
- `kiln-controller.py` lines 274-326

**Dependencies:** All profile loading/saving code

**Risk Assessment:** MEDIUM - Requires careful testing with existing profiles

**Migration Note:** Existing profiles without `temp_units` will be assumed to be in Fahrenheit (matches current usage). If user has C profiles without the field, they need to add `"temp_units": "c"` manually.

---

## High Priority Bugs (Fix Next)

### 5. Backwards Temperature Conversion - `kiln-tuner.py:195-196`

**Priority:** HIGH - PID tuning uses wrong target temperature

**Root Cause:** Logic is backwards - converts from F when config IS Celsius

**Current Code:**
```python
if config.temp_scale.lower() == "c":
    target = (target - 32)*5/9  # WRONG!
```

**Solution:** Fix logic to convert TO config scale when needed

**Implementation:**
```python
csvfile = "tuning.csv"
target = args.target_temp

# Assume user enters temperature in config.temp_scale
# No conversion needed by default

# ALTERNATIVE: If we want explicit input scale control
parser.add_argument('--input-scale', type=str, 
                    default=config.temp_scale,
                    choices=['c', 'f'],
                    help="Scale of input temperature (default: matches config)")

# Then convert if needed:
if args.input_scale.lower() == 'f' and config.temp_scale.lower() == 'c':
    target = (target - 32) * 5 / 9
elif args.input_scale.lower() == 'c' and config.temp_scale.lower() == 'f':
    target = (target * 9 / 5) + 32

tangentdivisor = args.tangent_divisor
```

**Why This Solution:**
- Explicit is better than implicit
- Allows users to override if needed
- Clear documentation of what's expected
- Defaults to sensible behavior (input matches config)

**Testing Strategy:**
```python
# With config in Celsius
config.temp_scale = "c"
args.target_temp = 400
args.input_scale = "c"
# Should NOT convert
assert target == 400

# With config in Celsius but input in F
args.input_scale = "f"
# Should convert to C
assert abs(target - 204.44) < 0.1
```

**Files to Modify:**
- `kiln-tuner.py` lines 186-197

**Dependencies:** None - standalone script

**Risk Assessment:** LOW - Script used infrequently, easy to verify

---

### 6. No File Locking - `lib/oven.py:489-510`

**Priority:** HIGH - Power failure during write corrupts state file

**Root Cause:** No atomic write or file locking; power failure mid-write corrupts file

**Solution:** Atomic write pattern (write to temp file, then rename)

**Implementation:**
```python
import fcntl  # Add at top of file (Unix/Linux)
import tempfile

def save_state(self):
    """Save state to file with atomic write"""
    try:
        # Write to temporary file in same directory (ensures same filesystem)
        temp_fd, temp_path = tempfile.mkstemp(
            dir=os.path.dirname(config.automatic_restart_state_file),
            prefix='.tmp_state_',
            suffix='.json'
        )
        
        with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
            # Optional: Lock the file for exclusive access
            # (not strictly needed with atomic rename, but doesn't hurt)
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            except (AttributeError, OSError):
                # fcntl not available on Windows, or locking not supported
                pass
            
            try:
                json.dump(self.get_state(), f, ensure_ascii=False, indent=4)
                f.flush()
                os.fsync(f.fileno())  # Force write to disk
            finally:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except (AttributeError, OSError):
                    pass
        
        # Atomic rename (overwrites old file if exists)
        # On POSIX systems, this is atomic
        os.replace(temp_path, config.automatic_restart_state_file)
        
    except Exception as e:
        log.error("Failed to save state: %s" % e)
        # Clean up temp file if it exists
        try:
            if 'temp_path' in locals():
                os.unlink(temp_path)
        except:
            pass

def should_i_automatic_restart(self):
    """Read state file with error handling"""
    if not config.automatic_restarts:
        return False
    
    if self.state_file_is_old():
        duplog.info("automatic restart not possible. state file does not exist or is too old.")
        return False
    
    try:
        with open(config.automatic_restart_state_file, 'r') as infile:
            # Optional: Shared read lock
            try:
                fcntl.flock(infile.fileno(), fcntl.LOCK_SH)
            except (AttributeError, OSError):
                pass
            
            try:
                d = json.load(infile)
            finally:
                try:
                    fcntl.flock(infile.fileno(), fcntl.LOCK_UN)
                except (AttributeError, OSError):
                    pass
        
        if d.get("state") != "RUNNING":
            duplog.info("automatic restart not possible. state = %s" % d.get("state"))
            return False
        
        return True
        
    except (IOError, ValueError, json.JSONDecodeError) as e:
        log.error("Failed to read state file: %s" % e)
        return False
```

**Why This Solution:**
- Write-to-temp-then-rename is atomic on POSIX systems
- No partial writes visible to readers
- Graceful degradation on Windows (still safer than before)
- File locking is optional bonus (main safety is atomic rename)

**Platform Considerations:**
- Linux/Unix: Fully atomic with `os.replace()`
- Windows: Nearly atomic (small race window but much better)
- Blinka boards (Linux-based): Full support

**Testing Strategy:**
```python
# Test atomic write
state_file = config.automatic_restart_state_file
original_content = open(state_file).read() if os.path.exists(state_file) else None

# Simulate write failure partway through
def test_interrupted_write():
    # Start write, kill process, verify old file intact or new file complete
    pass
```

**Files to Modify:**
- `lib/oven.py` lines 489-526

**Dependencies:** None

**Risk Assessment:** LOW - Standard pattern, well-tested

---

### 7. Multiple `if` Instead of `elif` - `kiln-controller.py:59-115`

**Priority:** HIGH - Multiple commands could execute from single request

**Root Cause:** Using `if` instead of `elif` checks all conditions even after match

**Solution:** Use `elif` with proper validation and error handling

**Implementation:**
```python
@app.post('/api')
def handle_api():
    log.info("/api command received")
    
    # Validate request has JSON
    if not bottle.request.json:
        return {"success": False, "error": "No JSON data provided"}
    
    # Validate cmd field exists
    if 'cmd' not in bottle.request.json:
        return {"success": False, "error": "No cmd field in request"}
    
    cmd = bottle.request.json['cmd']
    
    # Use elif for mutually exclusive commands
    if cmd == 'run':
        wanted = bottle.request.json.get('profile')
        if not wanted:
            return {"success": False, "error": "No profile specified"}
        
        log.info('api requested run of profile = %s' % wanted)
        
        # Start at a specific minute
        startat = bottle.request.json.get('startat', 0)
        
        # Shut off seek if start time has been set
        allow_seek = True
        if startat > 0:
            allow_seek = False
        
        profile = find_profile(wanted)
        if profile is None:
            return {"success": False, "error": "profile %s not found" % wanted}
        
        profile_json = json.dumps(profile)
        profile = Profile(profile_json)
        oven.run_profile(profile, startat=startat, allow_seek=allow_seek)
        ovenWatcher.record(profile)
        return {"success": True}
    
    elif cmd == 'pause':
        log.info("api pause command received")
        if oven.state == 'RUNNING':
            oven.state = 'PAUSED'
            return {"success": True}
        else:
            return {"success": False, "error": "Cannot pause, oven not running"}
    
    elif cmd == 'resume':
        log.info("api resume command received")
        if oven.state == 'PAUSED':
            oven.state = 'RUNNING'
            return {"success": True}
        else:
            return {"success": False, "error": "Cannot resume, oven not paused"}
    
    elif cmd == 'stop':
        log.info("api stop command received")
        oven.abort_run()
        return {"success": True}
    
    elif cmd == 'memo':
        log.info("api memo command received")
        memo = bottle.request.json.get('memo', '')
        log.info("memo=%s" % memo)
        return {"success": True}
    
    elif cmd == 'stats':
        log.info("api stats command received")
        if hasattr(oven, 'pid') and hasattr(oven.pid, 'pidstats'):
            return json.dumps(oven.pid.pidstats)
        else:
            return {"success": False, "error": "No PID stats available"}
    
    else:
        return {"success": False, "error": "Unknown command: %s" % cmd}
```

**Why This Solution:**
- `elif` ensures only one command executes
- Early returns prevent fall-through
- Validation at the top
- Proper error responses
- State checking for pause/resume

**Testing Strategy:**
```python
# Test only one command executes
response = api_call({"cmd": "pause"})  # With state = RUNNING
# Should pause, not continue to other commands

# Test error handling
response = api_call({"cmd": "pause"})  # With state = IDLE
assert response["success"] == False
```

**Files to Modify:**
- `kiln-controller.py` lines 59-115

**Dependencies:** None

**Risk Assessment:** LOW - Improves correctness without changing behavior

---

## Medium Priority Bugs (Fix Last)

### 8. PID Integral Windup - `lib/oven.py:833-836`

**Priority:** MEDIUM - Causes overshoot and oscillation

**Root Cause:** Integral term accumulates even when output is saturated

**Solution:** Anti-windup - stop accumulating integral when output is clamped

**Implementation:**
```python
else:
    # Proportional term
    p_term = self.kp * error
    
    # Derivative term
    dErr = (error - self.lastErr) / timeDelta
    d_term = self.kd * dErr
    
    # Calculate integral contribution
    i_contribution = error * timeDelta * (1/self.ki)
    
    # Calculate output before clamping
    output_unclamped = p_term + self.iterm + d_term
    
    # Clamp output to limits
    output = sorted([-1 * window_size, output_unclamped, window_size])[1]
    
    # Anti-windup: Only accumulate integral if output is not saturated
    # This prevents integral windup during saturation
    if output_unclamped == output:
        # Output is not saturated, safe to accumulate
        self.iterm += i_contribution
    else:
        # Output is saturated, don't accumulate more integral
        # Optional: Could also decay integral here
        log.debug("PID output saturated at %.2f, preventing integral windup" % output)
    
    out4logs = output
    output = float(output / window_size)
```

**Why This Solution:**
- Standard anti-windup technique
- Prevents overshoot from accumulated integral
- Only accumulates when PID has control authority
- Maintains existing behavior when not saturated

**Alternative Approaches:**
1. **Back-calculation:** Adjust integral based on saturation amount
2. **Conditional integration:** Only integrate when error is small
3. **Integral clamping:** Limit integral term directly

We chose simple conditional integration (method 1) as it's easiest to understand and sufficient for this application.

**Testing Strategy:**
```python
# Test with sustained large error (saturation)
pid = PID(ki=10, kp=1, kd=1)
for i in range(100):
    output = pid.compute(setpoint=1000, ispoint=100, now=now)
    # iterm should not grow unbounded
    assert pid.iterm < 200  # Some reasonable limit
```

**Files to Modify:**
- `lib/oven.py` lines 832-840

**Dependencies:** None

**Risk Assessment:** LOW-MEDIUM - Changes PID behavior but improves it

**Tuning Note:** After this change, may need to re-tune PID parameters. Integral gain can typically be increased without windup issues.

---

## Implementation Order

### Phase 1: Critical Safety Fixes (Days 1-2)
1. ✅ List modification during iteration (ovenWatcher.py)
2. ✅ Schedule completion crash (oven.py)
3. ✅ Temperature conversion bug (kiln-controller.py)

These three are safety-critical and could damage equipment.

### Phase 2: High Priority Fixes (Days 3-4)
4. ✅ Ambiguous return value (oven.py)
5. ✅ Backwards temp conversion in tuner (kiln-tuner.py)
6. ✅ Multiple if statements (kiln-controller.py)
7. ✅ File locking (oven.py)

These improve reliability and prevent data corruption.

### Phase 3: Quality Improvements (Day 5)
8. ✅ PID integral windup (oven.py)

This improves performance but isn't safety-critical.

---

## Testing Strategy

### Unit Tests to Add
1. **Temperature conversion tests**
   - Test idempotency
   - Test round-trip conversion
   - Test edge cases (0, negative, very high temps)

2. **Profile parsing tests**
   - Test single-point profiles
   - Test profiles at exact duration
   - Test seek with various slopes

3. **PID tests**
   - Test anti-windup behavior
   - Test saturation limits
   - Test normal operation

4. **API tests**
   - Test command validation
   - Test state transitions
   - Test error responses

### Integration Tests
1. **Full schedule run**
   - Start to finish without errors
   - Verify proper shutdown

2. **Temperature scale switching**
   - Load profiles, change config, reload
   - Verify no double conversion

3. **Automatic restart**
   - Simulate power failure during run
   - Verify clean recovery

### Manual Testing Checklist
- [ ] Run simulated kiln through full schedule
- [ ] Test pause/resume
- [ ] Test emergency shutdown
- [ ] Test seek start feature
- [ ] Test with both C and F configs
- [ ] Test automatic restart
- [ ] Test PID tuning script
- [ ] Test websocket connections/disconnections

---

## Rollback Plan

Each phase should be in a separate git branch:
- `fix/critical-safety-bugs`
- `fix/high-priority-bugs`
- `fix/quality-improvements`

If issues arise:
1. Revert specific commit
2. Or checkout previous branch
3. Keep fixes in separate commits for granular rollback

---

## Documentation Updates Needed

1. **README.md**
   - Update temperature scale handling documentation
   - Document automatic restart behavior
   - Document seek start behavior

2. **New Documentation**
   - Create TEMPERATURE_SCALES.md explaining conversion policy
   - Create TESTING.md with test procedures

3. **Code Comments**
   - Add docstrings to all modified functions
   - Explain non-obvious design decisions
   - Document thread safety considerations

---

## Backward Compatibility

### Breaking Changes
- **None expected** - All fixes maintain existing behavior for correct usage

### Migration Required
- **Profiles without temp_units:** Will be assumed to be in Celsius
- **Users with F profiles:** Need to add `"temp_units": "f"` to JSON manually or re-save through UI

### Migration Script
Provide optional migration script:
```python
# migrate_profiles.py
# Adds temp_units field to all profiles based on user input
```

---

## Risk Mitigation

1. **Test in simulation mode first**
2. **Create backup of profiles directory**
3. **Create backup of config.py**
4. **Test with non-critical test schedules**
5. **Keep original code available for comparison**
6. **Monitor logs closely during first real runs**

---

## Success Criteria

- [ ] All unit tests pass
- [ ] All integration tests pass
- [ ] Manual testing checklist complete
- [ ] No regressions in existing functionality
- [ ] Documentation updated
- [ ] Code review complete
- [ ] Successful simulation runs
- [ ] Successful real kiln run (if available)

---

## Post-Implementation

### Monitoring
- Watch logs for any new errors
- Monitor file system for state file corruption
- Check PID behavior for unusual oscillation

### Future Improvements
1. Add comprehensive unit test suite
2. Add continuous integration
3. Add linting/static analysis
4. Consider migrating to type hints (Python 3.5+)
5. Consider async/await for better concurrency

---

**Document Version:** 1.0  
**Created:** November 8, 2025  
**Status:** Ready for Implementation

