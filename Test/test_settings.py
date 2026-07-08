import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))

from settings import SettingsManager, SettingsValidationError

# Small fake schema: one key per behavior class. Manager tests never use the
# real 65-key schema — that is covered by Test/test_settings_schema.py.
TEST_SCHEMA = {
    "pid_kp": {"type": "float", "min": 0.0, "max": 1000.0, "scope": "kiln",
               "apply": "next-firing", "category": "PID Tuning", "label": "Kp", "help": ""},
    "emergency_shutoff_temp": {"type": "int", "min": 100, "max": 2500, "scope": "kiln",
                               "apply": "live", "category": "Safety", "label": "Emergency",
                               "help": "", "safety": True},
    "thermocouple_offset": {"type": "float", "min": -50.0, "max": 50.0, "scope": "kiln",
                            "apply": "restart", "category": "Kiln", "label": "TC offset", "help": ""},
    "ignore_tc_short_errors": {"type": "bool", "scope": "global", "apply": "live",
                               "category": "Safety", "label": "Ignore TC short", "help": "",
                               "safety": True, "mid_firing_editable": True},
    "kwh_rate": {"type": "float", "min": 0.0, "max": 10.0, "scope": "global",
                 "apply": "live", "category": "Cost", "label": "kWh rate", "help": ""},
    "mqtt_port": {"type": "int", "min": 1, "max": 65535, "scope": "global",
                  "apply": "restart", "category": "MQTT", "label": "MQTT port", "help": ""},
    "mqtt_username": {"type": "str", "max_length": 64, "nullable": True, "scope": "global",
                      "apply": "restart", "category": "MQTT", "label": "User", "help": ""},
    "temp_scale": {"type": "enum", "choices": ["f", "c"], "scope": "global",
                   "apply": "restart", "category": "Display", "label": "Scale", "help": ""},
}


def make_config():
    cfg = types.ModuleType("fake_config")
    cfg.pid_kp = 9.8
    cfg.emergency_shutoff_temp = 2264
    cfg.thermocouple_offset = 0.0
    cfg.ignore_tc_short_errors = False
    cfg.kwh_rate = 0.43
    cfg.mqtt_port = 1883
    cfg.mqtt_username = None
    cfg.temp_scale = "f"
    cfg.simulate = True
    return cfg


@pytest.fixture
def cfg():
    return make_config()


@pytest.fixture
def manager(cfg, tmp_path):
    return SettingsManager(cfg, str(tmp_path / "settings"), schema=TEST_SCHEMA)


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)


class TestValidateValue:
    def test_float_accepts_int(self, manager):
        assert manager.validate_value("pid_kp", 12) == (12.0, None)

    def test_int_rejects_fractional_float(self, manager):
        value, error = manager.validate_value("mqtt_port", 2.5)
        assert value is None and error

    def test_int_rejects_bool(self, manager):
        value, error = manager.validate_value("mqtt_port", True)
        assert value is None and error

    def test_number_out_of_range(self, manager):
        value, error = manager.validate_value("emergency_shutoff_temp", 9999)
        assert value is None and error

    def test_bool_rejects_int(self, manager):
        value, error = manager.validate_value("ignore_tc_short_errors", 1)
        assert value is None and error

    def test_enum_rejects_unknown_choice(self, manager):
        value, error = manager.validate_value("temp_scale", "k")
        assert value is None and error

    def test_nullable_str_accepts_empty_as_none(self, manager):
        assert manager.validate_value("mqtt_username", "") == (None, None)

    def test_unknown_key(self, manager):
        value, error = manager.validate_value("no_such_setting", 1)
        assert value is None and error == "unknown setting"


class TestLoadAndApply:
    def test_global_overlay_applies(self, cfg, tmp_path):
        d = str(tmp_path / "settings")
        write_json(os.path.join(d, "global.json"), {"kwh_rate": 0.25})
        m = SettingsManager(cfg, d, schema=TEST_SCHEMA)
        m.load_and_apply()
        assert cfg.kwh_rate == 0.25

    def test_kiln_overlay_applies_over_defaults(self, cfg, tmp_path):
        d = str(tmp_path / "settings")
        write_json(os.path.join(d, "kilns", "Big Kiln.json"), {"pid_kp": 12.0})
        write_json(os.path.join(d, "active_kiln.json"), {"active": "Big Kiln"})
        m = SettingsManager(cfg, d, schema=TEST_SCHEMA)
        m.load_and_apply()
        assert cfg.pid_kp == 12.0
        assert cfg.emergency_shutoff_temp == 2264  # sparse: untouched key keeps default

    def test_corrupt_global_file_falls_back_to_defaults(self, cfg, tmp_path):
        d = str(tmp_path / "settings")
        os.makedirs(d)
        with open(os.path.join(d, "global.json"), "w") as f:
            f.write("{not json")
        m = SettingsManager(cfg, d, schema=TEST_SCHEMA)
        m.load_and_apply()  # must not raise
        assert cfg.kwh_rate == 0.43

    def test_out_of_range_overlay_value_ignored(self, cfg, tmp_path):
        d = str(tmp_path / "settings")
        write_json(os.path.join(d, "global.json"), {"kwh_rate": 999})
        m = SettingsManager(cfg, d, schema=TEST_SCHEMA)
        m.load_and_apply()
        assert cfg.kwh_rate == 0.43

    def test_wrong_scope_key_in_global_overlay_ignored(self, cfg, tmp_path):
        d = str(tmp_path / "settings")
        write_json(os.path.join(d, "global.json"), {"pid_kp": 12.0})
        m = SettingsManager(cfg, d, schema=TEST_SCHEMA)
        m.load_and_apply()
        assert cfg.pid_kp == 9.8

    def test_dangling_active_pointer_ignored(self, cfg, tmp_path):
        d = str(tmp_path / "settings")
        write_json(os.path.join(d, "active_kiln.json"), {"active": "Gone"})
        m = SettingsManager(cfg, d, schema=TEST_SCHEMA)
        m.load_and_apply()
        assert m.get_active_kiln() is None

    def test_missing_settings_dir_is_fine(self, manager, cfg):
        manager.load_and_apply()
        assert cfg.pid_kp == 9.8


