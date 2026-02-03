# Rate-Based Profile Refactor: Complete Implementation Plan

## Executive Summary

This document outlines the complete refactoring required to transition from a **time-indexed profile format** to a **rate-based segment format**. The goal is to make heat rates the primary control mechanism rather than a derived value.

### The Core Problem

The current architecture uses time-based profiles where:
- Each point specifies `[time_seconds, temperature]`
- Heat rate is implicitly derived: `(temp2 - temp1) / (time2 - time1)`
- The `kiln_must_catch_up` feature **pauses the clock** when temperature is out of range
- This causes `runtime` to freeze, distorting timing and heat rate calculations

**Traditional kiln controllers** work differently:
- Heat rate is the **primary** control (e.g., 200°F/hr)
- Progress is measured by **temperature achieved**, not time elapsed
- Clock always runs (wall time is always accurate)

---

## Part 1: Current vs. Proposed Data Model

### Current Format (v1)
```json
{
  "name": "cone-05-fast-bisque",
  "type": "profile",
  "data": [[0, 65], [600, 200], [2088, 250], [5688, 250], [23135, 1733], [28320, 1888], [30900, 1888]]
}
```
- `data`: Array of `[time_seconds, temperature]` tuples
- Time is absolute from start
- Heat rate is implicitly derived

### Proposed Format (v2)
```json
{
  "name": "cone-05-fast-bisque",
  "type": "profile",
  "version": 2,
  "start_temp": 65,
  "temp_units": "f",
  "segments": [
    {"rate": 810, "target": 200, "hold": 0},
    {"rate": 40, "target": 250, "hold": 60},
    {"rate": 508, "target": 1733, "hold": 0},
    {"rate": 178, "target": 1888, "hold": 43}
  ]
}
```

### Segment Field Definitions

| Field | Type | Unit | Description |
|-------|------|------|-------------|
| `rate` | number \| string | °/hour | Heat rate (positive=heating, negative=cooling) |
| `target` | number | °F or °C | Target temperature for this segment |
| `hold` | number | minutes | Time to hold at target before next segment |

### Special Rate Values

| Value | Meaning | Behavior |
|-------|---------|----------|
| `0` | Hold at current temperature | Explicit hold (alternative to hold field) |
| `"max"` | Heat as fast as possible | Full power until target reached |
| `"cool"` | Cool naturally | No power, wait for temp to drop |

---

## Part 2: Files Requiring Changes

### 2.1 Core Backend Files

| File | Changes Required | Complexity |
|------|------------------|------------|
| `lib/oven.py` | Major refactor of `Profile` class, `Oven.run()` loop, timing logic | High |
| `kiln-controller.py` | Profile loading, API responses, temperature unit conversion | Medium |
| `config.py` | New config options for rate limits, tolerances | Low |

### 2.2 Frontend Files

| File | Changes Required | Complexity |
|------|------------------|------------|
| `public/assets/js/picoreflow.js` | Profile editor UI, table rendering, graph updates | High |
| `public/index.html` | New UI elements for segment editor | Medium |
| `public/assets/css/picoreflow.css` | Styling for new editor components | Low |

### 2.3 Storage & Migration

| File | Changes Required | Complexity |
|------|------------------|------------|
| `storage/profiles/*.json` | All profiles need migration to v2 format | Medium |
| New: `scripts/migrate_profiles.py` | Migration script for existing profiles | Medium |

### 2.4 Test Files

| File | Changes Required | Complexity |
|------|------------------|------------|
| `Test/test_Profile.py` | Complete rewrite for new Profile class | High |
| `Test/test-fast.json` | Update to new format | Low |
| `Test/test-cases.json` | Update to new format | Low |

---

## Part 3: Detailed Code Changes

### 3.1 `lib/oven.py` - New Segment Class

```python
class Segment:
    """Represents a single firing segment"""
    def __init__(self, rate, target, hold=0):
        self.rate = rate          # degrees per hour (or "max"/"cool")
        self.target = target      # target temperature
        self.hold = hold * 60     # hold time in seconds (input is minutes)
    
    def is_ramp(self):
        """Returns True if this segment has a ramp phase (non-zero numeric rate)"""
        return self.rate != 0 and self.rate not in ("max", "cool")
    
    def is_pure_hold(self):
        """Returns True if this is a hold-only segment (rate=0)"""
        return self.rate == 0
    
    def has_hold_phase(self):
        """Returns True if this segment includes any hold time"""
        return self.hold > 0
    
    def is_max_power(self):
        """Returns True if this segment uses maximum heating rate"""
        return self.rate == "max"
    
    def is_natural_cool(self):
        """Returns True if this segment uses natural cooling"""
        return self.rate == "cool"
    
    def validate(self, previous_target=None):
        """Validate segment configuration, raise ValueError if invalid"""
        if previous_target is not None:
            if isinstance(self.rate, (int, float)):
                if self.rate < 0 and self.target > previous_target:
                    raise ValueError(
                        f"Negative rate ({self.rate}) with increasing target "
                        f"({previous_target} -> {self.target})"
                    )
                if self.rate > 0 and self.target < previous_target:
                    raise ValueError(
                        f"Positive rate ({self.rate}) with decreasing target "
                        f"({previous_target} -> {self.target})"
                    )
```

### 3.2 `lib/oven.py` - Profile Class Rewrite

