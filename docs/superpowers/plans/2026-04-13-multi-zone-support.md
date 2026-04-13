# Multi-Zone Kiln Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add configurable N-zone support so each zone has its own thermocouple, PID controller, and relay output, all following a single shared firing profile.

**Architecture:** Zone object composition — a new `Zone` class bundles TempSensor + PID + Output. The Oven holds a list of Zones. The control loop iterates zones each cycle. Backward-compatible: no `zones` config = single zone auto-synthesized from legacy scalars.

**Tech Stack:** Python 3, threading, SPI (adafruit libs), Flot.js (frontend charting), MQTT (paho-mqtt), pytest

**Spec:** `docs/superpowers/specs/2026-04-13-multi-zone-support-design.md`

**Branch:** `feature/multi-zone` (create from `main`)

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `config.py` | Modify | Add `zones` list, `zone_control_strategy` |
| `lib/oven.py` | Modify | Add `Zone`, `SimulatedOutput`, `get_zone_configs()`. Refactor `Output`, `Board`, `TempSensorReal`, `Oven`, `SimulatedOven`, `RealOven` |
| `lib/ovenWatcher.py` | Modify (minimal) | No structural changes — richer state dict flows through |
| `lib/mqtt.py` | Modify | Per-zone topics in `publish_state()`, zone names in `_on_connect()` |
| `kiln-controller.py` | Modify | Multi-zone `set_sim_temp`, oven instantiation passes zone configs |
| `public/index.html` | Modify | Zone summary panel HTML container |
| `public/assets/js/picoreflow.js` | Modify | Multi-trace Flot graph, zone panel updates, crosshair per-zone |
| `public/assets/css/components.css` | Modify | Zone panel card styling |
| `public/assets/css/responsive.css` | Modify | Zone panel responsive layout |
| `Test/test_zone.py` | Create | Zone class, get_zone_configs, get_control_temperature, safety checks |
| `Test/test_mqtt.py` | Modify | Multi-zone publish tests |

---

## Task 1: Create Branch and Config Schema

**Files:**
- Modify: `config.py` (add after line 431)

- [ ] **Step 1: Create feature branch**

```bash
git checkout main
git checkout -b feature/multi-zone
```

- [ ] **Step 2: Add zone config to config.py**

Add after the MQTT section (line 431) in `config.py`:

```python
########################################################################
# Multi-Zone Configuration (optional)
# Each zone has its own thermocouple (CS pin), relay (GPIO), PID tuning,
# and metadata. If zones is empty or not defined, the system falls back
# to single-zone behavior using the legacy scalar config values above.
#
# Shared SPI bus pins (spi_sclk, spi_miso, spi_mosi) remain top-level.
# Only spi_cs moves into the per-zone config.
########################################################################
zones = []  # Empty = single-zone mode using legacy scalars above

# Strategy for determining the "control temperature" that drives profile progression.
# Only considers critical zones. Options: "coldest", "hottest", "average", or int (zone index).
# Example: zone_control_strategy = 1  # always use zone index 1
zone_control_strategy = "coldest"
```

- [ ] **Step 3: Verify imports still work**

Run: `python -c "import config; print('zones:', config.zones, 'strategy:', config.zone_control_strategy)"`
Expected: `zones: [] strategy: coldest`

- [ ] **Step 4: Commit**

```bash
git add config.py
git commit -m "feat: add multi-zone config schema (zones list, zone_control_strategy)"
```

---

## Task 2: Zone Class, SimulatedOutput, and get_zone_configs()

**Files:**
- Modify: `lib/oven.py` (add new classes near line 48, add helper function)
- Create: `Test/test_zone.py`

- [ ] **Step 1: Write tests for get_zone_configs()**

Create `Test/test_zone.py`:

```python
import pytest
import config

# Test get_zone_configs after it is added to oven.py
class TestGetZoneConfigs:
    """Test the helper that builds zone configs from config.py."""

    def test_empty_zones_returns_legacy_single_zone(self):
        """When config.zones is empty, synthesize one zone from legacy scalars."""
        from lib.oven import get_zone_configs
        original = getattr(config, 'zones', [])
        config.zones = []
        try:
            zones = get_zone_configs()
            assert len(zones) == 1
            assert zones[0]["name"] == "Kiln"
            assert zones[0]["pid_kp"] == config.pid_kp
            assert zones[0]["pid_ki"] == config.pid_ki
            assert zones[0]["pid_kd"] == config.pid_kd
            assert zones[0]["thermocouple_offset"] == config.thermocouple_offset
            assert zones[0]["temp_offset"] == 0
            assert zones[0]["critical"] == True
        finally:
            config.zones = original

    def test_no_zones_attr_returns_legacy_single_zone(self):
        """When config has no zones attribute at all, still works."""
        from lib.oven import get_zone_configs
        original = getattr(config, 'zones', [])
        if hasattr(config, 'zones'):
            delattr(config, 'zones')
        try:
            zones = get_zone_configs()
            assert len(zones) == 1
            assert zones[0]["name"] == "Kiln"
        finally:
            config.zones = original

    def test_explicit_zones_returned_as_is(self):
        """When config.zones is populated, return it directly."""
        from lib.oven import get_zone_configs
        original = getattr(config, 'zones', [])
        config.zones = [
            {"name": "Top", "spi_cs": None, "gpio_heat": None, "critical": True},
            {"name": "Bottom", "spi_cs": None, "gpio_heat": None, "critical": True},
        ]
        try:
            zones = get_zone_configs()
            assert len(zones) == 2
            assert zones[0]["name"] == "Top"
            assert zones[1]["name"] == "Bottom"
        finally:
            config.zones = original


class TestZone:
    """Test the Zone class."""

    def test_zone_init_basic(self):
        from lib.oven import Zone, PID
        zone_config = {
            "name": "Top",
            "pid_kp": 10.0,
            "pid_ki": 5,
            "pid_kd": 100,
            "thermocouple_offset": -2,
            "temp_offset": 5,
            "critical": True,
        }
        zone = Zone(index=0, zone_config=zone_config, temp_sensor=None, output=None)
        assert zone.index == 0
        assert zone.name == "Top"
        assert zone.temp_offset == 5
        assert zone.critical == True
        assert zone.thermocouple_offset == -2
        assert zone.temperature == 0
        assert zone.heat == 0
        assert zone.stall_start_time is None
        assert zone.runaway_start_time is None
        assert isinstance(zone.pid, PID)
        assert zone.pid.kp == 10.0

    def test_zone_defaults(self):
        """Zone uses config defaults when keys are missing."""
        from lib.oven import Zone
        zone_config = {"name": "Minimal"}
        zone = Zone(index=0, zone_config=zone_config, temp_sensor=None, output=None)
        assert zone.temp_offset == 0
        assert zone.critical == True
        assert zone.thermocouple_offset == 0
        assert zone.pid.kp == config.pid_kp

    def test_zone_advisory(self):
        from lib.oven import Zone
        zone_config = {"name": "Monitor", "critical": False}
        zone = Zone(index=0, zone_config=zone_config, temp_sensor=None, output=None)
        assert zone.critical == False


class TestSimulatedOutput:
    """Test the SimulatedOutput class."""

    def test_heat_does_not_sleep(self):
        import time
        from lib.oven import SimulatedOutput
        out = SimulatedOutput()
        assert out.active == False
        start = time.monotonic()
        out.heat(1.0)  # Should NOT sleep for 1 second
        elapsed = time.monotonic() - start
        assert elapsed < 0.1  # Should be near-instant
        assert out.active == True

    def test_cool_does_not_sleep(self):
        import time
        from lib.oven import SimulatedOutput
        out = SimulatedOutput()
        out.active = True
        start = time.monotonic()
        out.cool(1.0)
        elapsed = time.monotonic() - start
        assert elapsed < 0.1
        assert out.active == False
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `python -m pytest Test/test_zone.py -v`
Expected: FAIL — `Zone`, `SimulatedOutput`, `get_zone_configs` do not exist yet

- [ ] **Step 3: Implement get_zone_configs()**

Add to `lib/oven.py` after the imports (around line 28, after `log = logging.getLogger(__name__)`):

```python
def get_zone_configs():
    """Return zone configs. If none defined, synthesize one from legacy scalars."""
    if hasattr(config, 'zones') and config.zones:
        return config.zones
    return [{
        "name": "Kiln",
        "spi_cs": getattr(config, 'spi_cs', None),
        "gpio_heat": getattr(config, 'gpio_heat', None),
        "gpio_heat_invert": getattr(config, 'gpio_heat_invert', False),
        "pid_kp": config.pid_kp,
        "pid_ki": config.pid_ki,
        "pid_kd": config.pid_kd,
        "thermocouple_offset": config.thermocouple_offset,
        "temp_offset": 0,
        "critical": True,
    }]
