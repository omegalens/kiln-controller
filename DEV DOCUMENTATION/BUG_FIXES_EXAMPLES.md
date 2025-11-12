# Bug Fixes - Code Examples

This document shows specific code changes needed to fix the critical and high-priority bugs.

---

## Critical Bug #1: List Modification During Iteration

**File:** `lib/ovenWatcher.py` lines 83-91

### ❌ Current Code (BUGGY)
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
                self.observers.remove(wsock)  # BUG: modifying list during iteration
        else:
            self.observers.remove(wsock)  # BUG: modifying list during iteration
```

### ✅ Fixed Code (METHOD 1 - Iterate over copy)
```python
def notify_all(self, message):
    message_json = json.dumps(message)
    log.debug("sending to %d clients: %s"%(len(self.observers),message_json))
    
    for wsock in self.observers[:]:  # Iterate over a copy
        if wsock:
            try:
                wsock.send(message_json)
            except Exception as e:
                log.error("could not write to socket %s: %s" % (wsock, e))
                self.observers.remove(wsock)
        else:
            self.observers.remove(wsock)
```

### ✅ Fixed Code (METHOD 2 - Filter and rebuild)
```python
def notify_all(self, message):
    message_json = json.dumps(message)
    log.debug("sending to %d clients: %s"%(len(self.observers),message_json))
    
    failed_observers = []
    
    for wsock in self.observers:
        if not wsock:
            failed_observers.append(wsock)
            continue
            
        try:
            wsock.send(message_json)
        except Exception as e:
            log.error("could not write to socket %s: %s" % (wsock, e))
            failed_observers.append(wsock)
    
    # Remove all failed observers at once
    for wsock in failed_observers:
        self.observers.remove(wsock)
```

### ✅ Fixed Code (METHOD 3 - List comprehension, most Pythonic)
```python
def notify_all(self, message):
    message_json = json.dumps(message)
    log.debug("sending to %d clients: %s"%(len(self.observers),message_json))
    
    new_observers = []
    
    for wsock in self.observers:
        if not wsock:
            continue
            
        try:
            wsock.send(message_json)
            new_observers.append(wsock)  # Keep working connections
        except Exception as e:
            log.error("could not write to socket %s: %s" % (wsock, e))
            # Don't add to new_observers - effectively removed
    
    self.observers = new_observers
```

---

## Critical Bug #2: Schedule Completion Crash

**File:** `lib/oven.py` lines 763-786

### ❌ Current Code (BUGGY)
```python
def get_surrounding_points(self, time):
    if time > self.get_duration():
        return (None, None)
    
    prev_point = None
    next_point = None
    
    for i in range(len(self.data)):
        if time < self.data[i][0]:
            prev_point = self.data[i-1]
            next_point = self.data[i]
            break
    
    return (prev_point, next_point)  # BUG: both None if time == duration

def get_target_temperature(self, time):
    if time > self.get_duration():
        return 0
    
    (prev_point, next_point) = self.get_surrounding_points(time)
    
    # BUG: Crashes here if prev_point or next_point is None
    incl = float(next_point[1] - prev_point[1]) / float(next_point[0] - prev_point[0])
    temp = prev_point[1] + (time - prev_point[0]) * incl
    return temp
```

### ✅ Fixed Code
```python
def get_surrounding_points(self, time):
    if time > self.get_duration():
        return (None, None)
    
    # Edge case: if time equals or exceeds duration, return last two points
    if time >= self.data[-1][0]:
        if len(self.data) >= 2:
            return (self.data[-2], self.data[-1])
        else:
            return (self.data[0], self.data[0])  # Single point profile
    
    prev_point = None
    next_point = None
    
    for i in range(len(self.data)):
        if time < self.data[i][0]:
            prev_point = self.data[i-1]
            next_point = self.data[i]
            break
    
    return (prev_point, next_point)

def get_target_temperature(self, time):
    if time > self.get_duration():
        return 0
    
    (prev_point, next_point) = self.get_surrounding_points(time)
    
    # Handle None values (shouldn't happen with fixed get_surrounding_points)
    if prev_point is None or next_point is None:
        log.error("get_surrounding_points returned None for time=%s" % time)
        return 0
    
    # Handle identical points (flat segment at end)
    if next_point[0] == prev_point[0]:
        return prev_point[1]
    
    incl = float(next_point[1] - prev_point[1]) / float(next_point[0] - prev_point[0])
    temp = prev_point[1] + (time - prev_point[0]) * incl
    return temp