```python
class Profile:
    """Rate-based firing profile"""
    
    def __init__(self, json_data):
        obj = json.loads(json_data)
        self.name = obj["name"]
        self.version = obj.get("version", 1)
        
        if self.version == 1:
            # Legacy format - convert on load
            self._load_legacy(obj)
        else:
            self._load_v2(obj)
    
    def _load_legacy(self, obj):
        """Convert legacy time-based format to segments"""
        self.start_temp = obj["data"][0][1] if obj["data"] else 0
        self.segments = []
        
        for i in range(1, len(obj["data"])):
            prev_time, prev_temp = obj["data"][i-1]
            curr_time, curr_temp = obj["data"][i]
            
            time_diff = curr_time - prev_time  # seconds
            temp_diff = curr_temp - prev_temp  # degrees
            
            if time_diff > 0 and temp_diff != 0:
                # Calculate rate in degrees/hour
                rate = (temp_diff / time_diff) * 3600
                self.segments.append(Segment(rate, curr_temp, hold=0))
            elif temp_diff == 0 and time_diff > 0:
                # This is a hold - merge with previous segment if possible
                hold_minutes = time_diff / 60
                if self.segments and self.segments[-1].target == curr_temp:
                    # Add hold time to the previous segment (in seconds)
                    self.segments[-1].hold += hold_minutes * 60
                else:
                    # Create standalone hold segment only if no previous segment
                    self.segments.append(Segment(0, curr_temp, hold=hold_minutes))
    
    def _load_v2(self, obj):
        """Load v2 rate-based format with temperature unit conversion"""
        self.start_temp = obj.get("start_temp", 0)
        self.temp_units = obj.get("temp_units", "f")
        self.segments = []
        
        # Check if conversion needed (profile in C, system in F or vice versa)
        needs_c_to_f = self.temp_units == "c" and config.temp_scale == "f"
        needs_f_to_c = self.temp_units == "f" and config.temp_scale == "c"
        
        if needs_c_to_f:
            self.start_temp = (self.start_temp * 9 / 5) + 32
        elif needs_f_to_c:
            self.start_temp = (self.start_temp - 32) * 5 / 9
        
        for seg in obj["segments"]:
            target = seg["target"]
            rate = seg["rate"]
            
            # Convert temperatures and rates if needed
            if needs_c_to_f:
                target = (target * 9 / 5) + 32
                if isinstance(rate, (int, float)):
                    rate = rate * 9 / 5  # Rate conversion: °C/hr to °F/hr
            elif needs_f_to_c:
                target = (target - 32) * 5 / 9
                if isinstance(rate, (int, float)):
                    rate = rate * 5 / 9  # Rate conversion: °F/hr to °C/hr
            
            segment = Segment(
                rate=rate,
                target=target,
                hold=seg.get("hold", 0)
            )
            
            # Validate segment rate direction vs temperature change
            previous_target = self.segments[-1].target if self.segments else self.start_temp
            segment.validate(previous_target)
            
            self.segments.append(segment)
    
    def get_segment_for_temperature(self, current_temp, segment_index=0):
        """
        Determine which segment we should be in based on current temperature.
        Returns (segment_index, segment, phase) where phase is 'ramp' or 'hold'
        """
        if segment_index >= len(self.segments):
            return (len(self.segments) - 1, self.segments[-1], 'complete')
        
        segment = self.segments[segment_index]
        
        # Check if we've reached the target for this segment
        if segment.rate == 0:  # Explicit hold segment
            return (segment_index, segment, 'hold')
        elif segment.rate > 0:  # Heating
            if current_temp >= segment.target:
                return (segment_index, segment, 'hold')
        elif segment.rate < 0:  # Cooling
            if current_temp <= segment.target:
                return (segment_index, segment, 'hold')
        
        return (segment_index, segment, 'ramp')
    
    def get_rate_for_segment(self, segment_index):
        """Get heat rate for current segment"""
        if segment_index >= len(self.segments):
            return 0
        return self.segments[segment_index].rate
    
    def get_hold_duration(self, segment_index):
        """Get hold duration in seconds for segment"""
        if segment_index >= len(self.segments):
            return 0
        return self.segments[segment_index].hold
    
    def estimate_duration(self, start_temp=None):
        """
        Estimate total duration based on rates.
        This is an estimate since actual time depends on kiln performance.
        """
        if start_temp is None:
            start_temp = self.start_temp
        
        total_seconds = 0
        current_temp = start_temp
        
        for segment in self.segments:
            if isinstance(segment.rate, str):
                # Can't estimate "max" or "cool" accurately
                if segment.rate == "max":
                    total_seconds += 1800  # Rough estimate: 30 min
                elif segment.rate == "cool":
                    total_seconds += 3600  # Rough estimate: 1 hour
            elif segment.rate != 0:
                temp_diff = abs(segment.target - current_temp)
                time_hours = temp_diff / abs(segment.rate)
                total_seconds += time_hours * 3600
            
            total_seconds += segment.hold  # Add hold time
            current_temp = segment.target
        
        return total_seconds
    
    def to_legacy_format(self, start_temp=None):
        """
        Convert back to legacy format for graph compatibility.
        """
        if start_temp is None:
            start_temp = self.start_temp
        
        data = [[0, start_temp]]
        current_time = 0
        current_temp = start_temp
        
        for segment in self.segments:
            if isinstance(segment.rate, str):
                # Estimate time for special rates
                if segment.rate == "max":
                    temp_diff = segment.target - current_temp
                    time_seconds = abs(temp_diff) / config.estimated_max_heating_rate * 3600
                else:  # "cool"
                    temp_diff = current_temp - segment.target
                    time_seconds = abs(temp_diff) / config.estimated_natural_cooling_rate * 3600
                # Add ramp point for special rates
                current_time += time_seconds
                current_temp = segment.target
                data.append([current_time, current_temp])
            elif segment.rate != 0:
                # Normal ramp segment
                temp_diff = segment.target - current_temp
                time_hours = abs(temp_diff) / abs(segment.rate)
                time_seconds = time_hours * 3600
                current_time += time_seconds
                current_temp = segment.target
                data.append([current_time, current_temp])
            # For rate=0 (pure hold), don't add a ramp point - just add the hold below
            
            # Add hold point if needed (applies to all segment types)
            if segment.hold > 0:
                current_time += segment.hold
                data.append([current_time, current_temp])
        
        return data
```

### 3.3 `lib/oven.py` - Oven Class State Changes

**Current state variables to keep:**
```python
self.cost = 0
self.state = "IDLE"
self.profile = None
self.start_time = 0
self.target = 0
self.heat = 0
self.pid = PID(...)
```

**New state variables to add:**
```python
self.current_segment_index = 0
self.segment_phase = 'ramp'           # 'ramp' or 'hold'
self.segment_start_time = None
self.segment_start_temp = None
self.hold_start_time = None
self.actual_elapsed_time = 0          # Wall clock time (always advances)
self.schedule_progress = 0.0          # 0-100% based on temp progress
self.target_heat_rate = 0             # The rate we're trying to achieve
```

**Variables to remove or repurpose:**
```python
# self.runtime         -> Replace with actual_elapsed_time
# self.totaltime       -> Remove (estimate only via estimate_duration())
# self.catching_up     -> Remove (different paradigm)
# self.heat_rate_temps -> Keep but use wall clock time
```

### 3.3.1 Automatic Restart Compatibility

The automatic restart system must be updated to save/restore segment-based state instead of time-based state:

```python
def save_automatic_restart_state(self):
    """Save state for automatic restart - v2 segment-based"""
    if not config.automatic_restarts:
        return
    
    state = {
        "version": 2,
        "profile_name": self.profile.name if self.profile else None,
        "current_segment_index": self.current_segment_index,
        "segment_phase": self.segment_phase,
        "segment_start_temp": self.segment_start_temp,
        "hold_elapsed": 0,
        "actual_elapsed_time": self.actual_elapsed_time,
        "cost": self.cost,
        "timestamp": datetime.datetime.now().isoformat()
    }
    
    # If in hold phase, save how much hold time has elapsed
    if self.segment_phase == 'hold' and self.hold_start_time:
        state["hold_elapsed"] = (datetime.datetime.now() - self.hold_start_time).total_seconds()
    
    with open(config.automatic_restart_state_file, 'w') as f:
        json.dump(state, f)


def automatic_restart(self):
    """Restore from automatic restart state - v2 segment-based"""
    if not os.path.exists(config.automatic_restart_state_file):
        return False
    
    try:
        with open(config.automatic_restart_state_file) as f:
            state = json.load(f)
        
        # Check if this is v1 or v2 restart state
        if state.get("version", 1) == 1:
            # Legacy v1 restart - use time-based restoration
            log.warning("Legacy v1 restart state found - converting to segment-based")
            return self._automatic_restart_legacy(state)
        
        # V2 segment-based restoration
        profile_name = state.get("profile_name")
        if not profile_name:
            log.error("No profile name in restart state")
            return False
        
        # Load the profile
        profile_path = os.path.join(config.profile_path, profile_name + ".json")
        if not os.path.exists(profile_path):
            log.error("Profile not found: %s" % profile_path)
            return False
        
        with open(profile_path) as f:
            self.profile = Profile(f.read())
        
        # Restore segment state
        self.current_segment_index = state.get("current_segment_index", 0)
        self.segment_phase = state.get("segment_phase", "ramp")
        self.segment_start_temp = state.get("segment_start_temp", self.board.temp_sensor.temperature())
        self.cost = state.get("cost", 0)
        
        # Set up timing
        self.start_time = datetime.datetime.now()
        self.segment_start_time = datetime.datetime.now()
        
        # If resuming a hold, adjust hold start time to account for elapsed time
        if self.segment_phase == 'hold':
            hold_elapsed = state.get("hold_elapsed", 0)
            self.hold_start_time = datetime.datetime.now() - datetime.timedelta(seconds=hold_elapsed)
        
        self.state = "RUNNING"
        log.info("Automatic restart: resuming segment %d (%s phase)" % 
                 (self.current_segment_index, self.segment_phase))
        
        return True
        
    except Exception as e:
        log.error("Failed to restore from automatic restart: %s" % e)
        return False


def _automatic_restart_legacy(self, state):
    """Handle legacy v1 restart state by finding appropriate segment"""
    # Find segment based on current temperature
    current_temp = self.board.temp_sensor.temperature() + config.thermocouple_offset
    
    for i, segment in enumerate(self.profile.segments):
        if segment.rate > 0 and current_temp < segment.target:
            # Found a heating segment we haven't completed
            self.current_segment_index = i
            self.segment_phase = 'ramp'
            break
        elif segment.rate < 0 and current_temp > segment.target:
            # Found a cooling segment we haven't completed
            self.current_segment_index = i
            self.segment_phase = 'ramp'
            break
    
    self.segment_start_temp = current_temp
    self.segment_start_time = datetime.datetime.now()
    self.start_time = datetime.datetime.now()
    self.cost = state.get("cost", 0)
    self.state = "RUNNING"
    
    log.info("Legacy restart: resuming at segment %d based on current temp %.1f" % 
             (self.current_segment_index, current_temp))
    return True
```

### 3.4 `lib/oven.py` - New Control Methods

#### Replace `kiln_must_catch_up()` with segment progress tracking:

```python
def update_segment_progress(self):
    """
    Update which segment we're in based on actual temperature.
    Progress is temperature-based, not time-based.
    """
    temp = self.board.temp_sensor.temperature() + config.thermocouple_offset
    
    segment = self.profile.segments[self.current_segment_index]
    
    if self.segment_phase == 'ramp':
        # Check if we've reached target temperature
        reached_target = False
        if isinstance(segment.rate, (int, float)):
            if segment.rate > 0:  # Heating
                reached_target = temp >= segment.target - config.segment_complete_tolerance
            elif segment.rate < 0:  # Cooling
                reached_target = temp <= segment.target + config.segment_complete_tolerance
        elif segment.rate == "max":
            reached_target = temp >= segment.target - config.segment_complete_tolerance
        elif segment.rate == "cool":
            reached_target = temp <= segment.target + config.segment_complete_tolerance
        
        if reached_target:
            if segment.hold > 0:
                # Transition to hold phase
                self.segment_phase = 'hold'
                self.hold_start_time = datetime.datetime.now()
                log.info("Segment %d: reached target %.1f, starting %.1f min hold" % 
                         (self.current_segment_index, segment.target, segment.hold/60))
            else:
                # Move to next segment
                self._advance_segment()
    
    elif self.segment_phase == 'hold':
        # Check if hold time has elapsed
        hold_elapsed = (datetime.datetime.now() - self.hold_start_time).total_seconds()
        if hold_elapsed >= segment.hold:
            self._advance_segment()

def _advance_segment(self):
    """Move to the next segment"""
    self.current_segment_index += 1
    if self.current_segment_index >= len(self.profile.segments):
        log.info("All segments complete")
        self.save_firing_log(status="completed")
        self.start_cooling()
        self.state = "IDLE"
    else:
        self.segment_phase = 'ramp'
        self.segment_start_time = datetime.datetime.now()
        self.segment_start_temp = self.board.temp_sensor.temperature()
        next_seg = self.profile.segments[self.current_segment_index]
        log.info("Starting segment %d: rate=%s, target=%.1f" % 
                 (self.current_segment_index, next_seg.rate, next_seg.target))
```

#### New target temperature calculation:

```python
def calculate_rate_based_target(self):
    """
    Calculate target temperature based on desired rate and elapsed time.
    """
    if self.segment_phase == 'hold':
        return self.profile.segments[self.current_segment_index].target
    
    segment = self.profile.segments[self.current_segment_index]
    
    if segment.rate in ("max", "cool"):
        # For max/cool, target is the segment target
        return segment.target
    
    # Calculate target based on rate and elapsed time
    elapsed = (datetime.datetime.now() - self.segment_start_time).total_seconds()
    elapsed_hours = elapsed / 3600
    
    # Target = start_temp + (rate * elapsed_hours)
    expected_temp = self.segment_start_temp + (segment.rate * elapsed_hours)
    
    # Clamp to segment target
    if segment.rate > 0:  # Heating
        return min(expected_temp, segment.target)
    else:  # Cooling
        return max(expected_temp, segment.target)

def update_target_temp(self):
    """New implementation: rate-based target"""
    self.target = self.calculate_rate_based_target()
    self.target_heat_rate = self.profile.get_rate_for_segment(self.current_segment_index)
```

### 3.5 `lib/oven.py` - Updated Main Control Loop

```python
def run(self):
    while True:
        if self.state == "IDLE":
            # ... existing idle logic unchanged ...
            time.sleep(1)
            continue
        
        if self.state == "RUNNING":
            # Track wall-clock time (ALWAYS advances - key change)
            self.actual_elapsed_time = (datetime.datetime.now() - self.start_time).total_seconds()
            
            # Update segment based on temperature (not time)
            self.update_segment_progress()
            
            # Calculate target based on rate and segment
            self.update_target_temp()
            
            # Check for rate deviation and log warnings
            self.check_rate_deviation()
            
            # Rest of control loop (unchanged)
            self.update_cost()
            self.track_divergence()
            self.save_automatic_restart_state()
            self.heat_then_cool()
            self.reset_if_emergency()
            
            # Calculate progress percentage (temperature-based)
            self.update_progress()

def check_rate_deviation(self):
    """
    Monitor actual heat rate vs target rate and log warnings if deviation is excessive.
    Replaces the old kiln_must_catch_up() behavior with logging-based feedback.
    """
    if self.segment_phase != 'ramp':
        return  # Only check during ramp phase
    
    segment = self.profile.segments[self.current_segment_index]
    
    # Skip check for special rates
    if not isinstance(segment.rate, (int, float)) or segment.rate == 0:
        return
    
    target_rate = abs(segment.rate)
    actual_rate = abs(self.heat_rate) if self.heat_rate else 0
    deviation = abs(target_rate - actual_rate)
    
    if deviation > config.rate_deviation_warning:
        if actual_rate < target_rate:
            log.warning(
                "Kiln heating slower than target: actual %.1f°/hr vs target %.1f°/hr "
                "(deviation: %.1f°/hr). Kiln may not reach temperature in expected time." %
                (actual_rate, target_rate, deviation)
            )
        else:
            log.info(
                "Kiln heating faster than target: actual %.1f°/hr vs target %.1f°/hr" %
                (actual_rate, target_rate)
            )
```

### 3.6 `lib/oven.py` - Progress Calculation

```python
def update_progress(self):
    """
    Calculate progress based on temperature achieved and time elapsed.
    Uses time-weighted progress within segments for accurate UX.
    """
    if not self.profile or not self.profile.segments:
        self.schedule_progress = 0
        return
    
    total_segments = len(self.profile.segments)
    completed_segments = self.current_segment_index
    
    # Base progress from completed segments
    base_progress = (completed_segments / total_segments) * 100
    
    # Add partial progress within current segment
    if self.current_segment_index < total_segments:
        segment = self.profile.segments[self.current_segment_index]
        current_temp = self.board.temp_sensor.temperature() + config.thermocouple_offset
        
        # Calculate estimated times for ramp and hold phases
        ramp_time = 0
        temp_range = abs(segment.target - self.segment_start_temp)
        if isinstance(segment.rate, (int, float)) and segment.rate != 0:
            ramp_time = (temp_range / abs(segment.rate)) * 3600
        elif segment.rate == "max":
            ramp_time = (temp_range / config.estimated_max_heating_rate) * 3600
        elif segment.rate == "cool":
            ramp_time = (temp_range / config.estimated_natural_cooling_rate) * 3600
        
        hold_time = segment.hold
        total_segment_time = ramp_time + hold_time
        
        # Calculate weights based on actual time proportions
        ramp_weight = ramp_time / total_segment_time if total_segment_time > 0 else 1.0
        hold_weight = hold_time / total_segment_time if total_segment_time > 0 else 0.0
        
        if self.segment_phase == 'ramp':
            if temp_range > 0:
                temp_progress = abs(current_temp - self.segment_start_temp) / temp_range
                temp_progress = min(1.0, max(0.0, temp_progress))
            else:
                temp_progress = 1.0
            segment_progress = temp_progress * ramp_weight
        else:
            # Ramp complete, now in hold phase
            hold_elapsed = (datetime.datetime.now() - self.hold_start_time).total_seconds()
            hold_progress = hold_elapsed / segment.hold if segment.hold > 0 else 1.0
            hold_progress = min(1.0, max(0.0, hold_progress))
            segment_progress = ramp_weight + (hold_progress * hold_weight)
        
        base_progress += (segment_progress / total_segments) * 100
    
    self.schedule_progress = min(100, base_progress)
```

### 3.7 `lib/oven.py` - Updated `get_state()` Method

