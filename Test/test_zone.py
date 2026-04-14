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