```

- [ ] **Step 4: Implement Zone class**

Add to `lib/oven.py` after `get_zone_configs()`:

```python
class Zone:
    """A single heating zone: thermocouple + PID + relay output."""
    def __init__(self, index, zone_config, temp_sensor, output):
        self.index = index
        self.name = zone_config["name"]
        self.temp_offset = zone_config.get("temp_offset", 0)
        self.critical = zone_config.get("critical", True)
        self.thermocouple_offset = zone_config.get("thermocouple_offset", 0)

        self.temp_sensor = temp_sensor
        self.output = output
        self.pid = PID(
            kp=zone_config.get("pid_kp", config.pid_kp),
            ki=zone_config.get("pid_ki", config.pid_ki),
            kd=zone_config.get("pid_kd", config.pid_kd),
        )
        self.temperature = 0
        self.target = 0
        self.heat = 0
        self.heat_rate = 0
        self.heat_rate_temps = []

        # Per-zone safety tracking
        self.stall_start_time = None
        self.stall_start_temp = None
        self.runaway_start_time = None
        self.runaway_start_temp = None

    def set_heat_rate(self, runtime):
        """Calculate heat rate for this zone. Mirrors Oven.set_heat_rate()."""
        min_samples = 10
        rate_window_seconds = getattr(config, 'heat_rate_window_seconds', 300)
        self.heat_rate_temps.append((runtime, self.temperature))
        if len(self.heat_rate_temps) > min_samples:
            cutoff_time = runtime - rate_window_seconds
            filtered = [(t, tp) for t, tp in self.heat_rate_temps if t >= cutoff_time]
            if len(filtered) >= min_samples:
                self.heat_rate_temps = filtered
            else:
                self.heat_rate_temps = self.heat_rate_temps[-min_samples:]
        if len(self.heat_rate_temps) > 1000:
            self.heat_rate_temps = self.heat_rate_temps[-1000:]
        if len(self.heat_rate_temps) >= 2:
            time2 = self.heat_rate_temps[-1][0]
            time1 = self.heat_rate_temps[0][0]
            temp2 = self.heat_rate_temps[-1][1]
            temp1 = self.heat_rate_temps[0][1]
            if time2 > time1:
                self.heat_rate = ((temp2 - temp1) / (time2 - time1)) * 3600
```

- [ ] **Step 5: Implement SimulatedOutput class**

Add to `lib/oven.py` after the `Zone` class:

```python
class SimulatedOutput:
    """No-op output for simulated zones. No GPIO, no sleeping."""
    def __init__(self):
        self.active = False

    def heat(self, sleepfor):
        self.active = True

    def cool(self, sleepfor):
        self.active = False
```

- [ ] **Step 6: Run tests — verify they pass**

Run: `python -m pytest Test/test_zone.py -v`
Expected: All PASS

- [ ] **Step 7: Verify existing tests still pass**

Run: `python -m pytest Test/ -v`
Expected: All existing tests PASS (no regressions)

- [ ] **Step 8: Commit**

```bash
git add lib/oven.py Test/test_zone.py
git commit -m "feat: add Zone class, SimulatedOutput, and get_zone_configs()"
```

---

## Task 3: get_control_temperature() and Tests

**Files:**
- Modify: `lib/oven.py` (add method to `Oven` class, around line 398)
- Modify: `Test/test_zone.py`

- [ ] **Step 1: Write tests for get_control_temperature()**

Add to `Test/test_zone.py`:

```python
class TestGetControlTemperature:
    """Test the control temperature strategy logic."""

    def _make_zone(self, temp, critical=True, error_limit=False):
        """Helper to create a Zone with a mock temp sensor."""
        from lib.oven import Zone
        zone_config = {"name": "Z", "critical": critical}
        zone = Zone(index=0, zone_config=zone_config, temp_sensor=None, output=None)
        zone.temperature = temp

        class MockStatus:
            def over_error_limit(self):
                return error_limit
        class MockSensor:
            def __init__(self):
                self.status = MockStatus()
        zone.temp_sensor = MockSensor()
        return zone

    def _make_oven_with_zones(self, zones):
        """Create a minimal Oven-like object with zones and the method under test."""
        class OvenLike:
            def __init__(self, zones):
                self.zones = zones
                self.emergency_reason = None
                self.state = "RUNNING"
            def _emergency_shutdown(self, reason):
                self.emergency_reason = reason
                self.state = "IDLE"
        oven = OvenLike(zones)
        from lib.oven import Oven
        oven.get_control_temperature = Oven.get_control_temperature.__get__(oven, OvenLike)
        return oven

    def test_coldest_strategy(self):
        z1 = self._make_zone(2100)
        z2 = self._make_zone(2050)
        z3 = self._make_zone(2080)
        oven = self._make_oven_with_zones([z1, z2, z3])
        original = getattr(config, 'zone_control_strategy', 'coldest')
        config.zone_control_strategy = "coldest"
        try:
            assert oven.get_control_temperature() == 2050
        finally:
            config.zone_control_strategy = original

    def test_hottest_strategy(self):
        z1 = self._make_zone(2100)
        z2 = self._make_zone(2050)
        oven = self._make_oven_with_zones([z1, z2])
        original = getattr(config, 'zone_control_strategy', 'coldest')
        config.zone_control_strategy = "hottest"
        try:
            assert oven.get_control_temperature() == 2100
        finally:
            config.zone_control_strategy = original

    def test_average_strategy(self):
        z1 = self._make_zone(2100)
        z2 = self._make_zone(2000)
        oven = self._make_oven_with_zones([z1, z2])
        original = getattr(config, 'zone_control_strategy', 'coldest')
        config.zone_control_strategy = "average"
        try:
            assert oven.get_control_temperature() == 2050
        finally:
            config.zone_control_strategy = original

    def test_index_strategy(self):
        z1 = self._make_zone(2100)
        z2 = self._make_zone(2050)
        oven = self._make_oven_with_zones([z1, z2])
        original = getattr(config, 'zone_control_strategy', 'coldest')
        config.zone_control_strategy = 1
        try:
            assert oven.get_control_temperature() == 2050
        finally:
            config.zone_control_strategy = original

    def test_index_out_of_range_falls_back(self):
        z1 = self._make_zone(2100)
        oven = self._make_oven_with_zones([z1])
        original = getattr(config, 'zone_control_strategy', 'coldest')
        config.zone_control_strategy = 5
        try:
            assert oven.get_control_temperature() == 2100
        finally:
            config.zone_control_strategy = original

    def test_index_with_tc_error_falls_back(self):
        z1 = self._make_zone(2100, error_limit=True)
        z2 = self._make_zone(2050)
        oven = self._make_oven_with_zones([z1, z2])
        original = getattr(config, 'zone_control_strategy', 'coldest')
        config.zone_control_strategy = 0
        try:
            assert oven.get_control_temperature() == 2050
        finally:
            config.zone_control_strategy = original

    def test_advisory_zones_excluded(self):
        z1 = self._make_zone(2100, critical=True)
        z2 = self._make_zone(1900, critical=False)
        oven = self._make_oven_with_zones([z1, z2])
        original = getattr(config, 'zone_control_strategy', 'coldest')
        config.zone_control_strategy = "coldest"
        try:
            assert oven.get_control_temperature() == 2100
        finally:
            config.zone_control_strategy = original

    def test_all_critical_zones_failed_triggers_emergency(self):
        z1 = self._make_zone(None, critical=True)
        z2 = self._make_zone(None, critical=True)
        oven = self._make_oven_with_zones([z1, z2])
        original = getattr(config, 'zone_control_strategy', 'coldest')
        config.zone_control_strategy = "coldest"
        try:
            result = oven.get_control_temperature()
            assert result == 0
            assert oven.emergency_reason is not None
            assert "All critical" in oven.emergency_reason
        finally:
            config.zone_control_strategy = original
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `python -m pytest Test/test_zone.py::TestGetControlTemperature -v`
Expected: FAIL — `get_control_temperature` does not exist on Oven yet