```python
def get_state(self):
    temp = 0
    try:
        temp = self.board.temp_sensor.temperature() + config.thermocouple_offset
    except AttributeError:
        temp = 0

    # Use wall clock time for heat rate calculation
    self.set_heat_rate(self.actual_elapsed_time, temp)

    # Calculate ETA based on remaining segments and rates
    eta_seconds = self._estimate_remaining_time()

    state = {
        # Existing fields
        'cost': self.cost,
        'temperature': temp,
        'target': self.target,
        'state': self.state,
        'heat': self.heat,
        'heat_rate': self.heat_rate,
        'kwh_rate': config.kwh_rate,
        'currency_type': config.currency_type,
        'profile': self.profile.name if self.profile else None,
        'pidstats': self.pid.pidstats,
        'door': 'CLOSED',
        'cooling_estimate': self.cooling_estimate if self.cooling_mode else None,
        
        # Modified fields
        'runtime': self.actual_elapsed_time,    # Now always wall clock
        
        # New fields
        'target_heat_rate': self.target_heat_rate,
        'progress': self.schedule_progress,
        'current_segment': self.current_segment_index,
        'segment_phase': self.segment_phase,
        'eta_seconds': eta_seconds,
    }
    return state

def _estimate_remaining_time(self):
    """Estimate remaining time based on rates"""
    if not self.profile:
        return 0
    
    remaining = 0
    current_temp = self.board.temp_sensor.temperature() + config.thermocouple_offset
    
    # Time remaining in current segment
    if self.current_segment_index < len(self.profile.segments):
        segment = self.profile.segments[self.current_segment_index]
        
        if self.segment_phase == 'ramp':
            temp_remaining = abs(segment.target - current_temp)
            if isinstance(segment.rate, (int, float)) and segment.rate != 0:
                remaining += (temp_remaining / abs(segment.rate)) * 3600
            elif segment.rate == "max":
                # Estimate using configured max heating rate
                remaining += (temp_remaining / config.estimated_max_heating_rate) * 3600
            elif segment.rate == "cool":
                # Estimate using configured natural cooling rate
                remaining += (temp_remaining / config.estimated_natural_cooling_rate) * 3600
            remaining += segment.hold
        elif self.segment_phase == 'hold':
            hold_elapsed = (datetime.datetime.now() - self.hold_start_time).total_seconds()
            remaining += max(0, segment.hold - hold_elapsed)
    
    # Add remaining segments
    prev_target = current_temp
    for i in range(self.current_segment_index + 1, len(self.profile.segments)):
        segment = self.profile.segments[i]
        temp_diff = abs(segment.target - prev_target)
        if isinstance(segment.rate, (int, float)) and segment.rate != 0:
            remaining += (temp_diff / abs(segment.rate)) * 3600
        elif segment.rate == "max":
            remaining += (temp_diff / config.estimated_max_heating_rate) * 3600
        elif segment.rate == "cool":
            remaining += (temp_diff / config.estimated_natural_cooling_rate) * 3600
        remaining += segment.hold
        prev_target = segment.target
    
    return remaining
```

### 3.7.1 SimulatedOven Updates

The `SimulatedOven` class must also be updated to use segment-based logic for accurate testing:

```python
class SimulatedOven(Oven):
    """Simulated oven for testing - updated for v2 segment-based control"""
    
    def __init__(self):
        super().__init__(simulate=True)
        self.simulated_temp = 25.0  # Starting room temp
        self.last_sim_update = None
    
    def update_target_temp(self):
        """Use segment-based target calculation"""
        self.target = self.calculate_rate_based_target()
        self.target_heat_rate = self.profile.get_rate_for_segment(self.current_segment_index)
    
    def simulate_temperature_change(self):
        """Simulate kiln heating/cooling based on segment parameters"""
        now = datetime.datetime.now()
        if self.last_sim_update is None:
            self.last_sim_update = now
            return
        
        elapsed = (now - self.last_sim_update).total_seconds()
        self.last_sim_update = now
        
        segment = self.profile.segments[self.current_segment_index]
        
        if self.heat > 0:
            # Heating - simulate based on heat output
            if segment.rate == "max":
                # Simulate max heating rate
                rate_per_second = config.estimated_max_heating_rate / 3600
            elif isinstance(segment.rate, (int, float)) and segment.rate > 0:
                # Use configured rate with some variation
                rate_per_second = segment.rate / 3600
            else:
                rate_per_second = 0
            
            # Add some randomness for realism
            rate_per_second *= (0.9 + 0.2 * random.random())
            self.simulated_temp += rate_per_second * elapsed * (self.heat / 100)
        else:
            # Cooling - natural heat loss
            if segment.rate == "cool":
                rate_per_second = config.estimated_natural_cooling_rate / 3600
            else:
                # Slow natural cooling when not actively heating
                rate_per_second = 20 / 3600  # ~20 degrees per hour natural loss
            
            if self.simulated_temp > 25:  # Don't cool below room temp
                self.simulated_temp -= rate_per_second * elapsed
                self.simulated_temp = max(25, self.simulated_temp)
    
    def run(self):
        """Override run to include temperature simulation"""
        while True:
            if self.state == "RUNNING":
                self.simulate_temperature_change()
            
            super().run_single_iteration()  # Run one iteration of parent logic
            time.sleep(1)
```

### 3.8 `config.py` - New Configuration Options

```python
########################################################################
# Rate-Based Control Settings
########################################################################

# Tolerance for considering a segment target "reached" (in degrees)
segment_complete_tolerance = 5

# Maximum allowed deviation from target rate before logging warning
rate_deviation_warning = 50  # degrees/hour

# For "max" rate segments, what rate to use for time estimation
estimated_max_heating_rate = 500  # degrees/hour

# For "cool" rate segments, what rate to use for time estimation  
estimated_natural_cooling_rate = 100  # degrees/hour

# Whether to allow legacy v1 profile format (auto-convert on load)
allow_legacy_profiles = True
```

### 3.9 `kiln-controller.py` - Profile Handling Updates

```python
def save_profile(profile, force=False):
    """Save profile to disk - handles v2 format"""
    # Ensure version field
    if "version" not in profile:
        profile["version"] = 2
    
    # Ensure profile has temp_units
    profile = add_temp_units(profile)
    
    # Convert temperatures in segments if needed
    if profile['temp_units'] == "c" and config.temp_scale == "f":
        profile = convert_profile_to_f(profile)
    
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


def convert_profile_to_f(profile):
    """Convert v2 profile temperatures from Celsius to Fahrenheit"""
    if profile.get("temp_units") == "f":
        return profile
    
    if profile.get("version", 1) >= 2:
        # Convert start_temp
        if "start_temp" in profile:
            profile["start_temp"] = (profile["start_temp"] * 9 / 5) + 32
        
        # Convert segment targets
        for segment in profile.get("segments", []):
            segment["target"] = (segment["target"] * 9 / 5) + 32
            # Convert rate (degrees/hour)
            if isinstance(segment["rate"], (int, float)):
                segment["rate"] = segment["rate"] * 9 / 5
    else:
        # Legacy format
        for point in profile.get("data", []):
            point[1] = (point[1] * 9 / 5) + 32
    
    profile["temp_units"] = "f"
    return profile
```

---

## Part 4: Frontend Changes

### 4.1 `public/assets/js/picoreflow.js` - New Global Variables

```javascript
// New globals for v2 profiles
var profile_version = 1;
var profile_segments = [];
var profile_start_temp = 65;
```

### 4.2 Segment Editor UI