```

---

## Critical Bug #3: Ambiguous Return Value

**File:** `lib/oven.py` lines 742-747

### ❌ Current Code (BUGGY)
```python
@staticmethod
def find_x_given_y_on_line_from_two_points(y, point1, point2):
    if point1[0] > point2[0]: return 0  # time2 before time1 makes no sense
    if point1[1] >= point2[1]: return 0 # Zero will crach. Negative temperature slope
    x = (y - point1[1]) * (point2[0] - point1[0]) / (point2[1] - point1[1]) + point1[0]
    return x
```

### ✅ Fixed Code
```python
@staticmethod
def find_x_given_y_on_line_from_two_points(y, point1, point2):
    """
    Find x (time) given y (temperature) on a line defined by two points.
    
    Returns None if:
    - Points are in wrong order (time goes backwards)
    - Line is flat or slopes downward (can't solve for y)
    - y is outside the range defined by the two points
    
    Otherwise returns the time value.
    """
    # Validate point order
    if point1[0] > point2[0]:
        log.warning("Points in wrong order: time2 before time1")
        return None
    
    # Validate temperature slope
    if point1[1] >= point2[1]:
        # Flat or negative slope - can't uniquely solve for time
        # This is OK for cooling segments, but we can't seek to them
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
```

### Also Update the Caller (line 749-761)
```python
def find_next_time_from_temperature(self, temperature):
    time = 0  # The seek function will not do anything if this returns zero
    for index, point2 in enumerate(self.data):
        if point2[1] >= temperature:
            if index > 0:
                if self.data[index - 1][1] <= temperature:
                    time = self.find_x_given_y_on_line_from_two_points(
                        temperature, self.data[index - 1], point2)
                    
                    # CHANGE: Check for None instead of 0
                    if time is None:
                        if self.data[index - 1][1] == point2[1]:
                            time = self.data[index - 1][0]
                        else:
                            time = 0  # Error case, don't seek
                    break
    return time
```

---

## Critical Bug #4: Double Temperature Conversion

**File:** `kiln-controller.py` lines 288-308

### ❌ Current Code (BUGGY)
```python
def add_temp_units(profile):
    """
    always store the temperature in degrees c
    this way folks can share profiles
    """
    if "temp_units" in profile:
        return profile  # BUG: Already has units, but are they correct?
    profile['temp_units']="c"
    if config.temp_scale=="c":
        return profile
    if config.temp_scale=="f":
        profile=convert_to_c(profile);  # BUG: Assumes profile is in F
        return profile

def convert_to_c(profile):
    newdata=[]
    for (secs,temp) in profile["data"]:
        temp = (5/9)*(temp-32)  # Converts F to C
        newdata.append((secs,temp))
    profile["data"]=newdata
    return profile
```

**Problem:** If a profile is loaded that's already in Celsius but doesn't have `temp_units` field, and `config.temp_scale` is "f", it will convert Celsius to... garbage.

### ✅ Fixed Code
```python
def add_temp_units(profile):
    """
    Ensures profile has temp_units field set correctly.
    
    ASSUMPTIONS:
    - If profile has temp_units field, trust it
    - If profile lacks temp_units field, assume it matches config.temp_scale
    - Always store profiles on disk in Celsius for portability
    - Convert to config.temp_scale for display only (done in normalize_temp_units)
    """
    if "temp_units" in profile:
        # Profile already has units specified, don't modify it
        return profile
    
    # No units specified - assume profile is in same units as config
    # and mark it as such
    profile['temp_units'] = config.temp_scale
    
    # If we're configured for Fahrenheit but want to store as Celsius,
    # convert now
    if config.temp_scale == "f":
        profile = convert_to_c(profile)
        profile['temp_units'] = "c"
    
    return profile

def convert_to_c(profile):
    """Convert profile temperatures from Fahrenheit to Celsius"""
    newdata = []
    for (secs, temp) in profile["data"]:
        temp_c = (temp - 32) * 5 / 9
        newdata.append((secs, temp_c))
    profile["data"] = newdata
    return profile

def convert_to_f(profile):
    """Convert profile temperatures from Celsius to Fahrenheit"""
    newdata = []
    for (secs, temp) in profile["data"]:
        temp_f = (temp * 9 / 5) + 32
        newdata.append((secs, temp_f))
    profile["data"] = newdata
    return profile

def normalize_temp_units(profiles):
    """
    Convert profiles to display in config.temp_scale.
    This creates copies for display - doesn't modify originals.
    """
    normalized = []
    for profile in profiles:
        # Make a deep copy so we don't modify the original
        import copy
        display_profile = copy.deepcopy(profile)
        
        if "temp_units" not in display_profile:
            # Assume it's in Celsius (our storage standard)
            display_profile["temp_units"] = "c"
        
        # Convert to match config for display
        if config.temp_scale == "f" and display_profile["temp_units"] == "c":
            display_profile = convert_to_f(display_profile)
            display_profile["temp_units"] = "f"
        elif config.temp_scale == "c" and display_profile["temp_units"] == "f":
            display_profile = convert_to_c(display_profile)
            display_profile["temp_units"] = "c"
        
        normalized.append(display_profile)
    
    return normalized