- [ ] **Step 3: Implement get_control_temperature() on Oven class**

Add to the `Oven` class in `lib/oven.py` (after `set_heat_rate`, around line 441):

```python
    def get_control_temperature(self):
        """Return the temperature that drives profile progression, per strategy."""
        valid_temps = [z.temperature for z in self.zones
                       if z.critical and z.temperature is not None]

        if not valid_temps:
            self._emergency_shutdown("All critical zone sensors failed")
            return 0

        strategy = getattr(config, 'zone_control_strategy', 'coldest')

        if isinstance(strategy, int):
            if 0 <= strategy < len(self.zones):
                zone = self.zones[strategy]
                if zone.temp_sensor.status.over_error_limit():
                    log.error("zone_control_strategy zone %d ('%s') has TC errors, falling back to coldest" %
                              (strategy, zone.name))
                    return min(valid_temps)
                return zone.temperature
            log.error("zone_control_strategy index %d out of range, falling back to coldest" % strategy)
            return min(valid_temps)
        elif strategy == "coldest":
            return min(valid_temps)
        elif strategy == "hottest":
            return max(valid_temps)
        elif strategy == "average":
            return sum(valid_temps) / len(valid_temps)
        else:
            return min(valid_temps)
```

Note: `self.zones` doesn't exist on `Oven` yet — it will be wired up in Task 6. The method is correct and testable via the mock approach in the tests.

- [ ] **Step 4: Run tests — verify they pass**

Run: `python -m pytest Test/test_zone.py -v`
Expected: All PASS

- [ ] **Step 5: Verify no regressions**

Run: `python -m pytest Test/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add lib/oven.py Test/test_zone.py
git commit -m "feat: add get_control_temperature() with strategy support"
```

---

## Task 4: Refactor Output Class to Accept Constructor Args

**Files:**
- Modify: `lib/oven.py` — `Output.__init__` (lines 55-60), `RealOven.__init__` (lines 2155-2164)

- [ ] **Step 1: Refactor Output to take gpio_pin and invert as args**

Change `Output.__init__` (line 55) from reading config globals to accepting constructor args:

```python
class Output(object):
    def __init__(self, gpio_pin, invert=False):
        self.active = False
        self.heater = digitalio.DigitalInOut(gpio_pin)
        self.heater.direction = digitalio.Direction.OUTPUT
        self.off = invert
        self.on = not self.off
```

- [ ] **Step 2: Update RealOven.__init__ to pass args to Output**

In `RealOven.__init__` (around line 2157), change:
```python
self.output = Output()
```
to:
```python
self.output = Output(config.gpio_heat, config.gpio_heat_invert)
```

Note: This is a temporary change — once we wire up multi-zone in Task 6, `Output` will be created per-zone. But this keeps single-zone working during the refactor.

- [ ] **Step 3: Verify existing tests still pass**

Run: `python -m pytest Test/ -v`
Expected: All PASS

- [ ] **Step 4: Verify import works**

Run: `python -c "import lib.oven; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add lib/oven.py
git commit -m "refactor: Output class takes gpio_pin and invert as constructor args"
```

---

## Task 5: Refactor Board Classes to Create Zones

**Files:**
- Modify: `lib/oven.py` — `Board` (lines 73-80), `RealBoard` (lines 81-101), `SimulatedBoard` (lines 102-110), `TempSensorReal.__init__` (lines 136-141)

This is the core wiring task. After this, `Board.zones` exists and `Board.temp_sensor` is aliased to the first zone's sensor for backward compat.

- [ ] **Step 1: Refactor TempSensorReal to accept shared SPI and lock**

Change `TempSensorReal.__init__` to accept `spi`, `cs_pin`, and `spi_lock` as constructor args instead of reading from config:

```python
class TempSensorReal(TempSensor):
    def __init__(self, spi, cs_pin, spi_lock=None):
        TempSensor.__init__(self)
        self.sleeptime = self.time_step / float(config.temperature_average_samples)
        self.temptracker = TempTracker()
        self.spi = spi
        self.cs = digitalio.DigitalInOut(cs_pin)
        self.spi_lock = spi_lock
```

Remove the `spi_setup()` method from `TempSensorReal` — SPI setup moves to `RealBoard`.

- [ ] **Step 2: Add lock acquisition to Max31855 and Max31856 raw_temp()**

In `Max31855.__init__`, accept `spi`, `cs_pin`, `spi_lock`:
```python
class Max31855(TempSensorReal):
    def __init__(self, spi, cs_pin, spi_lock=None):
        TempSensorReal.__init__(self, spi, cs_pin, spi_lock)
        log.info("thermocouple MAX31855")
        import adafruit_max31855
        self.thermocouple = adafruit_max31855.MAX31855(self.spi, self.cs)
```

In `Max31855.raw_temp()`, wrap with lock using context manager to prevent leaks:
```python
    def raw_temp(self):
        try:
            if self.spi_lock:
                with self.spi_lock:
                    return self.thermocouple.temperature_NIST
            else:
                return self.thermocouple.temperature_NIST
        except RuntimeError as rte:
            ...
```

Apply the identical `with self.spi_lock:` pattern to `Max31856.raw_temp()`. Never use bare `acquire()`/`release()` — the context manager guarantees the lock is released even if the read throws.

- [ ] **Step 3: Refactor RealBoard to create zones**