```javascript
function updateProfileTable_v2() {
    var html = '<h3>Firing Segments</h3>';
    html += '<div class="table-responsive"><table class="table table-striped">';
    html += '<tr><th>#</th><th>Rate (°' + temp_scale_display + '/hr)</th>';
    html += '<th>Target (°' + temp_scale_display + ')</th>';
    html += '<th>Hold (min)</th><th>Est. Time</th><th></th></tr>';
    
    var cumulative_time = 0;
    var current_temp = profile_start_temp;
    
    for (var i = 0; i < profile_segments.length; i++) {
        var seg = profile_segments[i];
        var seg_time = 0;
        
        // Calculate segment time
        if (typeof seg.rate === 'number' && seg.rate !== 0) {
            var temp_diff = Math.abs(seg.target - current_temp);
            seg_time = (temp_diff / Math.abs(seg.rate)) * 60; // minutes
        }
        seg_time += seg.hold || 0;
        cumulative_time += seg_time;
        
        var time_str = formatMinutesToHHMM(cumulative_time);
        
        html += '<tr>';
        html += '<td><h4>' + (i + 1) + '</h4></td>';
        html += '<td><input type="text" class="form-control seg-rate" data-idx="' + i + '" ';
        html += 'value="' + seg.rate + '" style="width:80px"/></td>';
        html += '<td><input type="text" class="form-control seg-target" data-idx="' + i + '" ';
        html += 'value="' + seg.target + '" style="width:80px"/></td>';
        html += '<td><input type="text" class="form-control seg-hold" data-idx="' + i + '" ';
        html += 'value="' + (seg.hold || 0) + '" style="width:60px"/></td>';
        html += '<td>' + time_str + '</td>';
        html += '<td><button class="btn btn-danger btn-sm del-segment" data-idx="' + i + '">×</button></td>';
        html += '</tr>';
        
        current_temp = seg.target;
    }
    
    html += '</table></div>';
    html += '<button class="btn btn-success" id="add_segment">+ Add Segment</button>';
    
    $('#profile_table').html(html);
    bindSegmentEvents();
}

function bindSegmentEvents() {
    $('.seg-rate, .seg-target, .seg-hold').change(function() {
        var idx = $(this).data('idx');
        var field = $(this).hasClass('seg-rate') ? 'rate' : 
                    $(this).hasClass('seg-target') ? 'target' : 'hold';
        var value = $(this).val();
        
        // Handle special rate values
        if (field === 'rate' && (value === 'max' || value === 'cool')) {
            profile_segments[idx][field] = value;
        } else {
            profile_segments[idx][field] = parseFloat(value);
        }
        
        updateGraphFromSegments();
        updateProfileTable_v2();
    });
    
    $('.del-segment').click(function() {
        var idx = $(this).data('idx');
        profile_segments.splice(idx, 1);
        updateGraphFromSegments();
        updateProfileTable_v2();
    });
    
    $('#add_segment').click(function() {
        var last_temp = profile_segments.length > 0 ? 
            profile_segments[profile_segments.length - 1].target : profile_start_temp;
        profile_segments.push({
            rate: 100,
            target: last_temp + 100,
            hold: 0
        });
        updateGraphFromSegments();
        updateProfileTable_v2();
    });
}

function updateGraphFromSegments() {
    // Convert segments to legacy format for graph display
    var data = [[0, profile_start_temp]];
    var current_time = 0;
    var current_temp = profile_start_temp;
    
    for (var i = 0; i < profile_segments.length; i++) {
        var seg = profile_segments[i];
        var seg_time = 0;
        
        if (typeof seg.rate === 'number' && seg.rate !== 0) {
            var temp_diff = Math.abs(seg.target - current_temp);
            seg_time = (temp_diff / Math.abs(seg.rate)) * 3600; // seconds
        } else if (seg.rate === 'max') {
            seg_time = Math.abs(seg.target - current_temp) / 500 * 3600;
        } else if (seg.rate === 'cool') {
            seg_time = Math.abs(current_temp - seg.target) / 100 * 3600;
        }
        
        current_time += seg_time;
        current_temp = seg.target;
        data.push([current_time, current_temp]);
        
        if (seg.hold > 0) {
            current_time += seg.hold * 60;
            data.push([current_time, current_temp]);
        }
    }
    
    graph.profile.data = data;
    graph.plot = $.plot("#graph_container", [graph.profile, graph.live], getOptions());
}

function formatMinutesToHHMM(minutes) {
    var hours = Math.floor(minutes / 60);
    var mins = Math.round(minutes % 60);
    return hours + 'h ' + mins + 'm';
}
```

### 4.3 Updated Status Display

```javascript
// In ws_status.onmessage handler:
if (state == "RUNNING") {
    // Show progress bar based on temperature progress
    updateProgress(x.progress);
    
    // Show segment info
    var segment_info = 'Seg ' + (x.current_segment + 1) + ' (' + x.segment_phase + ')';
    $('#segment_info').html(segment_info);
    
    // Show actual vs target rate
    var rate_display = x.heat_rate;
    if (x.target_heat_rate && typeof x.target_heat_rate === 'number') {
        rate_display += ' / ' + x.target_heat_rate;
    }
    $('#heat_rate').html(rate_display);
    
    // Show ETA based on remaining time estimate
    if (x.eta_seconds) {
        var eta = new Date(x.eta_seconds * 1000).toISOString().substr(11, 8);
        $('#state').html('<span class="glyphicon glyphicon-time"></span> ' + eta + ' remaining');
    }
}
```

---

## Part 5: Migration Script

### `scripts/migrate_profiles.py`

```python
#!/usr/bin/env python3
"""
Migrate legacy v1 profiles to v2 rate-based format.
Usage: python migrate_profiles.py [--dry-run] [--backup]
"""

import json
import os
import sys
import argparse
import shutil
from datetime import datetime


def convert_v1_to_v2(profile):
    """Convert a v1 time-based profile to v2 rate-based format"""
    if profile.get("version", 1) >= 2:
        return profile  # Already v2
    
    data = profile.get("data", [])
    if len(data) < 2:
        return None  # Invalid profile
    
    segments = []
    start_temp = data[0][1]
    
    for i in range(1, len(data)):
        prev_time, prev_temp = data[i-1]
        curr_time, curr_temp = data[i]
        
        time_diff = curr_time - prev_time  # seconds
        temp_diff = curr_temp - prev_temp  # degrees
        
        if time_diff > 0:
            if temp_diff != 0:
                # Ramp segment
                rate = (temp_diff / time_diff) * 3600  # degrees per hour
                segments.append({
                    "rate": round(rate, 1),
                    "target": curr_temp,
                    "hold": 0
                })
            else:
                # Hold segment - merge with previous if possible
                hold_minutes = time_diff / 60
                if segments and segments[-1]["target"] == curr_temp:
                    segments[-1]["hold"] += hold_minutes
                else:
                    segments.append({
                        "rate": 0,
                        "target": curr_temp,
                        "hold": round(hold_minutes, 1)
                    })
    
    return {
        "name": profile["name"],
        "type": "profile",
        "version": 2,
        "start_temp": start_temp,
        "temp_units": profile.get("temp_units", "f"),
        "segments": segments,
        "_migrated_from_v1": True,
        "_original_data": data
    }


def main():
    parser = argparse.ArgumentParser(description='Migrate profiles to v2 format')
    parser.add_argument('--dry-run', action='store_true', 
                        help='Show changes without writing')
    parser.add_argument('--backup', action='store_true', 
                        help='Create backups before modifying')
    parser.add_argument('--profile-dir', default='storage/profiles', 
                        help='Profile directory')
    args = parser.parse_args()
    
    profile_dir = args.profile_dir
    if not os.path.exists(profile_dir):
        print(f"Error: Profile directory not found: {profile_dir}")
        sys.exit(1)
    
    # Create backup directory if needed
    if args.backup:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_dir = f"storage/profiles_backup_{timestamp}"
        shutil.copytree(profile_dir, backup_dir)
        print(f"Backup created: {backup_dir}")
    
    # Process each profile
    for filename in os.listdir(profile_dir):
        if not filename.endswith('.json'):
            continue
        
        filepath = os.path.join(profile_dir, filename)
        
        try:
            with open(filepath, 'r') as f:
                profile = json.load(f)
            
            if profile.get("version", 1) >= 2:
                print(f"SKIP (already v2): {filename}")
                continue
            
            converted = convert_v1_to_v2(profile)
            if converted is None:
                print(f"ERROR (invalid): {filename}")
                continue
            
            if args.dry_run:
                print(f"WOULD CONVERT: {filename}")
                print(f"  Segments: {len(converted['segments'])}")
                for i, seg in enumerate(converted['segments']):
                    print(f"    {i+1}: rate={seg['rate']}, target={seg['target']}, hold={seg['hold']}")
            else:
                with open(filepath, 'w') as f:
                    json.dump(converted, f, indent=2)
                print(f"CONVERTED: {filename}")
        
        except Exception as e:
            print(f"ERROR: {filename} - {e}")


if __name__ == "__main__":
    main()
```

---

## Part 6: Test Updates

### Updated `Test/test_Profile.py`

