import time
import pytest
import config
from lib.oven import get_zone_configs, Zone, SimulatedOutput


# =============================================================================
# Test get_zone_configs()
# =============================================================================

class TestGetZoneConfigs:

    def test_empty_zones_returns_legacy_single_zone(self):
        original = getattr(config, 'zones', [])
        try:
            config.zones = []
            zones = get_zone_configs()
            assert len(zones) == 1
            z = zones[0]
            assert z["name"] == "Kiln"
            assert z["pid_kp"] == config.pid_kp
            assert z["temp_offset"] == 0
            assert z["critical"] is True
        finally:
            config.zones = original

    def test_no_zones_attr_returns_legacy_single_zone(self):
        had_attr = hasattr(config, 'zones')
        original = getattr(config, 'zones', None)
        try:
            if had_attr:
                del config.zones
            zones = get_zone_configs()
            assert len(zones) == 1
            z = zones[0]
            assert z["name"] == "Kiln"
            assert z["pid_kp"] == config.pid_kp
            assert z["temp_offset"] == 0
            assert z["critical"] is True
        finally:
            if had_attr:
                config.zones = original

    def test_explicit_zones_returned_as_is(self):
        original = getattr(config, 'zones', [])
        try:
            explicit = [
                {"name": "Top", "pid_kp": 10, "pid_ki": 1, "pid_kd": 100},
                {"name": "Bottom", "pid_kp": 12, "pid_ki": 2, "pid_kd": 120},
            ]
            config.zones = explicit
            zones = get_zone_configs()
            assert len(zones) == 2
            assert zones is explicit
        finally:
            config.zones = original


# =============================================================================
# Test Zone class
# =============================================================================

class _MockTempSensor:
    def __init__(self):
        self.temperature = 72.0


class _MockOutput:
    def __init__(self):
        self.active = False

    def heat(self, sleepfor):
        self.active = True

    def cool(self, sleepfor):
        self.active = False


class TestZone:

    def test_zone_init_basic(self):
        sensor = _MockTempSensor()
        output = _MockOutput()
        zone_config = {
            "name": "Top",
            "temp_offset": 5,
            "critical": True,
            "thermocouple_offset": 2,
            "pid_kp": 10.0,
            "pid_ki": 1.5,
            "pid_kd": 200.0,
        }
        z = Zone(0, zone_config, sensor, output)

        assert z.index == 0
        assert z.name == "Top"
        assert z.temp_offset == 5
        assert z.critical is True
        assert z.thermocouple_offset == 2
        assert z.temp_sensor is sensor
        assert z.output is output
        assert z.pid.kp == 10.0
        assert z.pid.ki == 1.5
        assert z.pid.kd == 200.0
        assert z.temperature == 0
        assert z.target == 0
        assert z.heat == 0
        assert z.heat_rate == 0
        assert z.heat_rate_temps == []
        assert z.stall_start_time is None
        assert z.stall_start_temp is None
        assert z.runaway_start_time is None
        assert z.runaway_start_temp is None

    def test_zone_defaults(self):
        sensor = _MockTempSensor()
        output = _MockOutput()
        # Minimal config — no pid or offset keys
        z = Zone(1, {"name": "Minimal"}, sensor, output)

        assert z.name == "Minimal"
        assert z.temp_offset == 0
        assert z.critical is True
        assert z.thermocouple_offset == 0
        assert z.pid.kp == config.pid_kp
        assert z.pid.ki == config.pid_ki
        assert z.pid.kd == config.pid_kd

    def test_zone_advisory(self):
        sensor = _MockTempSensor()
        output = _MockOutput()
        z = Zone(2, {"name": "Advisory", "critical": False}, sensor, output)
        assert z.critical is False


# =============================================================================
# Test SimulatedOutput class
# =============================================================================

class TestSimulatedOutput:

    def test_heat_does_not_sleep(self):
        out = SimulatedOutput()
        start = time.monotonic()
        out.heat(1.0)
        elapsed = time.monotonic() - start
        assert elapsed < 0.1
        assert out.active is True

    def test_cool_does_not_sleep(self):
        out = SimulatedOutput()
        out.heat(1.0)  # set active first
        start = time.monotonic()
        out.cool(1.0)
        elapsed = time.monotonic() - start
        assert elapsed < 0.1
        assert out.active is False