```python
class RealBoard(Board):
    def __init__(self):
        self.name = None
        self.load_libs()
        zone_configs = get_zone_configs()
        spi = self._create_spi()
        spi_lock = threading.Lock()
        self.zones = []
        for i, zc in enumerate(zone_configs):
            sensor = self._create_sensor(spi, zc["spi_cs"], spi_lock)
            output = Output(zc["gpio_heat"], zc.get("gpio_heat_invert", False))
            zone = Zone(i, zc, sensor, output)
            self.zones.append(zone)
        # Keep temp_sensor as alias to first zone for backward compat during migration
        self.temp_sensor = self.zones[0].temp_sensor
        Board.__init__(self)

    def _create_spi(self):
        if (hasattr(config, 'spi_sclk') and config.spi_sclk and
            hasattr(config, 'spi_mosi') and
            hasattr(config, 'spi_miso')):
            spi = bitbangio.SPI(config.spi_sclk, config.spi_mosi, config.spi_miso)
            log.info("Software SPI selected")
        else:
            import board
            spi = board.SPI()
            log.info("Hardware SPI selected")
        return spi

    def _create_sensor(self, spi, cs_pin, spi_lock):
        if config.max31855:
            return Max31855(spi, cs_pin, spi_lock)
        if config.max31856:
            return Max31856(spi, cs_pin, spi_lock)
```

- [ ] **Step 4: Refactor SimulatedBoard to create zones**

```python
class SimulatedBoard(Board):
    def __init__(self):
        self.name = "simulated"
        zone_configs = get_zone_configs()
        self.zones = []
        for i, zc in enumerate(zone_configs):
            sensor = TempSensorSimulated()
            output = SimulatedOutput()
            zone = Zone(i, zc, sensor, output)
            self.zones.append(zone)
        # Backward compat alias
        self.temp_sensor = self.zones[0].temp_sensor
        Board.__init__(self)
```

- [ ] **Step 5: Update Board.__init__ to start all zone sensors**

```python
class Board(object):
    def __init__(self):
        log.info("board: %s" % (self.name))
        for zone in self.zones:
            zone.temp_sensor.start()
```

- [ ] **Step 6: Verify import and existing tests**

Run: `python -c "import lib.oven; print('OK')"`
Run: `python -m pytest Test/ -v`
Expected: Both pass. Existing code still uses `self.board.temp_sensor` which is aliased.

- [ ] **Step 7: Commit**

```bash
git add lib/oven.py
git commit -m "refactor: Board creates Zone objects with per-zone sensors and outputs"
```

---

## Task 6: Wire Zones into Oven Base Class

**Files:**
- Modify: `lib/oven.py` — `Oven.__init__` (line 345), `Oven.reset()` (line 352), `Oven.get_state()` (line 1183)

This task wires `self.zones` into the Oven and updates `get_state()` to emit zone data. The `self.board.temp_sensor` alias keeps old code working; we'll remove it in Task 7.

- [ ] **Step 1: Add self.zones to Oven.__init__ and reset()**

In `Oven.__init__` (line 345), after `self.time_step = config.sensor_time_wait`, add:
```python
        self.zones = []  # populated by subclass after board init
```

In `Oven.reset()`, add after existing PID init (line 364):
```python
        # Reset per-zone PID controllers
        for zone in self.zones:
            zone.pid = PID(
                ki=zone.pid.ki, kd=zone.pid.kd, kp=zone.pid.kp
            )
            zone.heat = 0
            zone.heat_rate = 0
            zone.heat_rate_temps = []
            zone.stall_start_time = None
            zone.stall_start_temp = None
            zone.runaway_start_time = None
            zone.runaway_start_temp = None
```

- [ ] **Step 2: Wire zones in SimulatedOven.__init__**

In `SimulatedOven.__init__` (line 1902), after `self.board = SimulatedBoard()`, add:
```python
        self.zones = self.board.zones
```

- [ ] **Step 3: Wire zones in RealOven.__init__**

In `RealOven.__init__` (line 2155), after `self.board = RealBoard()`, add:
```python
        self.zones = self.board.zones
```

Remove the standalone `self.output = Output(...)` line — outputs are now per-zone in `self.zones`.

- [ ] **Step 4: Update get_state() to emit zone data**

Modify `Oven.get_state()` (line 1183). Replace the single-sensor temp read with zone-aware logic:

```python
    def get_state(self):
        # Read all zone temperatures
        for zone in self.zones:
            try:
                zone.temperature = zone.temp_sensor.temperature() + zone.thermocouple_offset
            except (AttributeError, TypeError):
                pass  # startup race

        # Control temperature drives the schedule
        if self.zones:
            temp = self.get_control_temperature()
        else:
            temp = 0

        time_for_heat_rate = self.actual_elapsed_time if getattr(config, 'use_rate_based_control', False) else self.runtime

        # Per-zone heat rates
        for zone in self.zones:
            zone.set_heat_rate(time_for_heat_rate)

        # Top-level heat is average across zones
        avg_heat = sum(z.heat for z in self.zones) / len(self.zones) if self.zones else self.heat

        state = {
            'cost': self.cost,
            'runtime': self.runtime,
            'actual_elapsed_time': self.actual_elapsed_time,
            'temperature': temp,
            'target': self.target,
            'state': self.state,
            'heat': avg_heat,
            'heat_rate': self.heat_rate,
            'totaltime': self.totaltime,
            'kwh_rate': config.kwh_rate,
            'currency_type': config.currency_type,
            'profile': self.profile.name if self.profile else None,
            'pidstats': self.zones[0].pid.pidstats if self.zones else self.pid.pidstats,
            'catching_up': self.catching_up,
            'door': 'CLOSED',
            'cooling_estimate': self.cooling_estimate if self.cooling_mode else None,
            'simulate': config.simulate,
            'emergency': self.emergency_reason,
        }

        # Add zone data when multi-zone
        if len(self.zones) > 1:
            zone_temps = [z.temperature for z in self.zones]
            state['zones'] = [{
                'index': z.index,
                'name': z.name,
                'temperature': z.temperature,
                'target': z.target,
                'heat': z.heat,
                'heat_rate': z.heat_rate,
                'deviation': z.temperature - z.target if z.target else 0,
                'critical': z.critical,
                'pidstats': z.pid.pidstats,
            } for z in self.zones]
            state['zone_spread'] = max(zone_temps) - min(zone_temps)
            state['zone_max_deviation'] = max(
                abs(z.temperature - z.target) for z in self.zones
            ) if any(z.target for z in self.zones) else 0
            state['zone_control_strategy'] = getattr(config, 'zone_control_strategy', 'coldest')
            # Identify which zone is currently driving the schedule
            # and update top-level pidstats to reflect the control zone
            control_temp = temp
            for z in self.zones:
                if z.temperature == control_temp:
                    state['control_zone_index'] = z.index
                    state['pidstats'] = z.pid.pidstats
                    break

        # v2 segment fields
        if getattr(config, 'use_rate_based_control', False) and self.profile and hasattr(self.profile, 'segments'):
            state['target_heat_rate'] = self.target_heat_rate
            state['progress'] = self.schedule_progress
            state['current_segment'] = self.current_segment_index
            state['segment_phase'] = self.segment_phase
            state['eta_seconds'] = self.estimate_remaining_time()
            state['total_segments'] = len(self.profile.segments)

        return state
```

- [ ] **Step 5: Verify tests and import**

Run: `python -m pytest Test/ -v`
Run: `python -c "import lib.oven; print('OK')"`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add lib/oven.py
git commit -m "feat: wire zones into Oven, emit zone data from get_state()"
```

---

## Task 7: Replace self.board.temp_sensor References

**Files:**
- Modify: `lib/oven.py` — ~25 call sites throughout the file

This is a mechanical find-and-replace with behavioral review at each site. The `self.board.temp_sensor` alias from Task 5 is removed after this task.

- [ ] **Step 1: Grep all references**

Run: `grep -n "self.board.temp_sensor" lib/oven.py` to get the full list (~25 sites).

- [ ] **Step 2: Replace references in categories**

**Category A — Temperature reads in the control loop and state:**
Replace with `self.get_control_temperature()` or access via `self.zones`.

**Category B — Temperature reads in run_profile() for seek-start:**
Replace with reading from zones:
```python
# Old: temp = self.board.temp_sensor.temperature() + config.thermocouple_offset
# New:
for zone in self.zones:
    zone.temperature = zone.temp_sensor.temperature() + zone.thermocouple_offset