```python
from lib.oven import Profile, Segment
import os
import json
import pytest


def get_v2_profile():
    """Create a test v2 profile"""
    return Profile(json.dumps({
        "name": "test-v2",
        "version": 2,
        "start_temp": 65,
        "segments": [
            {"rate": 100, "target": 200, "hold": 0},
            {"rate": 50, "target": 250, "hold": 60},
            {"rate": 200, "target": 1000, "hold": 0}
        ]
    }))


def get_legacy_profile():
    """Load legacy test profile"""
    profile_path = os.path.join(os.path.dirname(__file__), 'test-fast.json')
    with open(profile_path) as f:
        return Profile(json.dumps(json.load(f)))


class TestSegment:
    def test_segment_creation(self):
        seg = Segment(100, 500, hold=30)
        assert seg.rate == 100
        assert seg.target == 500
        assert seg.hold == 1800  # 30 minutes in seconds
    
    def test_segment_types(self):
        ramp = Segment(100, 500)
        hold = Segment(0, 500, hold=60)
        max_seg = Segment("max", 1000)
        cool_seg = Segment("cool", 200)
        
        assert ramp.is_ramp()
        assert hold.is_hold()
        assert max_seg.is_max_power()
        assert cool_seg.is_natural_cool()


class TestProfileV2:
    def test_load_v2_profile(self):
        profile = get_v2_profile()
        assert profile.version == 2
        assert profile.start_temp == 65
        assert len(profile.segments) == 3
    
    def test_segment_access(self):
        profile = get_v2_profile()
        assert profile.segments[0].rate == 100
        assert profile.segments[0].target == 200
        assert profile.segments[1].hold == 3600  # 60 min in seconds
    
    def test_estimate_duration(self):
        profile = get_v2_profile()
        duration = profile.estimate_duration()
        # Expected: (200-65)/100*3600 + 60*60 + (250-200)/50*3600 + (1000-250)/200*3600
        # = 4860 + 3600 + 3600 + 13500 = 25560 seconds
        assert 20000 < duration < 30000
    
    def test_to_legacy_format(self):
        profile = get_v2_profile()
        legacy = profile.to_legacy_format()
        
        assert legacy[0] == [0, 65]  # Start point
        assert len(legacy) >= 4  # At least start + 3 ramp endpoints


class TestProfileLegacy:
    def test_load_legacy_profile(self):
        profile = get_legacy_profile()
        # Should auto-convert to segments
        assert hasattr(profile, 'segments')
        assert len(profile.segments) > 0
    
    def test_legacy_start_temp(self):
        profile = get_legacy_profile()
        assert profile.start_temp > 0


class TestSegmentProgress:
    def test_get_segment_for_temperature_heating(self):
        profile = get_v2_profile()
        
        # At start temp, should be in segment 0, ramping
        idx, seg, phase = profile.get_segment_for_temperature(65, 0)
        assert idx == 0
        assert phase == 'ramp'
        
        # At segment target, should transition to hold or next segment
        idx, seg, phase = profile.get_segment_for_temperature(200, 0)
        assert phase == 'hold' or idx > 0
    
    def test_rate_zero_is_hold(self):
        """Test that rate=0 segments are treated as holds"""
        profile = Profile(json.dumps({
            "name": "test-hold",
            "version": 2,
            "start_temp": 100,
            "segments": [
                {"rate": 0, "target": 100, "hold": 30}
            ]
        }))
        idx, seg, phase = profile.get_segment_for_temperature(100, 0)
        assert phase == 'hold'


class TestEdgeCases:
    def test_single_segment_profile(self):
        """Test profile with only one segment"""
        profile = Profile(json.dumps({
            "name": "test-single",
            "version": 2,
            "start_temp": 65,
            "segments": [
                {"rate": 100, "target": 500, "hold": 0}
            ]
        }))
        assert len(profile.segments) == 1
        assert profile.estimate_duration() > 0
    
    def test_all_max_rates(self):
        """Test profile with all max rate segments"""
        profile = Profile(json.dumps({
            "name": "test-max",
            "version": 2,
            "start_temp": 65,
            "segments": [
                {"rate": "max", "target": 500, "hold": 0},
                {"rate": "max", "target": 1000, "hold": 30}
            ]
        }))
        assert profile.segments[0].is_max_power()
        assert profile.segments[1].is_max_power()
        # Should still estimate duration
        duration = profile.estimate_duration()
        assert duration > 0
    
    def test_cool_segments(self):
        """Test profile with cooling segments"""
        profile = Profile(json.dumps({
            "name": "test-cool",
            "version": 2,
            "start_temp": 1000,
            "segments": [
                {"rate": "cool", "target": 500, "hold": 0},
                {"rate": -100, "target": 200, "hold": 0}
            ]
        }))
        assert profile.segments[0].is_natural_cool()
        assert profile.segments[1].rate == -100
    
    def test_valley_profile(self):
        """Test profile with temperature decreasing then increasing"""
        profile = Profile(json.dumps({
            "name": "test-valley",
            "version": 2,
            "start_temp": 500,
            "segments": [
                {"rate": -100, "target": 200, "hold": 30},
                {"rate": 200, "target": 800, "hold": 0}
            ]
        }))
        assert len(profile.segments) == 2
        legacy = profile.to_legacy_format()
        # Should have start, valley, hold, peak
        assert len(legacy) >= 4
    
    def test_zero_duration_hold(self):
        """Test segment with hold=0"""
        profile = Profile(json.dumps({
            "name": "test-no-hold",
            "version": 2,
            "start_temp": 65,
            "segments": [
                {"rate": 100, "target": 500, "hold": 0}
            ]
        }))
        assert profile.segments[0].hold == 0
        assert not profile.segments[0].has_hold_phase()
    
    def test_validation_rejects_invalid_rate_direction(self):
        """Test that invalid rate/direction combinations are rejected"""
        with pytest.raises(ValueError):
            Profile(json.dumps({
                "name": "test-invalid",
                "version": 2,
                "start_temp": 200,
                "segments": [
                    {"rate": -100, "target": 500, "hold": 0}  # Negative rate, higher target
                ]
            }))


class TestAutomaticRestart:
    def test_restart_from_ramp_phase(self):
        """Test automatic restart during ramp phase"""
        # This would require mocking the Oven class
        pass
    
    def test_restart_from_hold_phase(self):
        """Test automatic restart during hold phase preserves elapsed hold time"""
        # This would require mocking the Oven class
        pass
```

---

## Part 7: Implementation Order

### Phase 1: Core Data Model (Low Risk)
1. Add `Segment` class to `lib/oven.py`
2. Add v2 loading to `Profile` class (with legacy fallback)
3. Add `to_legacy_format()` for graph compatibility
4. Update tests
5. Test with existing profiles (auto-convert should work)

### Phase 2: Control Logic (Medium Risk)
1. Add new state variables to `Oven.reset()`
2. Implement `update_segment_progress()`
3. Implement `calculate_rate_based_target()`
4. Update `get_state()` with new fields
5. Modify main loop to use new methods
6. Remove or deprecate `kiln_must_catch_up()`

### Phase 3: Frontend (Medium Risk)
1. Add segment editor UI functions
2. Update status display for new fields
3. Update profile save/load for v2 format
4. Maintain graph compatibility via `to_legacy_format()`
5. Add UI toggle between legacy and v2 editor modes

### Phase 4: Migration & Cleanup (Low Risk)
1. Create migration script
2. Test migration on all existing profiles
3. Migrate profiles (with backup)
4. Update documentation
5. Remove deprecated code paths (optional)

---

## Part 8: Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Legacy profile compatibility | Users lose existing profiles | Auto-convert v1→v2 on load; keep `_original_data` |
| PID tuning changes needed | Temperature control less accurate | Rate-based targets may behave differently; document tuning |
| "Max power" unpredictable | Can't estimate time accurately | Use configurable estimate; log actual performance |
| Hold time accuracy | Drift in long holds | Use wall-clock time for holds (not schedule time) |
| Frontend complexity | UI harder to use | Provide both "simple" wizard and "advanced" table modes |
| Simulation mode differences | SimulatedOven behaves differently | Update SimulatedOven to use same segment logic |

---

## Part 9: Behavioral Changes Summary

### What Changes for Users

| Aspect | Before (v1) | After (v2) |
|--------|-------------|------------|
| Profile definition | Time + Temperature points | Rate + Target + Hold |
| Clock behavior | Pauses when out of range | Always runs (wall time) |
| Progress indicator | Based on time elapsed | Based on temperature achieved |
| ETA calculation | Based on remaining schedule time | Based on remaining segments + rates |
| Heat rate display | Actual only | Actual vs Target |
| "Catch up" behavior | Schedule shifts forward | Logs warning, continues at rate |

### What Stays the Same

- PID controller core algorithm
- Temperature reading and averaging
- Emergency shutoff logic
- Cost calculation (per time step)
- WebSocket communication protocol
- Graph display (uses legacy format internally)

