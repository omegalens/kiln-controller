import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))

import config
from settings_schema import SETTINGS_SCHEMA, CATEGORY_ORDER

VALID_SCOPES = {"global", "kiln"}
VALID_APPLY = {"live", "next-firing", "restart"}
VALID_TYPES = {"int", "float", "bool", "str", "enum"}


def test_every_schema_key_exists_in_config():
    missing = [k for k in SETTINGS_SCHEMA if not hasattr(config, k)]
    assert missing == [], "schema keys missing from config.py: %s" % missing


def test_entry_shapes():
    for key, e in SETTINGS_SCHEMA.items():
        assert e["type"] in VALID_TYPES, key
        assert e["scope"] in VALID_SCOPES, key
        assert e["apply"] in VALID_APPLY, key
        assert e["category"] in CATEGORY_ORDER, key
        assert e.get("label"), key
        assert "help" in e, key
        if e["type"] in ("int", "float"):
            assert "min" in e and "max" in e, key
            assert e["min"] < e["max"], key
        if e["type"] == "enum":
            assert e.get("choices"), key


def test_config_defaults_are_valid_per_schema():
    """Catches range/type mistakes in the schema itself."""
    for key, e in SETTINGS_SCHEMA.items():
        default = getattr(config, key)
        t = e["type"]
        if t == "bool":
            assert isinstance(default, bool), key
        elif t == "int":
            assert isinstance(default, int) and not isinstance(default, bool), key
            assert e["min"] <= default <= e["max"], key
        elif t == "float":
            assert isinstance(default, (int, float)) and not isinstance(default, bool), key
            assert e["min"] <= default <= e["max"], key
        elif t == "str":
            if default is None:
                assert e.get("nullable"), key
            else:
                assert isinstance(default, str), key
        elif t == "enum":
            assert default in e["choices"], key


def test_only_ignore_flags_are_mid_firing_editable():
    for key, e in SETTINGS_SCHEMA.items():
        if e.get("mid_firing_editable"):
            assert key.startswith("ignore_"), key
            assert e.get("safety"), key


def test_hardware_settings_are_not_exposed():
    for forbidden in ("spi_sclk", "spi_miso", "spi_cs", "spi_mosi",
                      "gpio_heat", "gpio_heat_invert", "thermocouple_type",
                      "listening_port", "zones", "zone_control_strategy"):
        assert forbidden not in SETTINGS_SCHEMA