temp = self.get_control_temperature()
```

**Category C — Temperature reads in reset_if_emergency():**
Replace with per-zone iteration (see Task 8).

**Category D — Temperature reads in SimulatedOven thermal model:**
These read `self.board.temp_sensor.simulated_temperature` to update the sim. Replace with per-zone: `for zone in self.zones: zone.temp_sensor.simulated_temperature = ...`

**Category E — Temperature reads in RealOven.heat_then_cool():**
Replace with zone iteration.

- [ ] **Step 3: Remove the temp_sensor alias from Board subclasses**

In `RealBoard.__init__` and `SimulatedBoard.__init__`, remove:
```python
self.temp_sensor = self.zones[0].temp_sensor
```

- [ ] **Step 4: Verify zero references remain**

Run: `grep -c "self.board.temp_sensor" lib/oven.py`
Expected: `0`

- [ ] **Step 5: Verify tests and import**

Run: `python -m pytest Test/ -v`
Run: `python -c "import lib.oven; print('OK')"`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add lib/oven.py
git commit -m "refactor: replace all self.board.temp_sensor with zone-aware access"
```

---

## Task 8: Per-Zone Safety Checks (reset_if_emergency)

**Files:**
- Modify: `lib/oven.py` — `reset_if_emergency()` (lines 856-940)
- Modify: `Test/test_zone.py`

- [ ] **Step 1: Write tests for per-zone safety checks**

Add to `Test/test_zone.py`:

```python
class TestPerZoneSafety:
    """Test per-zone thermocouple error, stall, and runaway detection."""

    def _make_zone(self, temp, heat, critical=True, error_pct=0):
        from lib.oven import Zone
        zone_config = {"name": "Test", "critical": critical}
        zone = Zone(index=0, zone_config=zone_config, temp_sensor=None, output=None)
        zone.temperature = temp
        zone.heat = heat

        class MockStatus:
            def __init__(self, pct):
                self._pct = pct
            def over_error_limit(self):
                return self._pct > 30
            def error_percent(self):
                return self._pct
        class MockSensor:
            def __init__(self, pct):
                self.status = MockStatus(pct)
        zone.temp_sensor = MockSensor(error_pct)
        zone.pid = type('PID', (), {'pidstats': {'out': heat}})()
        return zone

    def _make_oven_with_zones(self, zones):
        """Create a minimal Oven-like object for testing reset_if_emergency."""
        class OvenLike:
            def __init__(self, zones):
                self.zones = zones
                self.emergency_reason = None
                self.state = "RUNNING"
            def _emergency_shutdown(self, reason):
                self.emergency_reason = reason
                self.state = "IDLE"
        oven = OvenLike(zones)
        from lib.oven import Oven
        oven.reset_if_emergency = Oven.reset_if_emergency.__get__(oven, OvenLike)
        return oven

    def test_critical_zone_tc_error_calls_emergency_shutdown(self):
        """Critical zone over error limit should trigger emergency shutdown."""
        z1 = self._make_zone(2000, 0.5, critical=True, error_pct=50)
        z2 = self._make_zone(2000, 0.5, critical=True, error_pct=0)
        oven = self._make_oven_with_zones([z1, z2])
        oven.reset_if_emergency()
        assert oven.emergency_reason is not None
        assert "zone" in oven.emergency_reason.lower()

    def test_advisory_zone_tc_error_does_not_stop(self):
        """Advisory zone over error limit should NOT trigger emergency."""
        z1 = self._make_zone(2000, 0.5, critical=False, error_pct=50)
        z2 = self._make_zone(2000, 0.5, critical=True, error_pct=0)
        oven = self._make_oven_with_zones([z1, z2])
        oven.reset_if_emergency()
        assert oven.emergency_reason is None
        assert oven.state == "RUNNING"

    def test_stall_detection_triggers_per_zone(self):
        """A single stalling zone should trigger emergency even when other zones are healthy."""
        import time as time_mod
        z1 = self._make_zone(2000, 0.99, critical=True, error_pct=0)
        z1.stall_start_time = time_mod.time() - (config.stall_detect_time + 10)
        z1.stall_start_temp = 1999  # only 1 degree rise, below stall_min_temp_rise
        z2 = self._make_zone(2000, 0.5, critical=True, error_pct=0)
        oven = self._make_oven_with_zones([z1, z2])
        oven.reset_if_emergency()
        assert oven.emergency_reason is not None
        assert "stall" in oven.emergency_reason.lower()
```

- [ ] **Step 2: Refactor reset_if_emergency() for multi-zone**

In `reset_if_emergency()` (lines 856-940), replace the single-sensor TC error check with per-zone iteration:

```python
# TC error check — per zone
for zone in self.zones:
    if zone.temp_sensor.status.over_error_limit():
        if zone.critical:
            log.error("%.2f%% of recent thermocouple readings for zone '%s' failed" %
                      (zone.temp_sensor.status.error_percent(), zone.name))
            if config.ignore_tc_too_many_errors:
                log.error("Ignoring errors for zone '%s' per config" % zone.name)
            else:
                self._emergency_shutdown(
                    "Critical zone '%s' thermocouple error limit exceeded" % zone.name)
                return
        else:
            log.warning("Advisory zone '%s' TC errors over limit — continuing" % zone.name)
```

Replace single-zone stall detection with per-zone:
```python
# Stall detection — per zone
for zone in self.zones:
    if zone.pid.pidstats.get('out', 0) > 0.95:
        if zone.stall_start_time is None:
            zone.stall_start_time = time.time()
            zone.stall_start_temp = zone.temperature
        elif time.time() - zone.stall_start_time > config.stall_detect_time:
            temp_rise = zone.temperature - zone.stall_start_temp
            if temp_rise < config.stall_min_temp_rise:
                self._emergency_shutdown(
                    "Zone '%s' stall: heater >95%% for %ds with only %.1f deg rise" %
                    (zone.name, config.stall_detect_time, temp_rise))
                return
    else:
        zone.stall_start_time = None
```

Replace single-zone runaway detection with per-zone:
```python
# Runaway detection — per zone
for zone in self.zones:
    if zone.pid.pidstats.get('out', 0) < 0.05:
        if zone.runaway_start_time is None:
            zone.runaway_start_time = time.time()
            zone.runaway_start_temp = zone.temperature
        elif time.time() - zone.runaway_start_time > config.runaway_detect_time:
            temp_rise = zone.temperature - zone.runaway_start_temp
            if temp_rise > config.runaway_min_temp_rise:
                self._emergency_shutdown(
                    "Zone '%s' runaway: heater off but temp rose %.1f deg" %
                    (zone.name, temp_rise))
                return
    else:
        zone.runaway_start_time = None
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest Test/ -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add lib/oven.py Test/test_zone.py
git commit -m "feat: per-zone safety checks (TC errors, stall, runaway detection)"
```

---

## Task 9: Refactor SimulatedOven for Multi-Zone

**Files:**
- Modify: `lib/oven.py` — `SimulatedOven.__init__` (lines 1902-1924), `SimulatedOven.heat_then_cool()` (lines 2088-2150), `SimulatedOven.temp_changes()`, `SimulatedOven.heating_energy()`