```

---

## High Priority Bug #5: Backwards Temperature Conversion

**File:** `kiln-tuner.py` lines 192-196

### ❌ Current Code (BUGGY)
```python
csvfile = "tuning.csv"
target = args.target_temp
if config.temp_scale.lower() == "c":
    target = (target - 32)*5/9  # BUG: If scale is C, why convert FROM F?
tangentdivisor = args.tangent_divisor
```

**Problem:** Logic is backwards. If config is set to Celsius, the user probably entered Celsius, so don't convert.

### ✅ Fixed Code
```python
csvfile = "tuning.csv"
target = args.target_temp

# Kiln controller internally works in config.temp_scale
# User enters target in their preferred scale (could add --scale argument)
# For now, assume user enters in same scale as config

# If we want to always work internally in Celsius:
if config.temp_scale.lower() == "f":
    # User entered F, but we work in C internally
    target = (target - 32) * 5 / 9
# else: user entered C, keep as-is

tangentdivisor = args.tangent_divisor
```

**OR, better yet:**
```python
csvfile = "tuning.csv"
target = args.target_temp

# Add argument to specify input temperature scale
parser.add_argument('--input_scale', type=str, default=config.temp_scale, 
                    choices=['c', 'f'],
                    help="Scale of the input temperature (default: matches config)")

# Then in main:
args = parser.parse_args()
target = args.target_temp

# Convert input to config scale if needed
if args.input_scale.lower() == 'f' and config.temp_scale.lower() == 'c':
    target = (target - 32) * 5 / 9
elif args.input_scale.lower() == 'c' and config.temp_scale.lower() == 'f':
    target = (target * 9 / 5) + 32
```

---

## High Priority Bug #6: Multiple If Instead of Elif

**File:** `kiln-controller.py` lines 59-115

### ❌ Current Code (BUGGY)
```python
@app.post('/api')
def handle_api():
    log.info("/api is alive")
    
    # All of these are checked even if one matches!
    if bottle.request.json['cmd'] == 'run':
        # ... handle run ...
        # No return!
    
    if bottle.request.json['cmd'] == 'pause':
        log.info("api pause command received")
        oven.state = 'PAUSED'
        # No return!
    
    if bottle.request.json['cmd'] == 'resume':
        log.info("api resume command received")
        oven.state = 'RUNNING'
        # No return!
    
    # ... more if statements ...
    
    return { "success" : True }
```

### ✅ Fixed Code
```python
@app.post('/api')
def handle_api():
    log.info("/api command received")
    
    # Validate request
    if not bottle.request.json:
        return { "success": False, "error": "No JSON data provided" }
    
    if 'cmd' not in bottle.request.json:
        return { "success": False, "error": "No cmd field in request" }
    
    cmd = bottle.request.json['cmd']
    
    # Use elif for mutually exclusive commands
    if cmd == 'run':
        wanted = bottle.request.json.get('profile')
        if not wanted:
            return { "success": False, "error": "No profile specified" }
        
        log.info('api requested run of profile = %s' % wanted)
        
        startat = bottle.request.json.get('startat', 0)
        allow_seek = True
        if startat > 0:
            allow_seek = False
        
        profile = find_profile(wanted)
        if profile is None:
            return { "success": False, "error": "profile %s not found" % wanted }
        
        profile_json = json.dumps(profile)
        profile = Profile(profile_json)
        oven.run_profile(profile, startat=startat, allow_seek=allow_seek)
        ovenWatcher.record(profile)
        return { "success": True }
    
    elif cmd == 'pause':
        log.info("api pause command received")
        if oven.state == 'RUNNING':
            oven.state = 'PAUSED'
            return { "success": True }
        else:
            return { "success": False, "error": "Cannot pause, not running" }
    
    elif cmd == 'resume':
        log.info("api resume command received")
        if oven.state == 'PAUSED':
            oven.state = 'RUNNING'
            return { "success": True }
        else:
            return { "success": False, "error": "Cannot resume, not paused" }
    
    elif cmd == 'stop':
        log.info("api stop command received")
        oven.abort_run()
        return { "success": True }
    
    elif cmd == 'memo':
        log.info("api memo command received")
        memo = bottle.request.json.get('memo', '')
        log.info("memo=%s" % memo)
        return { "success": True }
    
    elif cmd == 'stats':
        log.info("api stats command received")
        if hasattr(oven, 'pid') and hasattr(oven.pid, 'pidstats'):
            return json.dumps(oven.pid.pidstats)
        else:
            return { "success": False, "error": "No PID stats available" }
    
    else:
        return { "success": False, "error": "Unknown command: %s" % cmd }