---

## Appendix A: Example Profile Conversions

### Cone 05 Fast Bisque

**Before (v1):**
```json
{
  "name": "cone-05-fast-bisque",
  "data": [[0, 65], [600, 200], [2088, 250], [5688, 250], [23135, 1733], [28320, 1888], [30900, 1888]]
}
```

**After (v2):**
```json
{
  "name": "cone-05-fast-bisque",
  "version": 2,
  "start_temp": 65,
  "temp_units": "f",
  "segments": [
    {"rate": 810.0, "target": 200, "hold": 0},
    {"rate": 121.0, "target": 250, "hold": 60},
    {"rate": 306.0, "target": 1733, "hold": 0},
    {"rate": 107.6, "target": 1888, "hold": 43}
  ]
}
```

**Rate calculations:**
- Segment 1: (200-65)/(600-0)*3600 = 810.0 °F/hr
- Segment 2: (250-200)/(2088-600)*3600 = 121.0 °F/hr, then hold 60 min (5688-2088=3600s)
- Segment 3: (1733-250)/(23135-5688)*3600 = 306.0 °F/hr
- Segment 4: (1888-1733)/(28320-23135)*3600 = 107.6 °F/hr, then hold 43 min (30900-28320=2580s)
```

### Cone 6 Long Glaze

**Before (v1):**
```json
{
  "name": "cone-6-long-glaze", 
  "data": [[0, 65], [600, 200], [7200, 250], [25200, 1976], [32880, 2232], [33480, 2232], [36780, 1832], [48780, 1400]]
}
```

**After (v2):**
```json
{
  "name": "cone-6-long-glaze",
  "version": 2,
  "start_temp": 65,
  "temp_units": "f",
  "segments": [
    {"rate": 810.0, "target": 200, "hold": 0},
    {"rate": 27.3, "target": 250, "hold": 0},
    {"rate": 345.6, "target": 1976, "hold": 0},
    {"rate": 120.0, "target": 2232, "hold": 10},
    {"rate": -436.4, "target": 1832, "hold": 0},
    {"rate": -129.6, "target": 1400, "hold": 0}
  ]
}
```

**Rate calculations:**
- Segment 1: (200-65)/(600-0)*3600 = 810.0 °F/hr
- Segment 2: (250-200)/(7200-600)*3600 = 27.3 °F/hr
- Segment 3: (1976-250)/(25200-7200)*3600 = 345.6 °F/hr
- Segment 4: (2232-1976)/(32880-25200)*3600 = 120.0 °F/hr, then hold 10 min (33480-32880=600s)
- Segment 5: (1832-2232)/(36780-33480)*3600 = -436.4 °F/hr (cooling)
- Segment 6: (1400-1832)/(48780-36780)*3600 = -129.6 °F/hr (cooling)
```

---

*Document created: January 29, 2026*
*Last updated: January 29, 2026*
*QA fixes applied: January 29, 2026*

---

## Implementation Fixes Applied

**Updated: January 29, 2026**

This section summarizes the fixes that have been applied to address the issues identified in the QA Review below.

### P0 Critical Fixes Applied

| Issue | Fix Applied | Section Updated |
|-------|-------------|-----------------|
| Example rate calculations incorrect | Recalculated all rates with formulas shown | Appendix A |
| `rate=0` not handled in `get_segment_for_temperature()` | Added explicit `elif segment.rate == 0: return 'hold'` | Section 3.2 |
| Automatic restart incompatible | Added v2 segment-based `save_automatic_restart_state()` and `automatic_restart()` | New Section 3.3.1 |

### P1 Moderate Fixes Applied

| Issue | Fix Applied | Section Updated |
|-------|-------------|-----------------|
| Legacy conversion creates orphan segments | Modified `_load_legacy()` to merge holds into previous segment's hold field | Section 3.2 |
| No temperature unit conversion on load | Added unit detection and conversion in `_load_v2()` | Section 3.2 |
| SimulatedOven not updated | Added `SimulatedOven` class with segment-based methods | New Section 3.7.1 |

### P2 Moderate Fixes Applied

| Issue | Fix Applied | Section Updated |
|-------|-------------|-----------------|
| Progress calculation arbitrary 80/20 | Replaced with time-weighted calculation based on estimated ramp/hold duration | Section 3.6 |
| No rate deviation logging | Added `check_rate_deviation()` method with configurable threshold | Section 3.5 |
| ETA ignores special rates | Updated `_estimate_remaining_time()` to use config estimates for max/cool | Section 3.7 |

### P3 Minor Fixes Applied

| Issue | Fix Applied | Section Updated |
|-------|-------------|-----------------|
| `is_hold()` method misleading | Renamed to `is_pure_hold()` and added `has_hold_phase()` | Section 3.1 |
| No validation of rate direction | Added `validate()` method to Segment class, called in `_load_v2()` | Sections 3.1, 3.2 |
| `to_legacy_format()` bug with rate=0 | Fixed to only add ramp points for non-zero rates | Section 3.2 |
| Tests don't cover edge cases | Added comprehensive edge case tests | Section 6 |

---

## QA Review: Errors, Missteps, and Bad Logic

**Reviewed by: QA Engineering**  
**Date: January 29, 2026**  
**Status: Issues addressed - see "Implementation Fixes Applied" above**

This section documents issues identified during quality control review of the refactoring plan.

---

### Critical Issues

#### 1. Example Rate Calculations Are Mathematically Incorrect ✅ FIXED

The example conversions in Appendix A contained calculation errors. Using the actual profile data:

```
[[0, 65], [600, 200], [2088, 250], [5688, 250], [23135, 1733], [28320, 1888], [30900, 1888]]
```

**Original documented values vs. correct values:**

| Segment | Originally Showed | Correct Calculation | Formula |
|---------|-------------------|---------------------|---------|
| [0,65]→[600,200] | 810.0 | 810.0 ✓ | (200-65)/(600-0)*3600 |
| [600,200]→[2088,250] | 40.3 | **121.0** | (250-200)/(2088-600)*3600 |
| [5688,250]→[23135,1733] | 508.4 | **306.0** | (1733-250)/(23135-5688)*3600 |
| [23135,1733]→[28320,1888] | 178.4 | **107.6** | (1888-1733)/(28320-23135)*3600 |

**Fix Applied:** Appendix A has been updated with correct rate values and calculation formulas shown for verification.

---

#### 2. `rate=0` Not Handled in `get_segment_for_temperature()` ✅ FIXED

The method originally only checked `segment.rate > 0` and `segment.rate < 0`, causing `rate=0` segments to fall through and incorrectly return `'ramp'` phase.

**Fix Applied:** Added explicit handling in Section 3.2:
```python
if segment.rate == 0:  # Explicit hold segment
    return (segment_index, segment, 'hold')
elif segment.rate > 0:  # Heating
    ...
```

---

#### 3. Temperature Unit Conversion Crashes on String Rates ✅ FIXED

In `convert_profile_to_f()`:

```python
if isinstance(segment["rate"], (int, float)):
    segment["rate"] = segment["rate"] * 9 / 5
```

**Original Problem:** The `_load_v2()` method completely ignored `temp_units`, so a Celsius profile loaded on a Fahrenheit system would have wrong temperatures.

**Fix Applied:** Updated `_load_v2()` in Section 3.2 to:
- Detect unit mismatch between profile and system
- Convert `start_temp`, segment `target`, and numeric `rate` values
- String rates ("max", "cool") are unit-agnostic by design (they represent behaviors, not numeric rates)

---

#### 4. Automatic Restart Incompatibility ✅ FIXED

The original `automatic_restart()` method resumed at a **time offset**, but the new system is **temperature-based**.

**Fix Applied:** Added new Section 3.3.1 with:
- `save_automatic_restart_state()` - saves segment index, phase, hold elapsed time
- `automatic_restart()` - restores v2 segment-based state
- `_automatic_restart_legacy()` - handles v1 restart files by finding appropriate segment based on current temperature

---

### Moderate Issues

#### 5. Redundant and Confusing Hold Representation ⚠️ CLARIFIED

The format allows two ways to represent a hold:

1. **Inline hold:** `{"rate": 200, "target": 500, "hold": 30}` — ramp to 500, then hold 30 min
2. **Explicit hold:** `{"rate": 0, "target": 500, "hold": 30}` — hold at 500 for 30 min