class TestUpdateSettings:
    def test_live_update_applies_and_persists(self, manager, cfg, tmp_path):
        outcomes = manager.update_settings("global", {"kwh_rate": 0.30})
        assert outcomes == {"kwh_rate": "applied"}
        assert cfg.kwh_rate == 0.30
        with open(str(tmp_path / "settings" / "global.json")) as f:
            assert json.load(f) == {"kwh_rate": 0.30}

    @pytest.mark.skip(reason="needs Task 4 kiln CRUD")
    def test_next_firing_update_applies_immediately(self, manager, cfg):
        manager.create_kiln("Big Kiln")
        manager.activate_kiln("Big Kiln")
        outcomes = manager.update_settings("kiln", {"pid_kp": 11.0})
        assert outcomes == {"pid_kp": "applied-next-firing"}
        assert cfg.pid_kp == 11.0

    def test_restart_key_persisted_but_not_applied(self, manager, cfg, tmp_path):
        outcomes = manager.update_settings("global", {"mqtt_port": 8883})
        assert outcomes == {"mqtt_port": "restart-required"}
        assert cfg.mqtt_port == 1883  # running value untouched
        with open(str(tmp_path / "settings" / "global.json")) as f:
            assert json.load(f)["mqtt_port"] == 8883

    def test_all_or_nothing_on_any_invalid_value(self, manager, cfg, tmp_path):
        with pytest.raises(SettingsValidationError) as exc:
            manager.update_settings("global", {"kwh_rate": 0.3, "mqtt_port": -1})
        assert "mqtt_port" in exc.value.errors
        assert cfg.kwh_rate == 0.43  # valid sibling NOT applied
        assert not os.path.exists(str(tmp_path / "settings" / "global.json"))

    def test_wrong_scope_key_rejected(self, manager):
        with pytest.raises(SettingsValidationError) as exc:
            manager.update_settings("global", {"pid_kp": 10.0})
        assert "pid_kp" in exc.value.errors

    def test_kiln_scope_requires_active_kiln(self, manager):
        with pytest.raises(SettingsValidationError):
            manager.update_settings("kiln", {"pid_kp": 10.0})

    @pytest.mark.skip(reason="needs Task 4 kiln CRUD")
    def test_safety_key_blocked_while_firing(self, manager, cfg):
        manager.create_kiln("Big Kiln")
        manager.activate_kiln("Big Kiln")
        with pytest.raises(SettingsValidationError) as exc:
            manager.update_settings("kiln", {"emergency_shutoff_temp": 2300},
                                    oven_state="RUNNING")
        assert "emergency_shutoff_temp" in exc.value.errors
        assert cfg.emergency_shutoff_temp == 2264

    def test_ignore_flag_editable_while_firing_with_confirm(self, manager, cfg):
        outcomes = manager.update_settings(
            "global", {"ignore_tc_short_errors": True},
            oven_state="RUNNING", confirm=True)
        assert outcomes == {"ignore_tc_short_errors": "applied"}
        assert cfg.ignore_tc_short_errors is True

    def test_ignore_flag_blocked_while_firing_without_confirm(self, manager, cfg):
        with pytest.raises(SettingsValidationError):
            manager.update_settings("global", {"ignore_tc_short_errors": True},
                                    oven_state="RUNNING", confirm=False)
        assert cfg.ignore_tc_short_errors is False

    def test_reset_removes_override_and_restores_default(self, manager, cfg, tmp_path):
        manager.update_settings("global", {"kwh_rate": 0.30})
        outcomes = manager.update_settings("global", {}, reset=["kwh_rate"])
        assert outcomes == {"kwh_rate": "applied"}
        assert cfg.kwh_rate == 0.43
        with open(str(tmp_path / "settings" / "global.json")) as f:
            assert "kwh_rate" not in json.load(f)

    def test_atomic_write_leaves_no_tmp_file(self, manager, tmp_path):
        manager.update_settings("global", {"kwh_rate": 0.30})
        files = os.listdir(str(tmp_path / "settings"))
        assert not any(f.endswith(".tmp") for f in files)

    def test_invalid_scope_rejected(self, manager):
        with pytest.raises(SettingsValidationError):
            manager.update_settings("bogus", {"kwh_rate": 0.3})