```

---

## High Priority Bug #7: No File Locking

**File:** `lib/oven.py` lines 489-510

### ❌ Current Code (BUGGY)
```python
def save_state(self):
    with open(config.automatic_restart_state_file, 'w', encoding='utf-8') as f:
        json.dump(self.get_state(), f, ensure_ascii=False, indent=4)
        # BUG: No locking - if power fails during write, file corrupted
```

### ✅ Fixed Code
```python
import fcntl  # Add at top of file

def save_state(self):
    """Save state to file with file locking for atomic writes"""
    try:
        # Write to temporary file first
        temp_file = config.automatic_restart_state_file + '.tmp'
        
        with open(temp_file, 'w', encoding='utf-8') as f:
            # Lock the file for exclusive access
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                json.dump(self.get_state(), f, ensure_ascii=False, indent=4)
                f.flush()
                os.fsync(f.fileno())  # Force write to disk
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        
        # Atomic rename (overwrites old file)
        os.replace(temp_file, config.automatic_restart_state_file)
        
    except Exception as e:
        log.error("Failed to save state: %s" % e)

def should_i_automatic_restart(self):
    """Read state file with proper locking and error handling"""
    if not config.automatic_restarts:
        return False
    
    if self.state_file_is_old():
        duplog.info("automatic restart not possible. state file does not exist or is too old.")
        return False
    
    try:
        with open(config.automatic_restart_state_file, 'r') as infile:
            # Lock file for shared read access
            fcntl.flock(infile.fileno(), fcntl.LOCK_SH)
            try:
                d = json.load(infile)
            finally:
                fcntl.flock(infile.fileno(), fcntl.LOCK_UN)
        
        if d.get("state") != "RUNNING":
            duplog.info("automatic restart not possible. state = %s" % d.get("state"))
            return False
        
        return True
        
    except (IOError, ValueError, json.JSONDecodeError) as e:
        log.error("Failed to read state file: %s" % e)
        return False
```

**Note:** For Windows compatibility, use `msvcrt` instead of `fcntl`:
```python
import platform
if platform.system() == 'Windows':
    import msvcrt
else:
    import fcntl
```

---

## Medium Priority: PID Integral Windup

**File:** `lib/oven.py` lines 833-836

### ❌ Current Code (BUGGY)
```python
else:
    icomp = (error * timeDelta * (1/self.ki))
    self.iterm += (error * timeDelta * (1/self.ki))
    dErr = (error - self.lastErr) / timeDelta
    output = self.kp * error + self.iterm + self.kd * dErr
    output = sorted([-1 * window_size, output, window_size])[1]
    # BUG: output is clamped but self.iterm is not
```

### ✅ Fixed Code
```python
else:
    # Proportional term
    p_term = self.kp * error
    
    # Integral term with anti-windup
    i_contribution = error * timeDelta * (1/self.ki)
    
    # Derivative term
    dErr = (error - self.lastErr) / timeDelta
    d_term = self.kd * dErr
    
    # Calculate output before clamping
    output = p_term + self.iterm + d_term
    
    # Clamp output
    output = sorted([-1 * window_size, output, window_size])[1]
    
    # Only add to integral if output is not saturated
    # This prevents integral windup
    if -1 * window_size < output < window_size:
        self.iterm += i_contribution
    else:
        # Output is saturated, don't accumulate more integral
        log.debug("PID output saturated, preventing integral windup")
```

---

## Testing Recommendations

After applying these fixes, test thoroughly:

1. **Temperature Conversion Test:**
   ```python
   # Test that profiles don't get double-converted
   profile = load_profile("test.json")
   temp1 = profile["data"][0][1]
   
   profile = add_temp_units(profile)
   profile = add_temp_units(profile)  # Call twice
   temp2 = profile["data"][0][1]
   
   assert temp1 == temp2, "Double conversion detected!"
   ```

2. **Schedule Completion Test:**
   ```python
   profile = Profile(json_data)
   duration = profile.get_duration()
   
   # Should not crash
   temp = profile.get_target_temperature(duration)
   temp = profile.get_target_temperature(duration + 1)
   ```

3. **WebSocket Observer Test:**
   ```python
   watcher = OvenWatcher(oven)
   
   # Add mock observers, some that will fail
   watcher.observers = [good_sock1, bad_sock, good_sock2, None, bad_sock2]
   
   watcher.notify_all({"test": "message"})
   
   # Should have only good sockets left
   assert len(watcher.observers) == 2
   ```

---

**Document Version:** 1.0  
**Last Updated:** November 6, 2025