- [ ] **Step 1: Add per-zone thermal variation to SimulatedOven.__init__**

After creating zones, assign each zone randomized thermal parameters:

```python
import random

# In SimulatedOven.__init__, after self.zones = self.board.zones:
for i, zone in enumerate(self.zones):
    variation = 1.0 + random.uniform(-0.1, 0.1)  # +/- 10%
    zone.sim_p_heat = config.sim_p_heat * variation
    zone.sim_c_heat = config.sim_c_heat
    zone.sim_c_oven = config.sim_c_oven
    zone.sim_R_o_nocool = config.sim_R_o_nocool
    zone.sim_R_o_cool = config.sim_R_o_cool
    zone.sim_R_ho_noair = config.sim_R_ho_noair
    zone.sim_R_ho_air = config.sim_R_ho_air
    zone.sim_t_heat = zone.temp_sensor.simulated_temperature
    zone.sim_t_oven = zone.temp_sensor.simulated_temperature
```

- [ ] **Step 2: Refactor heat_then_cool() for multi-zone simulation**

```python
    def heat_then_cool(self):
        now = datetime.datetime.now()
        for zone in self.zones:
            zone.heat = zone.pid.compute(zone.target, zone.temperature, now)
            zone.sim_t_heat += self.heating_energy(zone)
            zone.sim_t_heat, zone.sim_t_oven = self.temp_changes(zone)
            zone.temp_sensor.simulated_temperature = zone.sim_t_oven
```

- [ ] **Step 3: Refactor heating_energy() and temp_changes() to accept zone**

```python
    def heating_energy(self, zone):
        return zone.heat * zone.sim_p_heat / zone.sim_c_heat * self.get_loop_sleep_time()

    def temp_changes(self, zone):
        dt = self.get_loop_sleep_time()
        R_o = zone.sim_R_o_nocool
        R_ho = zone.sim_R_ho_noair
        t_heat = zone.sim_t_heat
        t_oven = zone.sim_t_oven
        t_env = config.sim_t_env

        dQ_ho = (t_heat - t_oven) / R_ho * dt
        t_heat -= dQ_ho / zone.sim_c_heat
        t_oven += dQ_ho / zone.sim_c_oven

        dQ_oe = (t_oven - t_env) / R_o * dt
        t_oven -= dQ_oe / zone.sim_c_oven

        return t_heat, t_oven
```

- [ ] **Step 4: Update update_cost() for multi-zone**

In `Oven.update_cost()` (around line 964):

```python
    def update_cost(self):
        # After multi-zone refactor, self.zones is always populated (even single-zone)
        avg_heat = sum(z.heat for z in self.zones) / len(self.zones) if self.zones else 0
        cost = avg_heat * config.kw_elements * config.kwh_rate * self.time_step / 3600
        self.cost += cost
```

- [ ] **Step 5: Update track_divergence() for multi-zone**

```python
    def track_divergence(self):
        if self.zones:
            max_dev = max(abs(z.target - z.temperature) for z in self.zones)
            self.divergence_samples.append(max_dev)
        else:
            self.divergence_samples.append(abs(self.target - self.temperature))
```

- [ ] **Step 6: Verify simulation runs**

Run: `python -c "import lib.oven; print('OK')"`
Run: `python -m pytest Test/ -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add lib/oven.py
git commit -m "feat: SimulatedOven multi-zone thermal model with per-zone variation"
```

---

## Task 10: Refactor RealOven for Multi-Zone

**Files:**
- Modify: `lib/oven.py` — `RealOven.__init__` (lines 2153-2164), `RealOven.heat_then_cool()` (lines 2170-2223)

- [ ] **Step 1: Update RealOven.__init__**

Remove standalone `self.output = Output(...)` (already done in Task 6 step 3). Ensure `self.zones = self.board.zones` is set.

- [ ] **Step 2: Refactor RealOven.heat_then_cool() for multi-zone**

**Critical:** Must include cooling-segment override. When the profile is in a natural-cool or controlled-cool segment, all zone outputs must be forced off — no PID computation should activate heaters during cool segments. Check the existing `heat_then_cool()` for the cooling logic to preserve.

```python
    def heat_then_cool(self):
        now = datetime.datetime.now()
        n = len(self.zones)
        zone_time_step = self.time_step / n

        # Cooling-segment override: when profile says cool, all zones cool
        is_cooling = getattr(self, 'target_heat_rate', 0)
        if isinstance(is_cooling, str) and is_cooling in ('cool', 'max_cool'):
            for zone in self.zones:
                zone.heat = 0
                zone.output.cool(zone_time_step)
            return

        for zone in self.zones:
            zone.heat = zone.pid.compute(zone.target, zone.temperature, now)
            heat_on = max(zone.heat, 0) * zone_time_step
            heat_off = zone_time_step - heat_on
            if heat_on > 0:
                zone.output.heat(heat_on)
            # Always call cool() even with heat_off=0 to ensure relay state is explicit
            zone.output.cool(heat_off)
```

Note: The exact cooling-segment detection logic must match what the existing `RealOven.heat_then_cool()` uses. Read the current implementation to preserve the exact condition. The above is illustrative — adapt to the actual field names/values used.

- [ ] **Step 3: Update the main run() loop's target assignment**

In the `Oven.run()` method (lines 1797-1894), where the target is set, add per-zone target assignment:

```python
# After computing self.target from profile:
for zone in self.zones:
    zone.target = self.target + zone.temp_offset
```

- [ ] **Step 4: Verify import**

Run: `python -c "import lib.oven; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add lib/oven.py
git commit -m "feat: RealOven multi-zone heat_then_cool with time-sliced duty cycles"
```

---

## Task 11: Resume State and set_sim_temp Multi-Zone

**Files:**
- Modify: `lib/oven.py` — `save_resume_state()` (lines 1392-1452), `resume_last_firing()` (lines 1599-1685)
- Modify: `kiln-controller.py` — `set_sim_temp` handler (lines 154-167)

- [ ] **Step 1: Extend save_resume_state() with zone_temperatures**

In `save_resume_state()`, add to the state dict:
```python
state['zone_temperatures'] = [z.temperature for z in self.zones]
```

- [ ] **Step 2: Update resume_last_firing() to use zone_temperatures**

When resuming, if `zone_temperatures` is present, use per-zone temps for seek:
```python
zone_temps = state.get('zone_temperatures', None)
if zone_temps and len(zone_temps) == len(self.zones):
    for i, zone in enumerate(self.zones):
        zone.temperature = zone_temps[i]
else:
    single_temp = state.get('temperature', self.zones[0].temp_sensor.temperature())
    for zone in self.zones:
        zone.temperature = single_temp
```

- [ ] **Step 3: Update set_sim_temp in kiln-controller.py**

Modify the handler (lines 154-167) to accept optional zone index:

```python
elif cmd == 'set_sim_temp':
    if not config.simulate:
        wsock.send(json.dumps({"error": "not in simulation mode"}))
        return
    parts = msgdata.split()
    if len(parts) < 2:
        wsock.send(json.dumps({"error": "usage: set_sim_temp <temp> [zone_index]"}))
        return
    temp = float(parts[1])
    if config.temp_scale.lower() == "f":
        temp = (temp - 32) * 5 / 9
    if len(parts) >= 3:
        zone_idx = int(parts[2])
        if 0 <= zone_idx < len(oven.zones):
            oven.zones[zone_idx].temp_sensor.simulated_temperature = temp
        else:
            wsock.send(json.dumps({"error": "zone index out of range"}))
            return
    else:
        for zone in oven.zones:
            zone.temp_sensor.simulated_temperature = temp
    wsock.send(json.dumps({"success": True}))
```