# =============================================================================
# Test get_control_temperature()
# =============================================================================

class TestGetControlTemperature:
    """Test the control temperature strategy logic."""

    def _make_zone(self, temp, critical=True, error_limit=False):
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
        original = getattr(config, 'zone_control_strategy', 'coldest')
        try:
            config.zone_control_strategy = 'coldest'
            zones = [self._make_zone(2100), self._make_zone(2050), self._make_zone(2080)]
            oven = self._make_oven_with_zones(zones)
            assert oven.get_control_temperature() == 2050
        finally:
            config.zone_control_strategy = original

    def test_hottest_strategy(self):
        original = getattr(config, 'zone_control_strategy', 'coldest')
        try:
            config.zone_control_strategy = 'hottest'
            zones = [self._make_zone(2100), self._make_zone(2050)]
            oven = self._make_oven_with_zones(zones)
            assert oven.get_control_temperature() == 2100
        finally:
            config.zone_control_strategy = original

    def test_average_strategy(self):
        original = getattr(config, 'zone_control_strategy', 'coldest')
        try:
            config.zone_control_strategy = 'average'
            zones = [self._make_zone(2100), self._make_zone(2000)]
            oven = self._make_oven_with_zones(zones)
            assert oven.get_control_temperature() == 2050
        finally:
            config.zone_control_strategy = original

    def test_index_strategy(self):
        original = getattr(config, 'zone_control_strategy', 'coldest')
        try:
            config.zone_control_strategy = 1
            zones = [self._make_zone(2100), self._make_zone(2050)]
            oven = self._make_oven_with_zones(zones)
            assert oven.get_control_temperature() == 2050
        finally:
            config.zone_control_strategy = original

    def test_index_out_of_range_falls_back(self):
        original = getattr(config, 'zone_control_strategy', 'coldest')
        try:
            config.zone_control_strategy = 5
            zones = [self._make_zone(2100)]
            oven = self._make_oven_with_zones(zones)
            assert oven.get_control_temperature() == 2100
        finally:
            config.zone_control_strategy = original

    def test_index_with_tc_error_falls_back(self):
        original = getattr(config, 'zone_control_strategy', 'coldest')
        try:
            config.zone_control_strategy = 0
            zones = [self._make_zone(2100, critical=True, error_limit=True), self._make_zone(2050)]
            oven = self._make_oven_with_zones(zones)
            # Zone 0 has TC errors, so falls back to coldest of valid_temps (2050)
            assert oven.get_control_temperature() == 2050
        finally:
            config.zone_control_strategy = original

    def test_advisory_zones_excluded(self):
        original = getattr(config, 'zone_control_strategy', 'coldest')
        try:
            config.zone_control_strategy = 'coldest'
            zones = [self._make_zone(2100, critical=True), self._make_zone(1900, critical=False)]
            oven = self._make_oven_with_zones(zones)
            # Advisory zone (critical=False) is excluded; only critical zone at 2100 counts
            assert oven.get_control_temperature() == 2100
        finally:
            config.zone_control_strategy = original

    def test_all_critical_zones_failed_triggers_emergency(self):
        original = getattr(config, 'zone_control_strategy', 'coldest')
        try:
            config.zone_control_strategy = 'coldest'
            zones = [self._make_zone(None), self._make_zone(None)]
            oven = self._make_oven_with_zones(zones)
            result = oven.get_control_temperature()
            assert result == 0
            assert oven.emergency_reason is not None
        finally:
            config.zone_control_strategy = original