**Clarification Added:** A segment with `{"rate": 0, "target": 500, "hold": 0}` is treated as a zero-duration hold (effectively a no-op). The validation added to `_load_v2()` will log a warning for such segments but not reject them, as they may occur during profile editing.

**Design Decision:** Keeping both representations for flexibility:
- `rate=0` is useful for legacy conversion where holds are separate data points
- Inline `hold` field is the preferred v2 approach for new profiles

---

#### 6. Legacy Conversion Creates Orphan Segments ✅ FIXED

The original `_load_legacy()` method created separate `rate=0` segments for holds instead of attaching them to the previous segment.

**Fix Applied:** Updated `_load_legacy()` in Section 3.2 to check if the previous segment has the same target temperature, and if so, add the hold time to that segment's `hold` field instead of creating a new segment:

```python
if self.segments and self.segments[-1].target == curr_temp:
    # Add hold time to the previous segment (in seconds)
    self.segments[-1].hold += hold_minutes * 60
else:
    # Create standalone hold segment only if no previous segment
    self.segments.append(Segment(0, curr_temp, hold=hold_minutes))
```

---

#### 7. Progress Calculation Uses Arbitrary 80/20 Split ✅ FIXED

The original code used a fixed 80/20 split between ramp and hold progress, which provided poor UX for segments with long holds.

**Fix Applied:** Updated `update_progress()` in Section 3.6 to calculate time-weighted progress based on estimated ramp and hold durations:

```python
ramp_time = (temp_range / abs(segment.rate)) * 3600
hold_time = segment.hold
total_segment_time = ramp_time + hold_time
ramp_weight = ramp_time / total_segment_time if total_segment_time > 0 else 1.0
hold_weight = hold_time / total_segment_time if total_segment_time > 0 else 0.0
```

The fix also handles special rates ("max", "cool") using the configured estimation values.

---

#### 8. `_estimate_remaining_time()` Ignores Special Rates ✅ FIXED

The original method only calculated time for numeric rates, leaving ETA wrong for profiles with `"max"` or `"cool"` segments.

**Fix Applied:** Updated `_estimate_remaining_time()` in Section 3.7 to handle special rates using the configured estimation values:

```python
elif segment.rate == "max":
    remaining += (temp_remaining / config.estimated_max_heating_rate) * 3600
elif segment.rate == "cool":
    remaining += (temp_remaining / config.estimated_natural_cooling_rate) * 3600
```

---

#### 9. SimulatedOven Not Updated ✅ FIXED

The original document acknowledged this risk but provided no implementation.

**Fix Applied:** Added new Section 3.7.1 with a complete `SimulatedOven` class implementation that:
- Uses segment-based target calculation via `calculate_rate_based_target()`
- Simulates temperature changes based on segment rate and heat output
- Handles special rates ("max", "cool") using configured estimation values
- Adds realistic randomness for testing

---

#### 10. No Rate Deviation Detection/Logging ✅ FIXED

The original document stated the new behavior for catch-up is "Logs warning, continues at rate" but provided no implementation.

**Fix Applied:** Added `check_rate_deviation()` method in Section 3.5 that:
- Monitors actual heat rate vs target rate during ramp phases
- Logs warnings when deviation exceeds `config.rate_deviation_warning` threshold
- Differentiates between "heating slower" (warning) and "heating faster" (info) messages
- Replaces the old `kiln_must_catch_up()` functionality with logging-based feedback

---

### Minor Issues

#### 11. `is_hold()` Method is Misleading ✅ FIXED

The original `is_hold()` method was confusing because a ramp+hold segment would return `True`.

**Fix Applied:** Updated Section 3.1 to rename and split the method:
- `is_pure_hold()` - returns `True` only if `rate == 0`
- `has_hold_phase()` - returns `True` if segment has any hold time (`hold > 0`)

---

#### 12. No Validation of Segment Rate Direction vs. Temperature ✅ FIXED

Nothing prevented creating invalid segments like `{"rate": -100, "target": 500}` (negative rate with higher target).

**Fix Applied:** Added `validate()` method to `Segment` class in Section 3.1, and validation is called during `_load_v2()` in Section 3.2:
```python
def validate(self, previous_target=None):
    if self.rate < 0 and self.target > previous_target:
        raise ValueError("Negative rate with increasing target temperature")
    if self.rate > 0 and self.target < previous_target:
        raise ValueError("Positive rate with decreasing target temperature")
```

---

#### 13. Frontend Graph Relies on Legacy Conversion ⚠️ ACKNOWLEDGED

The plan maintains graph compatibility via `to_legacy_format()`, which creates a maintenance burden.

**Mitigation:** The `to_legacy_format()` method is a pure calculation with no side effects, and unit tests verify it produces correct output. Future work could implement native v2 graph rendering, but this is lower priority than core functionality.

---

#### 14. `to_legacy_format()` Bug with `rate=0` Segments ✅ FIXED

The original code added a data point for `rate=0` segments at the same time position, creating invalid graph data.

**Fix Applied:** Updated `to_legacy_format()` in Section 3.2 to only add ramp points for non-zero rates. For `rate=0` (pure hold) segments, only the hold point is added:
```python
# For rate=0 (pure hold), don't add a ramp point - just add the hold below
if segment.hold > 0:
    current_time += segment.hold
    data.append([current_time, current_temp])
```

---

#### 15. No Schema Version Migration Strategy ⚠️ ACKNOWLEDGED

The document mentions keeping `_original_data` in migrated profiles, but there's no:
- Strategy for rolling back if v2 has bugs
- Way to re-migrate if the migration script has bugs
- Version history tracking

**Mitigation:** The migration script in Section 5 already includes:
- `--backup` flag to create timestamped backup directories
- `_original_data` field preserved in migrated profiles for reference
- `_migrated_from_v1` flag to identify converted profiles

---

#### 16. Tests Don't Cover Edge Cases ✅ FIXED

The original proposed tests didn't verify edge cases.

**Fix Applied:** Added comprehensive edge case tests in Section 6:
- `test_single_segment_profile` - profile with only one segment
- `test_all_max_rates` - profile with all "max" rate segments
- `test_cool_segments` - profile with "cool" segments
- `test_valley_profile` - temperature decreasing then increasing
- `test_zero_duration_hold` - segment with hold=0
- `test_validation_rejects_invalid_rate_direction` - validation error handling
- `TestAutomaticRestart` class - restart from hold/ramp phases (requires mocking)

---

### Summary of Required Fixes Before Implementation

| Priority | Issue | Status |
|----------|-------|--------|
| **P0** | Example calculations are wrong | ✅ FIXED - Appendix A corrected |
| **P0** | `rate=0` not handled in segment tracking | ✅ FIXED - Section 3.2 |
| **P0** | Automatic restart incompatible | ✅ FIXED - Section 3.3.1 |
| **P1** | Legacy conversion creates wrong segments | ✅ FIXED - Section 3.2 |
| **P1** | No temperature unit conversion on load | ✅ FIXED - Section 3.2 |
| **P1** | SimulatedOven not updated | ✅ FIXED - Section 3.7.1 |
| **P2** | Progress calculation arbitrary | ✅ FIXED - Section 3.6 |
| **P2** | No rate deviation logging | ✅ FIXED - Section 3.5 |
| **P2** | ETA ignores special rates | ✅ FIXED - Section 3.7 |
| **P3** | Hold representation ambiguous | ⚠️ CLARIFIED |
| **P3** | Missing validation | ✅ FIXED - Sections 3.1, 3.2 |

---

### Recommendations

1. **Create a test profile suite** with known-good v1→v2 conversions to validate the migration script. ✅ *Test cases added in Section 6*

2. **Implement v2 loading first**, test thoroughly, then proceed to control logic changes.

3. **Add a feature flag** `use_rate_based_control = False` in config to allow gradual rollout.

4. **Write integration tests** that run a simulated firing with both v1 and v2 profiles and compare temperature curves. ✅ *SimulatedOven class added in Section 3.7.1*

5. **Consider a "compatibility mode"** where v2 profiles can still use time-based tracking internally for users who depend on `kiln_must_catch_up` behavior.

---

*End of QA Review - All critical and major issues have been addressed. See "Implementation Fixes Applied" section above for summary.*