- [ ] **Step 4: Verify tests**

Run: `python -m pytest Test/ -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add lib/oven.py kiln-controller.py
git commit -m "feat: multi-zone resume state and set_sim_temp with zone index"
```

---

## Task 12: MQTT Multi-Zone Publishing

**Files:**
- Modify: `lib/mqtt.py` — `publish_state()` (lines 90-143), `_on_connect()` (lines 54-61)
- Modify: `Test/test_mqtt.py`

- [ ] **Step 1: Write test for per-zone MQTT topics**

Add to `Test/test_mqtt.py`:

```python
class TestMultiZonePublish:
    """Test MQTT publishing with multi-zone state data."""

    def test_publishes_per_zone_topics(self):
        """When state has zones, publish per-zone temperature/heat/target."""
        client = MQTTClient.__new__(MQTTClient)
        client.prefix = "kiln"
        client.publish_interval = 0
        client.connected = True
        client._last_publish = 0
        client._last_values = {}
        client.oven = None

        published = {}
        class MockMQTT:
            def publish(self, topic, payload, qos=0, retain=False):
                published[topic] = (str(payload), retain)
        client.client = MockMQTT()

        state = {
            "state": "RUNNING",
            "temperature": 2050,
            "target": 2100,
            "heat": 0.75,
            "zones": [
                {"index": 0, "name": "Top", "temperature": 2060, "target": 2100, "heat": 0.7},
                {"index": 1, "name": "Bottom", "temperature": 2040, "target": 2100, "heat": 0.8},
            ],
            "zone_spread": 20,
            "zone_max_deviation": 60,
        }
        client.publish_state(state)

        assert "kiln/zone/0/temperature" in published
        assert "kiln/zone/1/temperature" in published
        assert "kiln/zone/0/heat" in published
        assert "kiln/zone_spread" in published
        assert published["kiln/zone/0/temperature"][1] == True  # retained

    def test_no_zone_topics_for_single_zone(self):
        """When state has no zones key, no zone topics are published."""
        client = MQTTClient.__new__(MQTTClient)
        client.prefix = "kiln"
        client.publish_interval = 0
        client.connected = True
        client._last_publish = 0
        client._last_values = {}
        client.oven = None

        published = {}
        class MockMQTT:
            def publish(self, topic, payload, qos=0, retain=False):
                published[topic] = payload
        client.client = MockMQTT()

        state = {"state": "IDLE", "temperature": 65, "target": 0, "heat": 0}
        client.publish_state(state)

        assert not any("zone/" in k for k in published)
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `python -m pytest Test/test_mqtt.py::TestMultiZonePublish -v`
Expected: FAIL

- [ ] **Step 3: Extend publish_state() for per-zone topics**

In `lib/mqtt.py`, at the end of `publish_state()` (after existing topic publishing), add:

```python
        # Per-zone topics
        zones = state_dict.get("zones", [])
        for zone in zones:
            i = zone["index"]
            zone_topics = {
                "temperature": zone.get("temperature"),
                "target": zone.get("target"),
                "heat": round(zone.get("heat", 0), 3),
            }
            for key, value in zone_topics.items():
                topic = f"{self.prefix}/zone/{i}/{key}"
                last_key = f"zone/{i}/{key}"
                if value is not None and self._last_values.get(last_key) != value:
                    self.client.publish(topic, str(value), qos=0, retain=True)
                    self._last_values[last_key] = value

        # Aggregate zone metrics
        if zones:
            for key in ("zone_spread", "zone_max_deviation"):
                value = state_dict.get(key)
                if value is not None and self._last_values.get(key) != value:
                    self.client.publish(
                        f"{self.prefix}/{key}", str(round(value, 1)),
                        qos=0, retain=True)
                    self._last_values[key] = value
```

- [ ] **Step 4: Publish zone names in _on_connect()**

In `_on_connect()`, after the `available` publish:

```python
        # Publish zone names (retained, once on connect)
        zone_configs = []
        try:
            from lib.oven import get_zone_configs
            zone_configs = get_zone_configs()
        except Exception:
            pass
        for i, zc in enumerate(zone_configs):
            client.publish(f"{self.prefix}/zone/{i}/name",
                          zc.get("name", f"Zone {i}"), qos=1, retain=True)
```

- [ ] **Step 5: Run tests — verify they pass**

Run: `python -m pytest Test/test_mqtt.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add lib/mqtt.py Test/test_mqtt.py
git commit -m "feat: MQTT per-zone topics and aggregate metrics"
```

---

## Task 13: Frontend — Zone Summary Panel HTML & CSS

**Files:**
- Modify: `public/index.html` (after graph container, around line 52)
- Modify: `public/assets/css/components.css`
- Modify: `public/assets/css/responsive.css`

- [ ] **Step 1: Add zone panel container to index.html**

Wrap the graph container and zone panel in a flex container. After the existing graph area (line 51), restructure:

```html
<div class="graph-zone-wrapper">
    <div class="graph-area">
        <div id="graph_container"></div>
        <!-- existing graph overlay elements stay here -->
    </div>
    <div id="zone-panel" class="zone-panel" style="display:none;">
        <!-- Populated dynamically by picoreflow.js -->
    </div>
</div>
```

- [ ] **Step 2: Add zone panel CSS to components.css**

```css
/* Zone Summary Panel */
.graph-zone-wrapper {
    display: flex;
    gap: 12px;
}

.zone-panel {
    display: flex;
    flex-direction: column;
    gap: 8px;
    min-width: 180px;
    max-width: 200px;
}