# =============================================================================
# Test Per-Zone Safety Checks (reset_if_emergency)
# =============================================================================

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
                self.profile = None
                self.current_segment_index = 0
                self.segment_phase = 'ramp'
            def _emergency_shutdown(self, reason, status=None):
                self.emergency_reason = reason
                self.state = "IDLE"
        oven = OvenLike(zones)
        from lib.oven import Oven
        oven.reset_if_emergency = Oven.reset_if_emergency.__get__(oven, OvenLike)
        return oven

    def test_critical_zone_tc_error_calls_emergency_shutdown(self):
        """Critical zone over error limit should trigger emergency shutdown."""
        original_ignore = config.ignore_tc_too_many_errors
        try:
            config.ignore_tc_too_many_errors = False
            z1 = self._make_zone(2000, 0.5, critical=True, error_pct=50)
            z2 = self._make_zone(2000, 0.5, critical=True, error_pct=0)
            oven = self._make_oven_with_zones([z1, z2])
            oven.reset_if_emergency()
            assert oven.emergency_reason is not None
            assert (
                "zone" in oven.emergency_reason.lower()
                or "thermocouple" in oven.emergency_reason.lower()
            )
        finally:
            config.ignore_tc_too_many_errors = original_ignore

    def test_advisory_zone_tc_error_does_not_stop(self):
        """Advisory zone over error limit should NOT trigger emergency."""
        original_ignore = config.ignore_tc_too_many_errors
        try:
            config.ignore_tc_too_many_errors = False
            z1 = self._make_zone(2000, 0.5, critical=False, error_pct=50)
            z2 = self._make_zone(2000, 0.5, critical=True, error_pct=0)
            oven = self._make_oven_with_zones([z1, z2])
            oven.reset_if_emergency()
            assert oven.emergency_reason is None
            assert oven.state == "RUNNING"
        finally:
            config.ignore_tc_too_many_errors = original_ignore

    def test_stall_detection_triggers_per_zone(self):
        """A single stalling zone should trigger emergency even when other zones are healthy."""
        z1 = self._make_zone(2000, 0.99, critical=True, error_pct=0)
        z1.stall_start_time = time.time() - (config.stall_detect_time + 10)
        z1.stall_start_temp = 1999  # only 1 degree rise, below stall_min_temp_rise
        z2 = self._make_zone(2000, 0.5, critical=True, error_pct=0)
        oven = self._make_oven_with_zones([z1, z2])
        oven.reset_if_emergency()
        assert oven.emergency_reason is not None
        assert "stall" in oven.emergency_reason.lower()

    def test_no_stall_when_temp_rising(self):
        """No stall emergency when temperature is rising sufficiently."""
        z1 = self._make_zone(2050, 0.99, critical=True, error_pct=0)
        z1.stall_start_time = time.time() - (config.stall_detect_time + 10)
        z1.stall_start_temp = 2000  # 50 degree rise — above stall_min_temp_rise
        oven = self._make_oven_with_zones([z1])
        oven.reset_if_emergency()
        assert oven.emergency_reason is None

    def test_stall_timer_reset_when_duty_low(self):
        """stall_start_time should be cleared when duty cycle drops below 0.95."""
        z1 = self._make_zone(2000, 0.5, critical=True, error_pct=0)
        z1.stall_start_time = time.time() - 100
        z1.stall_start_temp = 1990
        oven = self._make_oven_with_zones([z1])
        oven.reset_if_emergency()
        assert z1.stall_start_time is None
        assert oven.emergency_reason is None

    def test_runaway_detection_triggers_per_zone(self):
        """A runaway zone should trigger emergency even when other zones are normal."""
        z1 = self._make_zone(2020, 0.01, critical=True, error_pct=0)
        z1.runaway_start_time = time.time() - (config.runaway_detect_time + 10)
        z1.runaway_start_temp = 2000  # 20 degree rise while heater off
        z2 = self._make_zone(2000, 0.5, critical=True, error_pct=0)
        oven = self._make_oven_with_zones([z1, z2])
        oven.reset_if_emergency()
        assert oven.emergency_reason is not None
        assert "runaway" in oven.emergency_reason.lower()

    def test_no_runaway_when_temp_stable(self):
        """No runaway emergency when temperature is not rising significantly."""
        z1 = self._make_zone(2001, 0.01, critical=True, error_pct=0)
        z1.runaway_start_time = time.time() - (config.runaway_detect_time + 10)
        z1.runaway_start_temp = 2000  # only 1 degree rise
        oven = self._make_oven_with_zones([z1])
        oven.reset_if_emergency()
        assert oven.emergency_reason is None

    def test_runaway_timer_reset_when_heater_on(self):
        """runaway_start_time should be cleared when duty cycle is above 0.05."""
        z1 = self._make_zone(2000, 0.5, critical=True, error_pct=0)
        z1.runaway_start_time = time.time() - 100
        z1.runaway_start_temp = 1980
        oven = self._make_oven_with_zones([z1])
        oven.reset_if_emergency()
        assert z1.runaway_start_time is None
        assert oven.emergency_reason is None