.zone-card {
    background: var(--bg-secondary, #2a2a3e);
    border-radius: 6px;
    padding: 10px;
    border-left: 3px solid var(--zone-color, #ff6b6b);
}

.zone-card .zone-name {
    font-weight: bold;
    font-size: 0.85em;
}

.zone-card .zone-temp {
    font-size: 1.1em;
    color: var(--text-primary, #ccc);
}

.zone-card .zone-target {
    font-size: 0.8em;
    color: var(--text-secondary, #666);
}

.zone-card .zone-stats {
    font-size: 0.75em;
    color: var(--text-muted, #888);
    margin-top: 4px;
}
```

- [ ] **Step 3: Add responsive styles**

In `responsive.css`, add a breakpoint to stack the zone panel below the graph on small screens:

```css
@media (max-width: 768px) {
    .graph-zone-wrapper {
        flex-direction: column;
    }
    .zone-panel {
        flex-direction: row;
        flex-wrap: wrap;
        max-width: none;
        min-width: auto;
    }
    .zone-card {
        flex: 1;
        min-width: 140px;
    }
}
```

- [ ] **Step 4: Commit**

```bash
git add public/index.html public/assets/css/components.css public/assets/css/responsive.css
git commit -m "feat: zone summary panel HTML and CSS"
```

---

## Task 14: Frontend — Multi-Zone Graph Traces and Panel Updates

**Files:**
- Modify: `public/assets/js/picoreflow.js` — graph series (lines 85-101), status WebSocket handler (lines 1711-1959), Flot plot calls

- [ ] **Step 1: Define zone color palette**

Near the top of `picoreflow.js` (after graph series definitions around line 101), add:

```javascript
var defined_zone_colors = ['#ff6b6b', '#4ecdc4', '#ffe66d', '#a29bfe', '#fd79a8', '#00cec9'];
var zone_series = {};  // keyed by zone index
```

- [ ] **Step 2: Add zone data series on first state update**

In the WebSocket `onmessage` handler (around line 1747), when state includes zones:

```javascript
// After existing state processing:
if (msg.zones && msg.zones.length > 1) {
    // Create/update zone data series
    msg.zones.forEach(function(zone) {
        if (!zone_series[zone.index]) {
            var color = defined_zone_colors[zone.index % defined_zone_colors.length];
            zone_series[zone.index] = {
                label: zone.name,
                data: [],
                lines: { show: true, lineWidth: 2 },
                color: color,
                shadowSize: 0
            };
        }
        // Add data point
        if (msg.state === "RUNNING") {
            zone_series[zone.index].data.push([msg.runtime, zone.temperature]);
        }
    });

    // Show zone panel
    updateZonePanel(msg.zones, msg.zone_spread, msg.zone_max_deviation);
    document.getElementById('zone-panel').style.display = 'flex';
} else {
    document.getElementById('zone-panel').style.display = 'none';
}
```

- [ ] **Step 3: Include zone series in Flot plot calls**

When calling `$.plot()`, build the series array to include zone traces:

```javascript
function getPlotSeries() {
    var series = [graph.profile, graph.live];
    Object.keys(zone_series).sort().forEach(function(idx) {
        series.push(zone_series[idx]);
    });
    return series;
}
```

Replace direct `$.plot("#graph_container", [graph.profile, graph.live], ...)` calls during RUNNING state with `$.plot("#graph_container", getPlotSeries(), ...)`.

- [ ] **Step 4: Implement updateZonePanel() using safe DOM methods**

```javascript
function updateZonePanel(zones, spread, maxDeviation) {
    var panel = document.getElementById('zone-panel');
    if (!panel) return;

    // Clear existing content
    while (panel.firstChild) {
        panel.removeChild(panel.firstChild);
    }

    zones.forEach(function(zone) {
        var color = defined_zone_colors[zone.index % defined_zone_colors.length];
        var card = document.createElement('div');
        card.className = 'zone-card';
        card.style.setProperty('--zone-color', color);
        card.style.borderLeftColor = color;

        var nameEl = document.createElement('div');
        nameEl.className = 'zone-name';
        nameEl.style.color = color;
        nameEl.textContent = zone.name;
        card.appendChild(nameEl);

        var tempEl = document.createElement('div');
        tempEl.className = 'zone-temp';
        tempEl.textContent = zone.temperature.toFixed(0) + '\u00B0';
        card.appendChild(tempEl);

        var targetEl = document.createElement('div');
        targetEl.className = 'zone-target';
        targetEl.textContent = '/ ' + zone.target.toFixed(0) + '\u00B0';
        card.appendChild(targetEl);

        var statsEl = document.createElement('div');
        statsEl.className = 'zone-stats';
        var deviation = zone.deviation ? zone.deviation.toFixed(0) : '0';
        var heatPct = zone.heat ? (zone.heat * 100).toFixed(0) : '0';
        statsEl.textContent = 'Heat: ' + heatPct + '% | \u0394 ' + deviation + '\u00B0';
        card.appendChild(statsEl);

        panel.appendChild(card);
    });

    if (spread !== undefined) {
        var spreadCard = document.createElement('div');
        spreadCard.className = 'zone-card';
        spreadCard.style.setProperty('--zone-color', '#888');
        spreadCard.style.borderLeftColor = '#888';

        var spreadName = document.createElement('div');
        spreadName.className = 'zone-name';
        spreadName.style.color = '#888';
        spreadName.textContent = 'Spread';
        spreadCard.appendChild(spreadName);

        var spreadTemp = document.createElement('div');
        spreadTemp.className = 'zone-temp';
        spreadTemp.textContent = spread.toFixed(0) + '\u00B0';
        spreadCard.appendChild(spreadTemp);

        panel.appendChild(spreadCard);
    }
}
```

- [ ] **Step 5: Clear zone data on firing stop/reset**

When the state transitions to IDLE, clear zone series:
```javascript
if (msg.state === "IDLE") {
    zone_series = {};
    document.getElementById('zone-panel').style.display = 'none';
}
```

- [ ] **Step 6: Test manually in simulation**

Add a 2-zone config to `config.py` for testing:
```python
zones = [
    {"name": "Top", "spi_cs": None, "gpio_heat": None, "critical": True},
    {"name": "Bottom", "spi_cs": None, "gpio_heat": None, "critical": True, "temp_offset": -5},
]
```

Start the server: `python kiln-controller.py`
Open `http://localhost:8081`, start a firing, verify:
- Two colored traces appear on the graph
- Zone panel shows with per-zone stats
- Zone panel hides when firing stops

- [ ] **Step 7: Revert test config**

Set `zones = []` back in config.py.

- [ ] **Step 8: Commit**

```bash
git add public/assets/js/picoreflow.js
git commit -m "feat: multi-zone graph traces and zone summary panel updates"
```

---

## Task 15: Integration Testing and Verification

**Files:**
- All files modified in previous tasks

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest Test/ -v`
Expected: All PASS

- [ ] **Step 2: Verify import chain**

Run: `python -c "import lib.oven; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Smoke test single-zone (backward compat)**

Ensure `config.zones = []` (default), then:
```bash
python kiln-controller.py &
sleep 3
curl -s http://localhost:8081/api/stats | python -m json.tool
kill %1
```
Expected: JSON output with `temperature`, `state`, no `zones` key.

- [ ] **Step 4: Smoke test multi-zone simulation**

Set `config.zones` to 2-zone test config, then:
```bash
python kiln-controller.py &
sleep 3
curl -s http://localhost:8081/api/stats | python -m json.tool
kill %1
```
Expected: JSON output includes `zones` array with 2 entries, `zone_spread`, `zone_max_deviation`.

- [ ] **Step 5: Revert config and commit**

```bash
# Ensure config.py has zones = []
git add -A
git commit -m "test: integration verification of multi-zone support"
```

- [ ] **Step 6: Final check — no debug code, no temp files**

```bash
grep -rn "TODO\|FIXME\|HACK\|XXX" lib/oven.py lib/mqtt.py kiln-controller.py || echo "clean"
```

---

## Summary

| Task | Description | Files | Est. Complexity |
|------|-------------|-------|-----------------|
| 1 | Branch + config schema | config.py | Low |
| 2 | Zone, SimulatedOutput, get_zone_configs | lib/oven.py, Test/test_zone.py | Medium |
| 3 | get_control_temperature() | lib/oven.py, Test/test_zone.py | Medium |
| 4 | Output constructor refactor | lib/oven.py | Low |
| 5 | Board refactor (create zones) | lib/oven.py | High |
| 6 | Wire zones into Oven | lib/oven.py | High |
| 7 | Replace self.board.temp_sensor | lib/oven.py | High (mechanical) |
| 8 | Per-zone safety checks | lib/oven.py, Test/test_zone.py | Medium |
| 9 | SimulatedOven multi-zone | lib/oven.py | High |
| 10 | RealOven multi-zone | lib/oven.py | Medium |
| 11 | Resume state + set_sim_temp | lib/oven.py, kiln-controller.py | Medium |
| 12 | MQTT multi-zone | lib/mqtt.py, Test/test_mqtt.py | Medium |
| 13 | Frontend HTML/CSS | public/ | Low |
| 14 | Frontend JS graph + panel | public/assets/js/picoreflow.js | High |
| 15 | Integration testing | All | Low |
