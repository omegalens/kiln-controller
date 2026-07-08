# GUI Settings Panel with Kiln Settings Profiles — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A web settings panel that edits `config.py`-backed settings via validated JSON overlays, with named per-kiln settings profiles switchable from the UI.

**Architecture:** `config.py` stays the defaults + hardware bootstrap. A new `lib/settings.py` (`SettingsManager`) loads sparse JSON overlays from `storage/settings/` at startup and `setattr`s them onto the `config` module before the oven is constructed, so all existing `config.x` call sites work unchanged. A declarative schema (`lib/settings_schema.py`) is the single source of truth for validation and frontend rendering. Thin REST handlers in `kiln-controller.py` expose it; a new standalone page `public/settings.html` + `settings.js` renders the panel.

**Tech Stack:** Python 3 / Bottle / gevent (existing), pytest, vanilla jQuery frontend (existing pattern), Playwright (verification only).

**Spec:** `docs/superpowers/specs/2026-07-07-settings-panel-design.md`
**Ledger:** `docs/superpowers/decisions/settings-panel-ledger.md`

## Global Constraints

- **No new pip dependencies.** gevent, bottle, pytest are already present.
- **Zero changes to `lib/oven.py`.** The overlay approach exists precisely to avoid touching it (ledger D101).
- Verification commands (from CLAUDE.md): `python -m pytest Test/`, `python -c "import lib.oven"`, server smoke start. There is no TypeScript/ESLint — do not run `npx` anything.
- Naming: per-kiln bundles are "kilns" in UI copy and "kiln settings profiles" in code/docs — never bare "profile" (reserved for firing profiles, ledger D103).
- Overlays are sparse: only overridden keys stored (D106). All-or-nothing writes per request (D109).
- Apply modes: `live` / `next-firing` / `restart`; restart-apply keys are persisted but NOT `setattr`'d at runtime (D107). `load_and_apply()` at startup applies everything (it runs before oven construction).
- **PHASED EXECUTION (user's global rule):** complete each phase, run verification, and WAIT for explicit user approval before starting the next phase. Each phase touches ≤5 files.
- Branch: create `feat/settings-panel` off `main`, then `git cherry-pick 0b00128` (spec + ledger docs commit currently on `feat/profile-editor-ux`) plus the spec-correction/plan commit that follows it. Verify with `git log --oneline -3`.
- Python style: match existing repo style (logging via module logger, `%`-style log formatting, no type-annotation retrofits of existing files).

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `lib/settings_schema.py` | Create | `SETTINGS_SCHEMA` + `CATEGORY_ORDER`. Pure data, no I/O. |
| `lib/settings.py` | Create | `SettingsManager`: validation, overlay load/apply, atomic persistence, kiln settings profile CRUD, activation, snapshot. No bottle imports — fully unit-testable. |
| `Test/test_settings_schema.py` | Create | Schema↔config integrity tests. |
| `Test/test_settings.py` | Create | `SettingsManager` behavior tests (fake config module + small fake schema). |
| `kiln-controller.py` | Modify | Startup wiring (load overlays before oven construction) + thin REST handlers. |
| `deploy/kiln-controller.service` | Create | Reference systemd unit with `Restart=always`. |
| `docs/api.md` | Modify | Document new endpoints. |
| `public/settings.html` | Create | Settings page skeleton. |
| `public/assets/js/settings.js` | Create | Fetch snapshot, render schema-driven form, kiln bar, save/restart flows. |
| `public/assets/css/settings.css` | Create | Settings-page layout (two-column rows collapsing at narrow widths). |
| `public/index.html` | Modify | Gear link to settings page. |
| `public/assets/css/components.css` | Modify | `.settings-gear` style only. |

---

# Phase 1 — Backend core (`lib/` + tests; 4 files)

### Task 1: Settings schema + integrity tests

**Files:**
- Create: `lib/settings_schema.py`
- Test: `Test/test_settings_schema.py`

**Interfaces:**
- Produces: `SETTINGS_SCHEMA: dict[str, dict]`, `CATEGORY_ORDER: list[str]` — imported by `lib/settings.py` (Task 2) and serialized verbatim to the frontend.

- [ ] **Step 1: Write the failing integrity tests**

```python
# Test/test_settings_schema.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source venv/bin/activate && python -m pytest Test/test_settings_schema.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'settings_schema'`

- [ ] **Step 3: Write the schema**

```python
# lib/settings_schema.py
"""Schema for GUI-exposed settings.

Single source of truth: drives server-side validation (lib/settings.py)
and frontend form rendering (public/assets/js/settings.js).

Every key MUST exist in config.py with a matching type — enforced by
Test/test_settings_schema.py.

Entry fields:
  type       "int" | "float" | "bool" | "str" | "enum"
  min / max  required for int/float (inclusive bounds)
  choices    required for enum
  nullable   str only: empty input is stored as None
  max_length str only (default 100)
  secret     str only: UI renders a password input
  scope      "kiln" (lives in the active kiln settings profile) | "global"
  apply      "live" (setattr immediately)
             "next-firing" (setattr immediately; consumed at next run start)
             "restart" (persist only; effective after server restart)
  category   one of CATEGORY_ORDER
  label      short UI label
  help       one-line UI help text
  unit       optional; literal ("s", "%", "kW", ...) or the sentinels
             "temp" / "temp_per_hr" (rendered per the active temp_scale)
  safety     True -> IDLE-only guard, confirmation styling in UI
  mid_firing_editable  True -> editable while firing WITH explicit confirm
                       (only the ignore_* thermocouple-error flags)
  sim_only   True -> UI shows the setting only in simulation mode
"""

CATEGORY_ORDER = [
    "PID Tuning", "Kiln", "Firing Behavior", "Safety", "Cost",
    "Display", "Cooling", "MQTT", "Advanced", "Simulation",
]

SETTINGS_SCHEMA = {
    # ------------------------------------------------------ kiln scope
    "pid_kp": {"type": "float", "min": 0.0, "max": 10000.0, "scope": "kiln",
               "apply": "next-firing", "category": "PID Tuning",
               "label": "Proportional gain (Kp)",
               "help": "PID proportional term. Tune per kiln (see docs/pid_tuning.md)."},
    "pid_ki": {"type": "float", "min": 0.0, "max": 10000.0, "scope": "kiln",
               "apply": "next-firing", "category": "PID Tuning",
               "label": "Integral gain (Ki)",
               "help": "Inverted: a SMALLER number means MORE integral action."},
    "pid_kd": {"type": "float", "min": 0.0, "max": 10000.0, "scope": "kiln",
               "apply": "next-firing", "category": "PID Tuning",
               "label": "Derivative gain (Kd)",
               "help": "PID derivative term."},
    "pid_control_window": {"type": "int", "min": 1, "max": 500, "scope": "kiln",
                           "apply": "live", "category": "PID Tuning",
                           "label": "PID control window", "unit": "temp", "safety": True,
                           "help": "Outside this window the elements are forced 100% on/off and no integral accumulates."},
    "kw_elements": {"type": "float", "min": 0.1, "max": 100.0, "scope": "kiln",
                    "apply": "live", "category": "Kiln",
                    "label": "Element power", "unit": "kW",
                    "help": "Total element wattage when on; used for cost estimates. Reload the dashboard to see updated estimates."},
    "thermocouple_offset": {"type": "float", "min": -50.0, "max": 50.0, "scope": "kiln",
                            "apply": "restart", "category": "Kiln",
                            "label": "Thermocouple offset", "unit": "temp",
                            "help": "Added to every reading to correct a mis-calibrated thermocouple. Takes effect after restart."},
    "emergency_shutoff_temp": {"type": "int", "min": 100, "max": 2500, "scope": "kiln",
                               "apply": "live", "category": "Safety",
                               "label": "Emergency shutoff temperature", "unit": "temp", "safety": True,
                               "help": "The firing aborts at or above this temperature."},
    "kiln_must_catch_up": {"type": "bool", "scope": "kiln", "apply": "live",
                           "category": "Firing Behavior", "label": "Kiln must catch up",
                           "help": "Pause the schedule while the temperature is outside the PID window."},
    "throttle_below_temp": {"type": "int", "min": 0, "max": 2500, "scope": "kiln",
                            "apply": "live", "category": "Firing Behavior",
                            "label": "Throttle below", "unit": "temp",
                            "help": "Below this temperature, cap element output to the throttle percentage."},
    "throttle_percent": {"type": "int", "min": 1, "max": 100, "scope": "kiln",
                         "apply": "live", "category": "Firing Behavior",
                         "label": "Throttle percentage", "unit": "%",
                         "help": "Maximum element output while below the throttle temperature. 100 disables throttling."},
    "segment_complete_tolerance": {"type": "int", "min": 1, "max": 100, "scope": "kiln",
                                   "apply": "live", "category": "Firing Behavior",
                                   "label": "Segment complete tolerance", "unit": "temp",
                                   "help": "A segment advances when within this many degrees of its target."},
    "rate_deviation_warning": {"type": "int", "min": 1, "max": 1000, "scope": "kiln",
                               "apply": "live", "category": "Firing Behavior",
                               "label": "Rate deviation warning", "unit": "temp_per_hr",
                               "help": "Log a warning when the actual rate deviates from the target rate by more than this."},
    "estimated_max_heating_rate": {"type": "int", "min": 1, "max": 2000, "scope": "kiln",
                                   "apply": "live", "category": "Firing Behavior",
                                   "label": "Estimated max heating rate", "unit": "temp_per_hr",
                                   "help": "Used to estimate the duration of \"max\" rate segments."},
    "estimated_natural_cooling_rate": {"type": "int", "min": 1, "max": 2000, "scope": "kiln",
                                       "apply": "live", "category": "Firing Behavior",
                                       "label": "Estimated natural cooling rate", "unit": "temp_per_hr",
                                       "help": "Used to estimate the duration of \"cool\" rate segments."},
    "rate_lookahead_seconds": {"type": "int", "min": 0, "max": 600, "scope": "kiln",
                               "apply": "live", "category": "Firing Behavior",
                               "label": "Rate lookahead", "unit": "s",
                               "help": "How far the rate-based target leads the actual temperature."},
    "max_target_divergence": {"type": "int", "min": 1, "max": 500, "scope": "kiln",
                              "apply": "live", "category": "Firing Behavior",
                              "label": "Max target divergence", "unit": "temp",
                              "help": "Caps how far the target may lead the actual temperature."},
    "use_rate_based_control": {"type": "bool", "scope": "kiln", "apply": "next-firing",
                               "category": "Firing Behavior", "label": "Rate-based control",
                               "help": "Use v2 rate-based control; off = legacy time-based control."},

    # ---------------------------------------------------- global scope
    "seek_start": {"type": "bool", "scope": "global", "apply": "live",
                   "category": "Firing Behavior", "label": "Seek start",
                   "help": "If the kiln is already hot when a firing starts, skip ahead to the matching point in the schedule."},
    "kwh_rate": {"type": "float", "min": 0.0, "max": 10.0, "scope": "global",
                 "apply": "live", "category": "Cost",
                 "label": "Electricity rate", "unit": "/kWh",
                 "help": "Cost per kilowatt-hour. Reload the dashboard to see updated estimates."},
    "currency_type": {"type": "str", "max_length": 5, "scope": "global",
                      "apply": "live", "category": "Cost",
                      "label": "Currency symbol",
                      "help": "Shown next to cost estimates."},
    "temp_scale": {"type": "enum", "choices": ["f", "c"], "scope": "global",
                   "apply": "restart", "category": "Display",
                   "label": "Temperature scale",
                   "help": "All temperature settings are interpreted in this scale. Requires restart and page reload."},
    "time_scale_slope": {"type": "enum", "choices": ["s", "m", "h"], "scope": "global",
                         "apply": "restart", "category": "Display",
                         "label": "Rate time unit",
                         "help": "Time unit for displayed heating rates."},
    "time_scale_profile": {"type": "enum", "choices": ["s", "m", "h"], "scope": "global",
                           "apply": "restart", "category": "Display",
                           "label": "Firing profile time unit",
                           "help": "Time unit for entering and viewing firing profile targets."},
    "graph_cutoff_temp": {"type": "int", "min": 0, "max": 2000, "scope": "global",
                          "apply": "live", "category": "Display",
                          "label": "Graph cutoff temperature", "unit": "temp",
                          "help": "Stop updating the graph once the kiln cools below this after a firing."},
    "cooling_ambient_temp": {"type": "int", "min": 0, "max": 150, "scope": "global",
                             "apply": "live", "category": "Cooling",
                             "label": "Ambient temperature", "unit": "temp",
                             "help": "Room temperature assumed for cooling estimates."},
    "cooling_target_temp": {"type": "int", "min": 0, "max": 500, "scope": "global",
                            "apply": "live", "category": "Cooling",
                            "label": "Safe-to-open temperature", "unit": "temp",
                            "help": "The cooling estimate counts down to this temperature."},
    "cooling_min_samples": {"type": "int", "min": 1, "max": 1000, "scope": "global",
                            "apply": "live", "category": "Cooling",
                            "label": "Minimum cooling samples",
                            "help": "Temperature samples required before showing a cooling estimate."},
    "stall_detect_time": {"type": "int", "min": 60, "max": 86400, "scope": "global",
                          "apply": "live", "category": "Safety",
                          "label": "Stall detection time", "unit": "s", "safety": True,
                          "help": "Abort if the heater runs above 95% for this long without the temperature rising."},
    "stall_min_temp_rise": {"type": "int", "min": 1, "max": 100, "scope": "global",
                            "apply": "live", "category": "Safety",
                            "label": "Stall minimum rise", "unit": "temp", "safety": True,
                            "help": "Minimum rise expected within the stall detection time."},
    "runaway_detect_time": {"type": "int", "min": 30, "max": 86400, "scope": "global",
                            "apply": "live", "category": "Safety",
                            "label": "Runaway detection time", "unit": "s", "safety": True,
                            "help": "Emergency stop if the heater is commanded off this long while the temperature keeps rising."},
    "runaway_min_temp_rise": {"type": "int", "min": 1, "max": 200, "scope": "global",
                              "apply": "live", "category": "Safety",
                              "label": "Runaway minimum rise", "unit": "temp", "safety": True,
                              "help": "Rise (with the heater off) that triggers the emergency stop."},
    "state_save_interval": {"type": "int", "min": 5, "max": 3600, "scope": "global",
                            "apply": "live", "category": "Advanced",
                            "label": "State save interval", "unit": "s",
                            "help": "Minimum seconds between state.json writes (SD card wear protection)."},
    "automatic_restarts": {"type": "bool", "scope": "global", "apply": "live",
                           "category": "Advanced", "label": "Automatic restarts",
                           "help": "Resume the firing automatically after a power outage."},
    "automatic_restart_window": {"type": "int", "min": 1, "max": 120, "scope": "global",
                                 "apply": "live", "category": "Advanced",
                                 "label": "Automatic restart window", "unit": "min",
                                 "help": "Maximum outage duration that still auto-resumes."},
    "sensor_time_wait": {"type": "int", "min": 1, "max": 60, "scope": "global",
                         "apply": "restart", "category": "Advanced",
                         "label": "Duty cycle", "unit": "s",
                         "help": "Seconds between relay on/off decisions."},
    "temperature_average_samples": {"type": "int", "min": 1, "max": 100, "scope": "global",
                                    "apply": "restart", "category": "Advanced",
                                    "label": "Temperature samples",
                                    "help": "Thermocouple readings per duty cycle (the median is used)."},
    "ac_freq_50hz": {"type": "bool", "scope": "global", "apply": "restart",
                     "category": "Advanced", "label": "50 Hz mains filtering",
                     "help": "Enable if your mains electricity is 50 Hz."},
    "allow_legacy_profiles": {"type": "bool", "scope": "global", "apply": "live",
                              "category": "Advanced", "label": "Allow legacy firing profiles",
                              "help": "Auto-convert v1 firing profiles on load."},
    "mqtt_enabled": {"type": "bool", "scope": "global", "apply": "restart",
                     "category": "MQTT", "label": "MQTT enabled",
                     "help": "Publish kiln status and accept stop/pause/resume over MQTT."},
    "mqtt_host": {"type": "str", "max_length": 253, "scope": "global", "apply": "restart",
                  "category": "MQTT", "label": "Broker host",
                  "help": "MQTT broker hostname or IP."},
    "mqtt_port": {"type": "int", "min": 1, "max": 65535, "scope": "global", "apply": "restart",
                  "category": "MQTT", "label": "Broker port",
                  "help": "MQTT broker port."},
    "mqtt_topic_prefix": {"type": "str", "max_length": 64, "scope": "global", "apply": "restart",
                          "category": "MQTT", "label": "Topic prefix",
                          "help": "Prefix for all published topics."},
    "mqtt_publish_interval": {"type": "int", "min": 1, "max": 3600, "scope": "global",
                              "apply": "restart", "category": "MQTT",
                              "label": "Publish interval", "unit": "s",
                              "help": "Seconds between MQTT publishes."},
    "mqtt_username": {"type": "str", "max_length": 64, "nullable": True, "scope": "global",
                      "apply": "restart", "category": "MQTT", "label": "Username",
                      "help": "Leave empty for an anonymous connection."},
    "mqtt_password": {"type": "str", "max_length": 128, "nullable": True, "secret": True,
                      "scope": "global", "apply": "restart", "category": "MQTT",
                      "label": "Password",
                      "help": "Stored in plain text in storage/settings/global.json (same trust model as config.py)."},

    # ------------------------------------------------- simulation only
    "sim_initial_temp": {"type": "float", "min": -50.0, "max": 2500.0, "scope": "global",
                         "apply": "restart", "category": "Simulation", "sim_only": True,
                         "label": "Initial temperature", "unit": "temp",
                         "help": "Sensor temperature when the simulation starts."},
    "sim_t_env": {"type": "float", "min": -50.0, "max": 2500.0, "scope": "global",
                  "apply": "restart", "category": "Simulation", "sim_only": True,
                  "label": "Ambient temperature", "unit": "temp",
                  "help": "Environment temperature for heat-loss calculations."},
    "sim_c_heat": {"type": "float", "min": 1.0, "max": 1000000.0, "scope": "global",
                   "apply": "restart", "category": "Simulation", "sim_only": True,
                   "label": "Element heat capacity", "unit": "J/K",
                   "help": "Heat capacity of the heating element."},
    "sim_c_oven": {"type": "float", "min": 1.0, "max": 1000000.0, "scope": "global",
                   "apply": "restart", "category": "Simulation", "sim_only": True,
                   "label": "Oven heat capacity", "unit": "J/K",
                   "help": "Heat capacity of the oven."},
    "sim_p_heat": {"type": "float", "min": 1.0, "max": 1000000.0, "scope": "global",
                   "apply": "restart", "category": "Simulation", "sim_only": True,
                   "label": "Heating power", "unit": "W",
                   "help": "Simulated element power."},
    "sim_R_o_nocool": {"type": "float", "min": 0.001, "max": 100.0, "scope": "global",
                       "apply": "restart", "category": "Simulation", "sim_only": True,
                       "label": "Oven-to-environment resistance", "unit": "K/W",
                       "help": "Higher = better insulation, slower cooling."},
    "sim_R_o_cool": {"type": "float", "min": 0.001, "max": 100.0, "scope": "global",
                     "apply": "restart", "category": "Simulation", "sim_only": True,
                     "label": "Oven-to-environment resistance (cooling)", "unit": "K/W",
                     "help": "Thermal resistance with cooling."},
    "sim_R_ho_noair": {"type": "float", "min": 0.001, "max": 100.0, "scope": "global",
                       "apply": "restart", "category": "Simulation", "sim_only": True,
                       "label": "Element-to-oven resistance", "unit": "K/W",
                       "help": "Thermal resistance from the element to the oven."},
    "sim_R_ho_air": {"type": "float", "min": 0.001, "max": 100.0, "scope": "global",
                     "apply": "restart", "category": "Simulation", "sim_only": True,
                     "label": "Element-to-oven resistance (air)", "unit": "K/W",
                     "help": "Thermal resistance with internal air circulation."},
    "sim_speedup_factor": {"type": "int", "min": 1, "max": 10000, "scope": "global",
                           "apply": "restart", "category": "Simulation", "sim_only": True,
                           "label": "Speedup factor",
                           "help": "Run simulations N times faster than real time."},
}

# The 12 thermocouple-error ignore flags share shape; generate them.
# mid_firing_editable is deliberate: their documented purpose is mid-firing
# triage ("ignore this error to complete a firing") — see spec section 6.
_IGNORE_FLAG_LABELS = {
    "ignore_temp_too_high": "temperature above emergency shutoff",
    "ignore_tc_lost_connection": "thermocouple lost connection",
    "ignore_tc_cold_junction_range_error": "cold junction range error",
    "ignore_tc_range_error": "thermocouple range error",
    "ignore_tc_cold_junction_temp_high": "cold junction temperature high",
    "ignore_tc_cold_junction_temp_low": "cold junction temperature low",
    "ignore_tc_temp_high": "thermocouple temperature high",
    "ignore_tc_temp_low": "thermocouple temperature low",
    "ignore_tc_voltage_error": "thermocouple voltage error",
    "ignore_tc_short_errors": "brief thermocouple error bursts",
    "ignore_tc_unknown_error": "unknown thermocouple error",
    "ignore_tc_too_many_errors": "ALL thermocouple errors (master override)",
}
for _key, _desc in _IGNORE_FLAG_LABELS.items():
    SETTINGS_SCHEMA[_key] = {
        "type": "bool", "scope": "global", "apply": "live",
        "category": "Safety",
        "label": "Ignore: %s" % _desc,
        "help": "Log and keep firing when this error occurs. Enable only for mid-firing triage you understand.",
        "safety": True, "mid_firing_editable": True,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest Test/test_settings_schema.py -v`
Expected: 5 passed. If `test_config_defaults_are_valid_per_schema` fails on a specific key, the schema's range or type for that key is wrong — fix the schema entry, not config.py.

- [ ] **Step 5: Commit**

```bash
git add lib/settings_schema.py Test/test_settings_schema.py
git commit -m "feat(settings): declarative schema for GUI-exposed settings"
```

---

### Task 2: SettingsManager — validation + overlay load/apply

**Files:**
- Create: `lib/settings.py`
- Test: `Test/test_settings.py`

**Interfaces:**
- Consumes: `SETTINGS_SCHEMA`, `CATEGORY_ORDER` from Task 1.
- Produces: `SettingsManager(config_module, settings_dir, schema=None)` with `validate_value(key, value) -> (coerced, error)`, `load_and_apply() -> dict`, `get_active_kiln() -> str|None`; `SettingsValidationError(errors: dict)`. Tasks 3–5 extend this class; Task 6+ consume it from `kiln-controller.py`.

- [ ] **Step 1: Write the failing tests**

```python
# Test/test_settings.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest Test/test_settings.py -v`
Expected: ERROR with `ModuleNotFoundError: No module named 'settings'`

- [ ] **Step 3: Write the implementation**

```python
# lib/settings.py
"""Runtime settings overlays for config.py.

config.py stays the code-level defaults + hardware bootstrap. This module
loads sparse JSON overlays from storage/settings/ and applies them onto the
imported config module via setattr, so every existing `config.x` call site
keeps working unchanged (spec D101).

Layering per key (spec section 4):
  kiln-scope key:   active kiln settings profile file, else config.py default
  global-scope key: global.json, else config.py default
Kiln-scope keys never live in global.json and vice versa.

load_and_apply() runs once at startup, BEFORE the oven is constructed, and
applies every valid override — including restart-apply keys. At runtime,
update_settings()/activate_kiln() never setattr restart-apply keys (the value
consumers cached the old value; pretending otherwise would lie to the UI).
"""
import json
import logging
import os
import re

from gevent.lock import RLock

from settings_schema import SETTINGS_SCHEMA, CATEGORY_ORDER

log = logging.getLogger(__name__)

KILN_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 _-]{0,39}$')


class SettingsValidationError(Exception):
    """Raised when a settings request is invalid. `errors` maps
    key (or field name) -> human-readable message."""
    def __init__(self, errors):
        super(SettingsValidationError, self).__init__("invalid settings: %s" % errors)
        self.errors = errors


class SettingsManager:
    def __init__(self, config_module, settings_dir, schema=None):
        self.config = config_module
        self.schema = schema if schema is not None else SETTINGS_SCHEMA
        self.settings_dir = settings_dir
        self.kilns_dir = os.path.join(settings_dir, "kilns")
        self.global_file = os.path.join(settings_dir, "global.json")
        self.active_file = os.path.join(settings_dir, "active_kiln.json")
        self._lock = RLock()
        # Captured before any overlay is applied: the config.py values.
        self.defaults = {key: getattr(config_module, key) for key in self.schema}

    # ------------------------------------------------------ file helpers

    def _read_json(self, path):
        try:
            with open(path) as f:
                return json.load(f)
        except FileNotFoundError:
            return None
        except (ValueError, OSError) as e:
            log.warning("Ignoring unreadable settings file %s: %s", path, e)
            return None

    def _write_json_atomic(self, path, data):
        """Atomic write (tmp + fsync + rename) — this is an SD card on a
        kiln controller; a torn write on power loss must not eat the file."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def _kiln_path(self, name):
        return os.path.join(self.kilns_dir, name + ".json")

    def _load_kiln_overlay(self, name):
        return self._read_json(self._kiln_path(name))

    # ------------------------------------------------------- validation

    def validate_value(self, key, value):
        """Returns (coerced_value, None) on success or (None, error_message)."""
        entry = self.schema.get(key)
        if entry is None:
            return None, "unknown setting"
        t = entry["type"]
        if t == "bool":
            if not isinstance(value, bool):
                return None, "must be true or false"
            return value, None
        if t in ("int", "float"):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None, "must be a number"
            if t == "int":
                if isinstance(value, float) and not value.is_integer():
                    return None, "must be a whole number"
                value = int(value)
            else:
                value = float(value)
            if value < entry["min"] or value > entry["max"]:
                return None, "must be between %s and %s" % (entry["min"], entry["max"])
            return value, None
        if t == "str":
            if value is None:
                if entry.get("nullable"):
                    return None, None
                return None, "must not be empty"
            if not isinstance(value, str):
                return None, "must be a string"
            value = value.strip()
            max_length = entry.get("max_length", 100)
            if len(value) > max_length:
                return None, "too long (max %d characters)" % max_length
            if value == "":
                if entry.get("nullable"):
                    return None, None
                return None, "must not be empty"
            return value, None
        if t == "enum":
            if value not in entry["choices"]:
                return None, "must be one of: %s" % ", ".join(entry["choices"])
            return value, None
        return None, "unsupported type %s" % t

    # --------------------------------------------------- startup loading

    def load_and_apply(self):
        """Apply overlays onto the config module. Startup only — runs before
        the oven is constructed, so restart-apply keys are applied too.
        Returns {key: value} of everything applied."""
        applied = {}
        applied.update(self._apply_overlay(self._read_json(self.global_file) or {}, "global"))
        active = self.get_active_kiln()
        if active:
            overlay = self._load_kiln_overlay(active)
            if overlay is None:
                log.warning("Active kiln '%s' has no settings file; using defaults", active)
            else:
                applied.update(self._apply_overlay(overlay, "kiln"))
        log.info("Applied %d setting override(s); active kiln: %s", len(applied), active)
        return applied

    def _apply_overlay(self, overlay, scope):
        applied = {}
        for key, value in overlay.items():
            entry = self.schema.get(key)
            if entry is None or entry["scope"] != scope:
                log.warning("Ignoring overlay key '%s' (unknown or not %s-scope)", key, scope)
                continue
            coerced, error = self.validate_value(key, value)
            if error:
                log.warning("Ignoring invalid overlay value %s=%r: %s", key, value, error)
                continue
            setattr(self.config, key, coerced)
            applied[key] = coerced
        return applied

    # ----------------------------------------------------- active kiln

    def get_active_kiln(self):
        data = self._read_json(self.active_file)
        name = (data or {}).get("active")
        if name and not os.path.exists(self._kiln_path(name)):
            log.warning("Active kiln pointer '%s' is dangling; ignoring", name)
            return None
        return name or None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest Test/test_settings.py Test/test_settings_schema.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add lib/settings.py Test/test_settings.py
git commit -m "feat(settings): SettingsManager validation and startup overlay loading"
```

---

### Task 3: SettingsManager — runtime updates with guards and outcomes

**Files:**
- Modify: `lib/settings.py` (append methods to `SettingsManager`)
- Test: `Test/test_settings.py` (append test classes)

**Interfaces:**
- Produces: `update_settings(scope, values, reset=None, oven_state="IDLE", confirm=False) -> {key: outcome}` where outcome is `"applied" | "applied-next-firing" | "restart-required"`; raises `SettingsValidationError`.

- [ ] **Step 1: Append the failing tests to `Test/test_settings.py`**

```python
class TestUpdateSettings:
    def test_live_update_applies_and_persists(self, manager, cfg, tmp_path):
        outcomes = manager.update_settings("global", {"kwh_rate": 0.30})
        assert outcomes == {"kwh_rate": "applied"}
        assert cfg.kwh_rate == 0.30
        with open(str(tmp_path / "settings" / "global.json")) as f:
            assert json.load(f) == {"kwh_rate": 0.30}

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
```

Note: these tests call `create_kiln`/`activate_kiln`, implemented in Task 4. Write BOTH tasks' tests in whichever order you reach them, but run the full file only after Task 4's implementation exists — OR stub the two kiln tests with `pytest.skip` markers until Task 4. Preferred: implement Task 3 and Task 4 methods in the same file edit sequence, keeping commits separate (Task 3 commit may carry two `@pytest.mark.skip` markers that Task 4 removes).

- [ ] **Step 2: Run tests to verify the new class fails**

Run: `python -m pytest Test/test_settings.py::TestUpdateSettings -v`
Expected: FAIL with `AttributeError: ... no attribute 'update_settings'`

- [ ] **Step 3: Append the implementation to `SettingsManager`**

```python
    # ------------------------------------------------- runtime updates

    def update_settings(self, scope, values, reset=None, oven_state="IDLE",
                        confirm=False):
        """Validate, persist, and apply a batch of changes. All-or-nothing:
        any error rejects the entire request (spec D109).
        Returns {key: "applied" | "applied-next-firing" | "restart-required"}."""
        reset = reset or []
        errors = {}
        coerced_values = {}

        if scope not in ("global", "kiln"):
            raise SettingsValidationError({"scope": "must be 'global' or 'kiln'"})
        active = self.get_active_kiln()
        if scope == "kiln" and not active:
            raise SettingsValidationError({"scope": "no active kiln settings profile"})

        for key in list(values.keys()) + list(reset):
            entry = self.schema.get(key)
            if entry is None:
                errors[key] = "unknown setting"
                continue
            if entry["scope"] != scope:
                errors[key] = "not a %s-scope setting" % scope
                continue
            guard_error = self._guard(entry, oven_state, confirm)
            if guard_error:
                errors[key] = guard_error

        for key, value in values.items():
            if key in errors:
                continue
            coerced, error = self.validate_value(key, value)
            if error:
                errors[key] = error
            else:
                coerced_values[key] = coerced

        if errors:
            raise SettingsValidationError(errors)

        with self._lock:
            if scope == "global":
                path = self.global_file
                overlay = self._read_json(path) or {}
            else:
                path = self._kiln_path(active)
                overlay = self._read_json(path) or {}
            for key in reset:
                overlay.pop(key, None)
            overlay.update(coerced_values)
            self._write_json_atomic(path, overlay)

        outcomes = {}
        for key, value in coerced_values.items():
            outcomes[key] = self._apply_and_outcome(key, value, oven_state)
        for key in reset:
            outcomes[key] = self._apply_and_outcome(key, self.defaults[key], oven_state)
        return outcomes

    def _guard(self, entry, oven_state, confirm):
        if not entry.get("safety") or oven_state == "IDLE":
            return None
        if entry.get("mid_firing_editable"):
            if not confirm:
                return "confirmation required while oven is %s" % oven_state
            return None
        return "cannot change a safety setting while oven is %s" % oven_state

    def _apply_and_outcome(self, key, value, oven_state):
        entry = self.schema[key]
        old = getattr(self.config, key, None)
        if entry["apply"] == "restart":
            log.info("Setting %s=%r persisted (restart required; running value %r)",
                     key, value, old)
            return "restart-required"
        setattr(self.config, key, value)
        level = logging.WARNING if entry.get("safety") else logging.INFO
        log.log(level, "Setting %s changed %r -> %r (oven %s)", key, old, value, oven_state)
        return "applied-next-firing" if entry["apply"] == "next-firing" else "applied"
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest Test/test_settings.py -v`
Expected: all pass except the two kiln-CRUD-dependent tests if you skipped them (see Step 1 note).

- [ ] **Step 5: Commit**

```bash
git add lib/settings.py Test/test_settings.py
git commit -m "feat(settings): guarded all-or-nothing runtime updates with apply outcomes"
```

---

### Task 4: SettingsManager — kiln settings profile CRUD + activation

**Files:**
- Modify: `lib/settings.py` (append methods)
- Test: `Test/test_settings.py` (append test class; remove any Task-3 skips)

**Interfaces:**
- Produces: `list_kilns() -> list[str]`, `create_kiln(name, duplicate_from=None) -> str`, `rename_kiln(old, new)`, `delete_kiln(name)`, `activate_kiln(name_or_None, oven_state="IDLE") -> {"restart_required": bool}`.

- [ ] **Step 1: Append the failing tests**

```python
class TestKilnCrud:
    def test_create_and_list(self, manager):
        manager.create_kiln("Big Kiln")
        manager.create_kiln("test-kiln")
        assert manager.list_kilns() == ["Big Kiln", "test-kiln"]

    def test_create_rejects_bad_names(self, manager):
        for bad in ("", "  ", "a/b", "..", "x" * 41, "name.with.dots", None):
            with pytest.raises(SettingsValidationError):
                manager.create_kiln(bad)

    def test_create_rejects_duplicate_case_insensitive(self, manager):
        manager.create_kiln("Big Kiln")
        with pytest.raises(SettingsValidationError):
            manager.create_kiln("big kiln")

    def test_duplicate_from_copies_overlay(self, manager, cfg):
        manager.create_kiln("Big Kiln")
        manager.activate_kiln("Big Kiln")
        manager.update_settings("kiln", {"pid_kp": 15.0})
        manager.create_kiln("Copy", duplicate_from="Big Kiln")
        manager.activate_kiln("Copy")
        assert cfg.pid_kp == 15.0

    def test_duplicate_from_missing_source(self, manager):
        with pytest.raises(SettingsValidationError):
            manager.create_kiln("Copy", duplicate_from="Nope")

    def test_rename_updates_active_pointer(self, manager):
        manager.create_kiln("Old Name")
        manager.activate_kiln("Old Name")
        manager.rename_kiln("Old Name", "New Name")
        assert manager.get_active_kiln() == "New Name"
        assert manager.list_kilns() == ["New Name"]

    def test_delete_active_refused(self, manager):
        manager.create_kiln("Big Kiln")
        manager.activate_kiln("Big Kiln")
        with pytest.raises(SettingsValidationError):
            manager.delete_kiln("Big Kiln")

    def test_delete_missing_refused(self, manager):
        with pytest.raises(SettingsValidationError):
            manager.delete_kiln("Nope")


class TestActivateKiln:
    def test_activate_applies_overlay_and_reverts_missing_keys(self, manager, cfg):
        manager.create_kiln("A")
        manager.activate_kiln("A")
        manager.update_settings("kiln", {"pid_kp": 15.0, "emergency_shutoff_temp": 2000})
        manager.create_kiln("B")
        manager.activate_kiln("B")  # B has an empty overlay
        assert cfg.pid_kp == 9.8               # reverted to default
        assert cfg.emergency_shutoff_temp == 2264

    def test_activate_null_returns_to_defaults(self, manager, cfg):
        manager.create_kiln("A")
        manager.activate_kiln("A")
        manager.update_settings("kiln", {"pid_kp": 15.0})
        result = manager.activate_kiln(None)
        assert cfg.pid_kp == 9.8
        assert manager.get_active_kiln() is None
        assert result == {"restart_required": False}

    def test_activate_blocked_while_firing(self, manager):
        manager.create_kiln("A")
        with pytest.raises(SettingsValidationError):
            manager.activate_kiln("A", oven_state="RUNNING")

    def test_activate_missing_kiln(self, manager):
        with pytest.raises(SettingsValidationError):
            manager.activate_kiln("Nope")

    def test_activate_reports_restart_required_for_restart_keys(self, manager, cfg):
        # thermocouple_offset is a restart-apply kiln key: switching to a kiln
        # with a different offset must NOT setattr it, and must report it.
        manager.create_kiln("A")
        manager.activate_kiln("A")
        manager.update_settings("kiln", {"thermocouple_offset": -4.0})
        assert cfg.thermocouple_offset == 0.0  # persisted, not applied
        result = manager.activate_kiln(None)
        assert result == {"restart_required": False}  # running value already matches defaults
        result = manager.activate_kiln("A")
        assert result == {"restart_required": True}
        assert cfg.thermocouple_offset == 0.0  # still not applied at runtime
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest Test/test_settings.py::TestKilnCrud Test/test_settings.py::TestActivateKiln -v`
Expected: FAIL with `AttributeError: ... no attribute 'create_kiln'`

- [ ] **Step 3: Append the implementation**

```python
    # ------------------------------------------- kiln settings profiles

    def list_kilns(self):
        try:
            files = os.listdir(self.kilns_dir)
        except FileNotFoundError:
            return []
        return sorted(f[:-5] for f in files if f.endswith(".json"))

    def _validate_kiln_name(self, name):
        if not isinstance(name, str) or not KILN_NAME_RE.match(name):
            raise SettingsValidationError(
                {"name": "1-40 characters: letters, numbers, spaces, - or _; "
                         "must start with a letter or number"})
        for existing in self.list_kilns():
            if existing.lower() == name.lower():
                raise SettingsValidationError(
                    {"name": "a kiln named '%s' already exists" % existing})

    def create_kiln(self, name, duplicate_from=None):
        with self._lock:
            self._validate_kiln_name(name)
            overlay = {}
            if duplicate_from:
                source = self._load_kiln_overlay(duplicate_from)
                if source is None:
                    raise SettingsValidationError(
                        {"duplicate_from": "kiln '%s' not found" % duplicate_from})
                overlay = dict(source)
            self._write_json_atomic(self._kiln_path(name), overlay)
            log.info("Created kiln settings profile '%s'%s", name,
                     " (copy of '%s')" % duplicate_from if duplicate_from else "")
        return name

    def rename_kiln(self, old, new):
        with self._lock:
            if not os.path.exists(self._kiln_path(old)):
                raise SettingsValidationError({"name": "kiln '%s' not found" % old})
            if not isinstance(new, str) or not KILN_NAME_RE.match(new):
                raise SettingsValidationError({"name": "invalid name"})
            if old.lower() != new.lower():
                # Full uniqueness check; skipped for pure case-change renames
                # of the same kiln.
                self._validate_kiln_name(new)
            pointer = self._read_json(self.active_file) or {}
            os.replace(self._kiln_path(old), self._kiln_path(new))
            if pointer.get("active") == old:
                self._write_json_atomic(self.active_file, {"active": new})
            log.info("Renamed kiln settings profile '%s' -> '%s'", old, new)

    def delete_kiln(self, name):
        with self._lock:
            if name == self.get_active_kiln():
                raise SettingsValidationError(
                    {"name": "cannot delete the active kiln settings profile"})
            try:
                os.remove(self._kiln_path(name))
            except FileNotFoundError:
                raise SettingsValidationError({"name": "kiln '%s' not found" % name})
            log.info("Deleted kiln settings profile '%s'", name)

    def activate_kiln(self, name, oven_state="IDLE"):
        """Switch the active kiln settings profile (None = defaults only).
        Applies live/next-firing kiln keys immediately; restart-apply kiln
        keys are never setattr'd at runtime — the return value reports
        whether any of them differ from the running values."""
        if oven_state != "IDLE":
            raise SettingsValidationError(
                {"state": "cannot switch kilns while oven is %s" % oven_state})
        with self._lock:
            if name is not None and not os.path.exists(self._kiln_path(name)):
                raise SettingsValidationError({"name": "kiln '%s' not found" % name})
            self._write_json_atomic(self.active_file, {"active": name})
            overlay = (self._load_kiln_overlay(name) or {}) if name else {}
            restart_required = False
            for key, entry in self.schema.items():
                if entry["scope"] != "kiln":
                    continue
                target = overlay.get(key, self.defaults[key])
                coerced, error = self.validate_value(key, target)
                if error:
                    log.warning("Ignoring invalid value %s=%r in kiln '%s': %s",
                                key, target, name, error)
                    coerced = self.defaults[key]
                if entry["apply"] == "restart":
                    if getattr(self.config, key) != coerced:
                        restart_required = True
                    continue
                setattr(self.config, key, coerced)
        log.info("Activated kiln settings profile: %s (restart_required=%s)",
                 name, restart_required)
        return {"restart_required": restart_required}
```

- [ ] **Step 4: Run the full settings suite**

Run: `python -m pytest Test/test_settings.py Test/test_settings_schema.py -v`
Expected: all pass, including the previously-skipped Task 3 tests (remove skips now).

- [ ] **Step 5: Commit**

```bash
git add lib/settings.py Test/test_settings.py
git commit -m "feat(settings): kiln settings profile CRUD and guarded activation"
```

---

### Task 5: SettingsManager — snapshot for the GET endpoint

**Files:**
- Modify: `lib/settings.py` (append methods)
- Test: `Test/test_settings.py` (append test class)

**Interfaces:**
- Produces: `snapshot(oven_state) -> dict` with keys `schema`, `category_order`, `values`, `sources`, `defaults`, `pending`, `restart_pending`, `active_kiln`, `kilns`, `oven_state`, `simulate`. Consumed verbatim by `GET /api/settings` (Task 7) and `settings.js` (Task 10).

- [ ] **Step 1: Append the failing tests**

```python
class TestSnapshot:
    def test_snapshot_shape_and_sources(self, manager, cfg):
        manager.update_settings("global", {"kwh_rate": 0.30})
        manager.create_kiln("Big Kiln")
        manager.activate_kiln("Big Kiln")
        manager.update_settings("kiln", {"pid_kp": 15.0})
        snap = manager.snapshot("IDLE")
        assert snap["values"]["kwh_rate"] == 0.30
        assert snap["sources"]["kwh_rate"] == "global"
        assert snap["sources"]["pid_kp"] == "kiln"
        assert snap["sources"]["emergency_shutoff_temp"] == "default"
        assert snap["defaults"]["kwh_rate"] == 0.43
        assert snap["active_kiln"] == "Big Kiln"
        assert snap["kilns"] == ["Big Kiln"]
        assert snap["oven_state"] == "IDLE"
        assert snap["simulate"] is True
        assert snap["schema"] is manager.schema
        json.dumps(snap)  # must be JSON-serializable

    def test_pending_reports_unapplied_restart_keys(self, manager, cfg):
        manager.update_settings("global", {"mqtt_port": 8883})
        snap = manager.snapshot("IDLE")
        assert snap["pending"] == {"mqtt_port": 8883}
        assert snap["restart_pending"] is True
        assert snap["values"]["mqtt_port"] == 1883  # running value

    def test_no_pending_when_clean(self, manager):
        snap = manager.snapshot("IDLE")
        assert snap["pending"] == {}
        assert snap["restart_pending"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest Test/test_settings.py::TestSnapshot -v`
Expected: FAIL with `AttributeError: ... no attribute 'snapshot'`

- [ ] **Step 3: Append the implementation**

```python
    # ---------------------------------------------------------- snapshot

    def effective_values(self):
        return {key: getattr(self.config, key) for key in self.schema}

    def value_sources(self):
        sources = {key: "default" for key in self.schema}
        for key in (self._read_json(self.global_file) or {}):
            if key in sources:
                sources[key] = "global"
        active = self.get_active_kiln()
        if active:
            for key in (self._load_kiln_overlay(active) or {}):
                if key in sources:
                    sources[key] = "kiln"
        return sources

    def _pending_values(self):
        """Restart-apply keys whose persisted target differs from the
        running value. Drives the UI's 'restart required' banner robustly
        across page reloads."""
        global_overlay = self._read_json(self.global_file) or {}
        active = self.get_active_kiln()
        kiln_overlay = (self._load_kiln_overlay(active) or {}) if active else {}
        pending = {}
        for key, entry in self.schema.items():
            if entry["apply"] != "restart":
                continue
            overlay = global_overlay if entry["scope"] == "global" else kiln_overlay
            target = overlay.get(key, self.defaults[key])
            if getattr(self.config, key) != target:
                pending[key] = target
        return pending

    def snapshot(self, oven_state):
        pending = self._pending_values()
        return {
            "schema": self.schema,
            "category_order": CATEGORY_ORDER,
            "values": self.effective_values(),
            "sources": self.value_sources(),
            "defaults": self.defaults,
            "pending": pending,
            "restart_pending": bool(pending),
            "active_kiln": self.get_active_kiln(),
            "kilns": self.list_kilns(),
            "oven_state": oven_state,
            "simulate": bool(getattr(self.config, "simulate", False)),
        }
```

Note: with a custom test schema, `CATEGORY_ORDER` still comes from the real module — that is fine; the frontend only uses it for section ordering.

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest Test/ -v`
Expected: all pass (including all pre-existing tests — this is the Phase 1 exit gate).
Also run: `python -c "import sys; sys.path.insert(0, 'lib'); import settings"` — clean import.

- [ ] **Step 5: Commit**

```bash
git add lib/settings.py Test/test_settings.py
git commit -m "feat(settings): snapshot with sources and pending-restart detection"
```

**PHASE 1 GATE — STOP.** Run `python -m pytest Test/` and report results. Wait for explicit user approval before Phase 2.

---

# Phase 2 — Server wiring (3 files)

### Task 6: Startup wiring in `kiln-controller.py`

**Files:**
- Modify: `kiln-controller.py` (imports/startup section, currently lines 24–41)

**Interfaces:**
- Consumes: `SettingsManager` from Phase 1.
- Produces: module-global `settings_manager`, used by Task 7 handlers.

- [ ] **Step 1: Re-read `kiln-controller.py` lines 1–65** (edit-integrity rule), then apply this edit. The overlay load MUST happen after `sys.path` gains `lib/` and BEFORE `from oven import ...` / oven construction — the oven caches config values (PID, sensor timing, thermocouple offset) at construction time.

Replace:

```python
script_dir = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, script_dir + '/lib/')
profile_path = config.kiln_profiles_directory

from oven import SimulatedOven, RealOven, Profile
from ovenWatcher import OvenWatcher
```

with:

```python
script_dir = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, script_dir + '/lib/')
profile_path = config.kiln_profiles_directory

# Apply settings overlays BEFORE importing/constructing the oven — the oven
# caches config values (PID gains, sensor timing, thermocouple offset) at
# construction time, so overlays must already be in place.
from settings import SettingsManager, SettingsValidationError
settings_manager = SettingsManager(
    config, os.path.join(script_dir, "storage", "settings"))
settings_manager.load_and_apply()

from oven import SimulatedOven, RealOven, Profile
from ovenWatcher import OvenWatcher
```

- [ ] **Step 2: Verify startup with no settings directory**

Run: `python kiln-controller.py & sleep 3; curl -s http://localhost:8081/api/stats > /dev/null && echo SERVER_OK; kill %1`
Expected: log line `Applied 0 setting override(s); active kiln: None`, then `SERVER_OK`.

- [ ] **Step 3: Verify an overlay actually applies**

```bash
mkdir -p storage/settings
echo '{"kwh_rate": 0.99}' > storage/settings/global.json
python kiln-controller.py & sleep 3
curl -s http://localhost:8081/picoreflow/index.html > /dev/null
kill %1
rm storage/settings/global.json
```
Expected: log line `Applied 1 setting override(s)`. (The `/config` WebSocket would now serve `kwh_rate: 0.99` — full API verification comes in Task 7.)

- [ ] **Step 4: Verify startup with a corrupt overlay** (spec §9: a bad settings file must never prevent the controller from starting)

```bash
echo '{not json' > storage/settings/global.json
python kiln-controller.py & sleep 3
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8081/picoreflow/index.html
kill %1
rm storage/settings/global.json
```
Expected: a WARNING log line `Ignoring unreadable settings file ... global.json`, then `200`.

- [ ] **Step 5: Run the existing suite**

Run: `python -m pytest Test/ && python -c "import lib.oven"`
Expected: all pass, clean import.

- [ ] **Step 6: Commit**

```bash
git add kiln-controller.py
git commit -m "feat(settings): apply settings overlays at startup before oven construction"
```

---

### Task 7: REST endpoints

**Files:**
- Modify: `kiln-controller.py` (add handlers after the existing `/api/pinned_logs` handlers, ~line 686)

**Interfaces:**
- Consumes: `settings_manager`, `oven`, `SettingsValidationError` (all module globals).
- Produces the API from spec §5. Handlers are deliberately thin — all logic and guard behavior is unit-tested in Phase 1; handlers are verified by curl here and by the browser gate in Phase 4. (The repo has no HTTP-route test harness and we are not adding a dependency for one.)

- [ ] **Step 1: Append the handlers**

```python
########################################################################
# Settings API — see docs/superpowers/specs/2026-07-07-settings-panel-design.md

@app.get('/api/settings')
def api_get_settings():
    bottle.response.content_type = 'application/json'
    return json.dumps(settings_manager.snapshot(oven.state))

@app.post('/api/settings')
def api_update_settings():
    payload = bottle.request.json or {}
    try:
        outcomes = settings_manager.update_settings(
            scope=payload.get("scope"),
            values=payload.get("values") or {},
            reset=payload.get("reset") or [],
            oven_state=oven.state,
            confirm=bool(payload.get("confirm")),
        )
        return {"success": True, "outcomes": outcomes}
    except SettingsValidationError as e:
        bottle.response.status = 400
        return {"success": False, "errors": e.errors}

@app.post('/api/settings/kilns')
def api_create_kiln():
    payload = bottle.request.json or {}
    try:
        name = settings_manager.create_kiln(
            payload.get("name"), payload.get("duplicate_from"))
        return {"success": True, "name": name}
    except SettingsValidationError as e:
        bottle.response.status = 400
        return {"success": False, "errors": e.errors}

@app.put('/api/settings/kilns/<name>')
def api_rename_kiln(name):
    payload = bottle.request.json or {}
    try:
        settings_manager.rename_kiln(name, payload.get("name"))
        return {"success": True}
    except SettingsValidationError as e:
        bottle.response.status = 400
        return {"success": False, "errors": e.errors}

@app.delete('/api/settings/kilns/<name>')
def api_delete_kiln(name):
    try:
        settings_manager.delete_kiln(name)
        return {"success": True}
    except SettingsValidationError as e:
        bottle.response.status = 400
        return {"success": False, "errors": e.errors}

@app.post('/api/settings/active_kiln')
def api_activate_kiln():
    payload = bottle.request.json or {}
    try:
        result = settings_manager.activate_kiln(
            payload.get("name"), oven_state=oven.state)
        return {"success": True, "restart_required": result["restart_required"]}
    except SettingsValidationError as e:
        bottle.response.status = 400
        return {"success": False, "errors": e.errors}

@app.post('/api/settings/restart')
def api_restart():
    """Exit cleanly so systemd (Restart=always) brings the server back up.
    os._exit is deliberate: sys.exit inside a gevent handler only kills the
    greenlet. The oven is IDLE (guarded below), so there is no firing state
    to lose; state.json is written periodically regardless."""
    if oven.state != "IDLE":
        bottle.response.status = 409
        return {"success": False,
                "error": "cannot restart while oven is %s" % oven.state}
    log.warning("Restart requested via /api/settings/restart; exiting for systemd restart")
    gevent.spawn_later(0.5, os._exit, 0)
    return {"success": True}
```

- [ ] **Step 2: curl verification script** (run each; server started with `python kiln-controller.py &`)

```bash
# snapshot
curl -s http://localhost:8081/api/settings | python -m json.tool | head -20
# expect: schema/values/sources/... keys, "active_kiln": null

# valid global update (live)
curl -s -X POST http://localhost:8081/api/settings \
  -H 'Content-Type: application/json' \
  -d '{"scope":"global","values":{"kwh_rate":0.25}}'
# expect: {"success": true, "outcomes": {"kwh_rate": "applied"}}

# invalid value -> 400, all-or-nothing
curl -s -w '\n%{http_code}\n' -X POST http://localhost:8081/api/settings \
  -H 'Content-Type: application/json' \
  -d '{"scope":"global","values":{"kwh_rate":0.30,"mqtt_port":-1}}'
# expect: {"success": false, "errors": {"mqtt_port": "..."}} and 400
curl -s http://localhost:8081/api/settings | python -c "import sys,json; print(json.load(sys.stdin)['values']['kwh_rate'])"
# expect: 0.25 (the 0.30 sibling was NOT applied)

# restart-apply key -> pending
curl -s -X POST http://localhost:8081/api/settings \
  -H 'Content-Type: application/json' \
  -d '{"scope":"global","values":{"mqtt_port":8883}}'
# expect: outcomes {"mqtt_port": "restart-required"}
curl -s http://localhost:8081/api/settings | python -c "import sys,json; s=json.load(sys.stdin); print(s['restart_pending'], s['pending'])"
# expect: True {'mqtt_port': 8883}

# kiln lifecycle
curl -s -X POST http://localhost:8081/api/settings/kilns -H 'Content-Type: application/json' -d '{"name":"Test Kiln"}'
curl -s -X POST http://localhost:8081/api/settings/active_kiln -H 'Content-Type: application/json' -d '{"name":"Test Kiln"}'
curl -s -X POST http://localhost:8081/api/settings -H 'Content-Type: application/json' -d '{"scope":"kiln","values":{"pid_kp":12.5}}'
curl -s -X PUT "http://localhost:8081/api/settings/kilns/Test%20Kiln" -H 'Content-Type: application/json' -d '{"name":"Renamed Kiln"}'
curl -s -X DELETE "http://localhost:8081/api/settings/kilns/Renamed%20Kiln"
# expect: 400 {"errors": {"name": "cannot delete the active kiln settings profile"}}
curl -s -X POST http://localhost:8081/api/settings/active_kiln -H 'Content-Type: application/json' -d '{"name":null}'
curl -s -X DELETE "http://localhost:8081/api/settings/kilns/Renamed%20Kiln"
# expect: {"success": true}

# restart (do this LAST; without systemd locally the process just exits)
curl -s -X POST http://localhost:8081/api/settings/restart
# expect: {"success": true}; the local process exits ~0.5 s later
```

Then clean up test state: `rm -rf storage/settings` (dev machine only).

- [ ] **Step 3: Run the suite + import smoke**

Run: `python -m pytest Test/ && python -c "import lib.oven"`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add kiln-controller.py
git commit -m "feat(settings): REST API for settings, kiln settings profiles, and restart"
```

---

### Task 8: systemd unit + API docs

**Files:**
- Create: `deploy/kiln-controller.service`
- Modify: `docs/api.md`

- [ ] **Step 1: Write the unit file** (reference copy; the live one is `/etc/systemd/system/kiln-controller.service` on the Pi)

```ini
# deploy/kiln-controller.service
# Reference unit for the Raspberry Pi. Install:
#   sudo cp deploy/kiln-controller.service /etc/systemd/system/
#   sudo systemctl daemon-reload && sudo systemctl restart kiln-controller
# Restart=always is REQUIRED for the GUI restart button (the server exits
# cleanly and relies on systemd to come back up) and also fixes crash
# recovery, which the previous unit lacked.
[Unit]
Description=kiln-controller
After=network.target

[Service]
User=user
ExecStart=/home/user/kiln-controller/venv/bin/python /home/user/kiln-controller/kiln-controller.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Document the endpoints in `docs/api.md`** — append a "Settings API" section listing the seven endpoints with request/response examples matching Task 7's curl script exactly (same payloads, same responses). State the guard rules: safety keys and kiln switching are IDLE-only; `ignore_*` flags need `"confirm": true` while firing; restart endpoint returns 409 while firing.

- [ ] **Step 3: Commit**

```bash
git add deploy/kiln-controller.service docs/api.md
git commit -m "docs(settings): systemd unit with Restart=always + settings API docs"
```

**PHASE 2 GATE — STOP.** Report curl results and test suite status. Wait for explicit user approval before Phase 3.

---

# Phase 3 — Frontend (5 files)

### Task 9: Settings page skeleton + gear link + ledger visual rows

**Files:**
- Create: `public/settings.html`
- Create: `public/assets/css/settings.css`
- Modify: `public/index.html` (gear link)
- Modify: `public/assets/css/components.css` (gear style only)
- Modify: `docs/superpowers/decisions/settings-panel-ledger.md` (append D111–D115)

- [ ] **Step 1: Append visual decision rows to the ledger** (Mockup ref justified-N/A: these decisions were made textually during planning; no mockups were produced. The gate verifies the decided-behavior sentences in a real browser.)

```markdown
| D111 | Settings are reached via a gear icon in the Data Insights widget title bar linking to a standalone settings.html page, explicitly NOT a modal inside index.html. | N/A (textual decision) | 320, 375, 1024 | §8 | Task 9 | — (browser gate) | PENDING |
| D112 | The settings form is rendered entirely from the server schema — every schema key appears on the page and the JS hardcodes no setting names, explicitly NOT hand-built per-setting markup. | N/A (textual decision) | 1024 | §2, §8 | Tasks 10 | — (browser gate: rendered-row count equals schema-key count) | PENDING |
| D113 | A kiln bar at the top provides select + Activate/New/Duplicate/Rename/Delete, disabled with an explanatory hint while the oven is not IDLE, explicitly NOT kiln management buried in a submenu. | N/A (textual decision) | 320, 375, 1024 | §8 | Tasks 9, 10 | — (browser gate) | PENDING |
| D114 | Safety-flagged inputs are disabled while firing, EXCEPT ignore_* flags which stay editable behind a typed IGNORE confirmation, explicitly NOT a blanket lockout of the whole panel. | N/A (textual decision) | 1024 | §6, §8 | Task 11 | — (browser gate) | PENDING |
| D115 | Saves returning restart-required outcomes raise a persistent banner with a Restart button (driven by server-side restart_pending, so it survives reloads), explicitly NOT a transient toast. | N/A (textual decision) | 320, 1024 | §8 | Task 11 | — (browser gate) | PENDING |
```

- [ ] **Step 2: Write `public/settings.html`**

```html
<!DOCTYPE html>
<html lang="en">

<head>
  <title>Kiln Controller — Settings</title>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">

  <script src="assets/js/jquery-1.10.2.min.js"></script>
  <script src="assets/js/bootstrap.min.js"></script>
  <script src="assets/js/jquery.bootstrap-growl.min.js"></script>
  <script src="assets/js/settings.js"></script>

  <link rel="stylesheet" href="assets/css/variables.css" />
  <link rel="stylesheet" href="assets/css/layout.css" />
  <link rel="stylesheet" href="assets/css/widgets.css" />
  <link rel="stylesheet" href="assets/css/components.css" />
  <link rel="stylesheet" href="assets/css/settings.css" />
  <link rel="stylesheet" href="assets/css/responsive.css" />
</head>

<body class="kiln-dashboard settings-page">
  <main class="settings-main">
    <header class="settings-header">
      <a href="index.html" class="btn btn-secondary settings-back">&larr; Dashboard</a>
      <h1 class="settings-title">Settings</h1>
      <span class="settings-oven-state" id="oven-state-chip">—</span>
    </header>

    <div id="restart-banner" class="restart-banner" style="display:none;">
      <span>Some saved changes need a server restart to take effect.</span>
      <button id="btn_restart" class="btn btn-danger">Restart Controller</button>
      <span id="restart-status"></span>
    </div>

    <section class="widget settings-widget">
      <h2 class="widget-title">Kiln
        <span id="active-kiln-label" class="active-kiln-label"></span>
      </h2>
      <div class="widget-content">
        <div class="kiln-bar">
          <select id="kiln-select" class="form-input"></select>
          <button id="btn_kiln_activate" class="btn btn-success">Activate</button>
          <button id="btn_kiln_new" class="btn btn-icon">+ New</button>
          <button id="btn_kiln_dup" class="btn btn-icon">Duplicate</button>
          <button id="btn_kiln_rename" class="btn btn-icon">Rename</button>
          <button id="btn_kiln_delete" class="btn btn-danger">Delete</button>
        </div>
        <p id="kiln-bar-hint" class="settings-hint" style="display:none;"></p>
        <div id="kiln-settings"></div>
        <div class="settings-save-row">
          <button id="btn_save_kiln" class="btn btn-success" disabled>Save Kiln Settings</button>
        </div>
      </div>
    </section>

    <section class="widget settings-widget">
      <h2 class="widget-title">Global</h2>
      <div class="widget-content">
        <div id="global-settings"></div>
        <div class="settings-save-row">
          <button id="btn_save_global" class="btn btn-success" disabled>Save Global Settings</button>
        </div>
      </div>
    </section>
  </main>
</body>

</html>
```

- [ ] **Step 3: Write `public/assets/css/settings.css`**

```css
/* Settings page — schema-driven form layout.
   Rows are a two-column grid (label+help | control) that stacks below 480px. */

.settings-main {
  max-width: 960px;
  margin: 0 auto;
  padding: var(--space-md, 16px);
  display: flex;
  flex-direction: column;
  gap: var(--space-md, 16px);
}

.settings-header {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.settings-title { margin: 0; flex: 1; }

.settings-oven-state {
  font-size: 0.8em;
  padding: 2px 10px;
  border-radius: 10px;
  background: var(--color-surface-raised, #333);
}

.restart-banner {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 10px 14px;
  border-radius: 6px;
  background: var(--color-warning-bg, #4a3200);
  border: 1px solid var(--color-warning, #c90);
}

.kiln-bar {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
  margin-bottom: 10px;
}

.kiln-bar select { min-width: 160px; flex: 1 1 160px; }

.active-kiln-label { font-size: 0.7em; opacity: 0.8; margin-left: 8px; }

.settings-hint { opacity: 0.7; font-size: 0.9em; margin: 4px 0 10px; }

.settings-category { margin: 18px 0 6px; }
.settings-category h3 {
  font-size: 0.95em;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  opacity: 0.75;
  margin: 0 0 6px;
  border-bottom: 1px solid var(--color-border, #444);
  padding-bottom: 4px;
}

.setting-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(180px, 260px);
  gap: 4px 16px;
  align-items: center;
  padding: 8px 0;
}

.setting-row.setting-dirty { background: rgba(255, 255, 0, 0.04); }

.setting-label { font-weight: 600; }

.setting-source {
  font-size: 0.7em;
  padding: 1px 6px;
  border-radius: 8px;
  margin-left: 6px;
  background: var(--color-surface-raised, #333);
  opacity: 0.8;
}
.setting-source.source-kiln { background: #1e4620; }
.setting-source.source-global { background: #1e3a46; }
.setting-source.source-pending { background: #4a3200; }

.setting-help { font-size: 0.8em; opacity: 0.65; margin: 2px 0 0; }

.setting-control {
  display: flex;
  align-items: center;
  gap: 6px;
  justify-content: flex-end;
  flex-wrap: wrap;
}

.setting-control input[type="number"],
.setting-control input[type="text"],
.setting-control input[type="password"],
.setting-control select {
  width: 120px;
  max-width: 100%;
}

.setting-unit { font-size: 0.85em; opacity: 0.7; min-width: 2.5em; }

.setting-reset {
  background: none;
  border: none;
  cursor: pointer;
  opacity: 0.5;
  font-size: 0.85em;
}
.setting-reset:hover { opacity: 1; }

.setting-error { color: var(--color-danger, #e55); font-size: 0.8em; grid-column: 1 / -1; }

.settings-save-row {
  display: flex;
  justify-content: flex-end;
  padding-top: 10px;
  border-top: 1px solid var(--color-border, #444);
}

@media (max-width: 480px) {
  .setting-row { grid-template-columns: 1fr; }
  .setting-control { justify-content: flex-start; }
  .kiln-bar .btn { flex: 1 1 auto; }
}
```

Adjust the `var(--...)` fallbacks to the actual token names in `variables.css` — read that file first and use its real tokens; the fallbacks above are only safety nets.

- [ ] **Step 4: Add the gear link to `public/index.html`**

Replace:

```html
    <section class="widget widget-insights">
      <h2 class="widget-title">Data Insights</h2>
```

with:

```html
    <section class="widget widget-insights">
      <h2 class="widget-title widget-title-row">Data Insights
        <a href="settings.html" class="settings-gear" title="Settings" aria-label="Settings">&#9881;</a>
      </h2>
```

And append to `public/assets/css/components.css`:

```css
/* Gear link to the settings page (in the Data Insights title bar) */
.widget-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.settings-gear {
  font-size: 1.1em;
  line-height: 1;
  padding: 2px 8px;
  border-radius: 6px;
  text-decoration: none;
  color: inherit;
  opacity: 0.6;
}
.settings-gear:hover,
.settings-gear:focus { opacity: 1; text-decoration: none; }
```

- [ ] **Step 5: Verify skeleton renders**

Start the server, then with Playwright (or a browser): navigate to `http://localhost:8081/picoreflow/settings.html`. Expected: header with back-link, empty Kiln and Global widgets, no console 404s except `settings.js` (created next task — acceptable at this step only if you create an empty `public/assets/js/settings.js` placeholder now; do that: `echo "// populated in the next task" > public/assets/js/settings.js` and include it in the commit). Navigate to `http://localhost:8081/picoreflow/index.html` — gear visible in the Data Insights title, clicking it lands on the settings page.

- [ ] **Step 6: Commit**

```bash
git add public/settings.html public/assets/css/settings.css public/index.html \
        public/assets/css/components.css public/assets/js/settings.js \
        docs/superpowers/decisions/settings-panel-ledger.md
git commit -m "feat(ui): settings page skeleton, gear link, ledger visual rows D111-D115"
```

---

### Task 10: settings.js — fetch, render, kiln bar

**Files:**
- Modify: `public/assets/js/settings.js` (replace placeholder)

**Interfaces:**
- Consumes: `GET /api/settings` snapshot (Task 5 shape), kiln endpoints (Task 7).
- Produces: rendered form + working kiln bar. Save flow lands in Task 11 — this task renders inputs and tracks dirty state but the save buttons only log.

- [ ] **Step 1: Write the rendering core**

```javascript
// public/assets/js/settings.js
// Settings panel — rendered entirely from the server schema (ledger D112).
// No setting names are hardcoded here.

(function () {
  'use strict';

  var API = '/api/settings';
  var snap = null;                       // last GET /api/settings payload
  var dirty = { global: {}, kiln: {} };  // key -> new raw value
  var resets = { global: {}, kiln: {} }; // key -> true

  function tempUnit() { return snap.values.temp_scale === 'c' ? '°C' : '°F'; }

  function unitText(entry) {
    if (!entry.unit) return '';
    if (entry.unit === 'temp') return tempUnit();
    if (entry.unit === 'temp_per_hr') return tempUnit() + '/hr';
    return entry.unit;
  }

  function displayValue(key) {
    // Pending restart-apply target beats the running value in the form.
    if (Object.prototype.hasOwnProperty.call(snap.pending, key)) return snap.pending[key];
    return snap.values[key];
  }

  function hasDirty() {
    return Object.keys(dirty.global).length + Object.keys(dirty.kiln).length +
           Object.keys(resets.global).length + Object.keys(resets.kiln).length > 0;
  }

  function isLocked(entry) {
    if (snap.oven_state === 'IDLE') return false;
    return !!entry.safety && !entry.mid_firing_editable;
  }

  function controlFor(key, entry) {
    var value = displayValue(key);
    var $input;
    if (entry.type === 'bool') {
      $input = $('<input type="checkbox">').prop('checked', value === true);
    } else if (entry.type === 'enum') {
      $input = $('<select class="form-input">');
      entry.choices.forEach(function (c) {
        $input.append($('<option>').val(c).text(c));
      });
      $input.val(value);
    } else if (entry.type === 'str') {
      $input = $('<input class="form-input">')
        .attr('type', entry.secret ? 'password' : 'text')
        .val(value === null ? '' : value);
    } else {
      $input = $('<input type="number" class="form-input">')
        .attr({ min: entry.min, max: entry.max, step: entry.type === 'int' ? 1 : 'any' })
        .val(value);
    }
    $input.attr('data-key', key);
    if (isLocked(entry)) {
      $input.prop('disabled', true)
        .attr('title', 'Safety setting — locked while the oven is ' + snap.oven_state);
    }
    return $input;
  }

  function readInput($input, entry) {
    if (entry.type === 'bool') return $input.prop('checked');
    if (entry.type === 'str') return $input.val();
    if (entry.type === 'enum') return $input.val();
    var n = parseFloat($input.val());
    return isNaN(n) ? null : n;
  }

  function renderRow(key, entry) {
    var scope = entry.scope;
    var source = snap.sources[key];
    var $row = $('<div class="setting-row">').attr('data-key', key);
    var $info = $('<div class="setting-info">');
    var $label = $('<span class="setting-label">').text(entry.label);
    var $badge = $('<span class="setting-source">');
    if (Object.prototype.hasOwnProperty.call(snap.pending, key)) {
      $badge.addClass('source-pending').text('pending restart');
    } else if (source !== 'default') {
      $badge.addClass('source-' + source).text(source);
    } else {
      $badge = null;
    }
    $info.append($label);
    if ($badge) $info.append($badge);
    $info.append($('<p class="setting-help">').text(entry.help));

    var $control = $('<div class="setting-control">');
    var $input = controlFor(key, entry);
    $control.append($input);
    var unit = unitText(entry);
    if (unit) $control.append($('<span class="setting-unit">').text(unit));
    if (source !== 'default' && !isLocked(entry)) {
      $control.append(
        $('<button type="button" class="setting-reset" title="Reset to default">')
          .text('↺ default')
          .on('click', function () {
            resets[scope][key] = true;
            delete dirty[scope][key];
            $row.addClass('setting-dirty');
            $input.val(entry.type === 'bool' ? undefined : snap.defaults[key]);
            if (entry.type === 'bool') $input.prop('checked', snap.defaults[key]);
            $input.prop('disabled', true);
            updateSaveButtons();
          }));
    }

    $input.on('change input', function () {
      var next = readInput($input, entry);
      var current = displayValue(key);
      if (next === current || (next === '' && current === null)) {
        delete dirty[scope][key];
        $row.removeClass('setting-dirty');
      } else {
        dirty[scope][key] = next;
        $row.addClass('setting-dirty');
      }
      updateSaveButtons();
    });

    $row.append($info, $control);
    return $row;
  }

  function renderScope(scope, $container) {
    $container.empty();
    snap.category_order.forEach(function (category) {
      var keys = Object.keys(snap.schema).filter(function (k) {
        var e = snap.schema[k];
        if (e.scope !== scope || e.category !== category) return false;
        if (e.sim_only && !snap.simulate) return false;
        return true;
      }).sort();
      if (!keys.length) return;
      var $section = $('<div class="settings-category">')
        .append($('<h3>').text(category));
      keys.forEach(function (k) { $section.append(renderRow(k, snap.schema[k])); });
      $container.append($section);
    });
  }

  function renderKilnBar() {
    var idle = snap.oven_state === 'IDLE';
    var $select = $('#kiln-select').empty();
    $select.append($('<option>').val('').text('— defaults (no kiln) —'));
    snap.kilns.forEach(function (name) {
      $select.append($('<option>').val(name).text(name));
    });
    $select.val(snap.active_kiln || '');
    $('#active-kiln-label').text(
      snap.active_kiln ? 'active: ' + snap.active_kiln : 'no kiln active — using defaults');
    $('.kiln-bar button, #kiln-select').prop('disabled', !idle);
    $('#kiln-bar-hint').toggle(!idle || !snap.active_kiln);
    if (!idle) {
      $('#kiln-bar-hint').text('Kiln switching is locked while the oven is ' + snap.oven_state + '.');
    } else if (!snap.active_kiln) {
      $('#kiln-bar-hint').text('Create and activate a kiln settings profile to edit kiln settings.');
    }
    // Kiln-scope inputs are read-only without an active kiln (nothing to
    // save to). Only ever ADD disabling here — per-input safety locks were
    // already applied at render time and must not be un-done.
    if (!snap.active_kiln) {
      $('#kiln-settings').find('input, select, button').prop('disabled', true);
    }
  }

  function updateSaveButtons() {
    $('#btn_save_kiln').prop('disabled',
      !Object.keys(dirty.kiln).length && !Object.keys(resets.kiln).length);
    $('#btn_save_global').prop('disabled',
      !Object.keys(dirty.global).length && !Object.keys(resets.global).length);
  }

  function renderAll() {
    dirty = { global: {}, kiln: {} };
    resets = { global: {}, kiln: {} };
    $('#oven-state-chip').text(snap.oven_state);
    renderScope('kiln', $('#kiln-settings'));
    renderScope('global', $('#global-settings'));
    renderKilnBar();
    $('#restart-banner').toggle(snap.restart_pending);
    updateSaveButtons();
  }

  function refresh() {
    return $.getJSON(API).done(function (data) {
      snap = data;
      renderAll();
    }).fail(function () {
      $.bootstrapGrowl('Could not load settings', { type: 'danger' });
    });
  }

  function post(url, payload) {
    return $.ajax({
      url: url, type: 'POST', contentType: 'application/json',
      data: JSON.stringify(payload)
    });
  }

  // --- kiln bar actions -------------------------------------------------
  function bindKilnBar() {
    $('#btn_kiln_new').on('click', function () {
      var name = window.prompt('Name for the new kiln settings profile:');
      if (!name) return;
      post(API + '/kilns', { name: name }).done(refresh).fail(showErrors);
    });
    $('#btn_kiln_dup').on('click', function () {
      var source = $('#kiln-select').val();
      if (!source) { $.bootstrapGrowl('Select a kiln to duplicate', { type: 'warning' }); return; }
      var name = window.prompt('Name for the copy of "' + source + '":');
      if (!name) return;
      post(API + '/kilns', { name: name, duplicate_from: source }).done(refresh).fail(showErrors);
    });
    $('#btn_kiln_rename').on('click', function () {
      var current = $('#kiln-select').val();
      if (!current) { $.bootstrapGrowl('Select a kiln to rename', { type: 'warning' }); return; }
      var name = window.prompt('New name for "' + current + '":', current);
      if (!name || name === current) return;
      $.ajax({
        url: API + '/kilns/' + encodeURIComponent(current), type: 'PUT',
        contentType: 'application/json', data: JSON.stringify({ name: name })
      }).done(refresh).fail(showErrors);
    });
    $('#btn_kiln_delete').on('click', function () {
      var name = $('#kiln-select').val();
      if (!name) { $.bootstrapGrowl('Select a kiln to delete', { type: 'warning' }); return; }
      if (!window.confirm('Delete kiln settings profile "' + name + '"? This cannot be undone.')) return;
      $.ajax({ url: API + '/kilns/' + encodeURIComponent(name), type: 'DELETE' })
        .done(refresh).fail(showErrors);
    });
    $('#btn_kiln_activate').on('click', function () {
      var name = $('#kiln-select').val() || null;
      post(API + '/active_kiln', { name: name }).done(function (resp) {
        if (resp.restart_required) {
          $.bootstrapGrowl('Kiln activated — a restart is required for some of its settings', { type: 'warning' });
        } else {
          $.bootstrapGrowl('Kiln activated', { type: 'success' });
        }
        refresh();
      }).fail(showErrors);
    });
  }

  function showErrors(xhr) {
    var errors = (xhr.responseJSON || {}).errors || { error: 'request failed' };
    Object.keys(errors).forEach(function (k) {
      $.bootstrapGrowl(k + ': ' + errors[k], { type: 'danger' });
    });
  }

  $(function () {
    bindKilnBar();
    refresh();
    // Keep oven-state gating fresh, but never clobber in-progress edits.
    setInterval(function () { if (!hasDirty()) refresh(); }, 10000);
  });

  // Save flow attached in the next task; expose internals for it.
  window._settingsPanel = {
    getSnap: function () { return snap; },
    getDirty: function () { return dirty; },
    getResets: function () { return resets; },
    refresh: refresh, post: post, showErrors: showErrors
  };
})();
```

- [ ] **Step 2: Browser verification**

Start the server. With Playwright: open `http://localhost:8081/picoreflow/settings.html` and verify: (a) Kiln panel shows PID Tuning / Kiln / Firing Behavior / Safety categories; (b) Global panel shows the rest including Simulation (sim mode is on for local dev); (c) rendered `.setting-row` count equals the number of schema keys (all `sim_only` included locally) — run in the console:
`document.querySelectorAll('.setting-row').length` and compare against `Object.keys((await (await fetch('/api/settings')).json()).schema).length`. They must be equal.
(d) Create a kiln via "+ New", activate it, see the active label update and kiln inputs enable. (e) No console errors.

- [ ] **Step 3: Commit**

```bash
git add public/assets/js/settings.js
git commit -m "feat(ui): schema-driven settings rendering and kiln bar"
```

---

### Task 11: settings.js — save flow, confirmations, restart

**Files:**
- Modify: `public/assets/js/settings.js`

**Interfaces:**
- Consumes: `POST /api/settings`, `POST /api/settings/restart` (Task 7); internals exposed by Task 10.

- [ ] **Step 1: Add the save + restart flow** (replace the trailing `window._settingsPanel` block with the code below, keeping everything above it)

```javascript
  // --- save flow --------------------------------------------------------
  function saveScope(scope) {
    var values = dirty[scope];
    var reset = Object.keys(resets[scope]);
    var payload = { scope: scope, values: values, reset: reset };

    // Mid-firing ignore_* changes need a typed confirmation (ledger D114).
    if (snap.oven_state !== 'IDLE') {
      var needsConfirm = Object.keys(values).some(function (k) {
        return snap.schema[k].mid_firing_editable;
      }) || reset.some(function (k) {
        return snap.schema[k].mid_firing_editable;
      });
      if (needsConfirm) {
        var typed = window.prompt(
          'You are changing thermocouple error handling WHILE THE KILN IS FIRING.\n' +
          'Type IGNORE to confirm:');
        if (typed !== 'IGNORE') {
          $.bootstrapGrowl('Save cancelled', { type: 'warning' });
          return;
        }
        payload.confirm = true;
      }
    }

    post(API, payload).done(function (resp) {
      var outcomes = resp.outcomes || {};
      var counts = { 'applied': 0, 'applied-next-firing': 0, 'restart-required': 0 };
      Object.keys(outcomes).forEach(function (k) { counts[outcomes[k]]++; });
      if (counts['applied']) {
        $.bootstrapGrowl(counts['applied'] + ' setting(s) applied', { type: 'success' });
      }
      if (counts['applied-next-firing']) {
        $.bootstrapGrowl(counts['applied-next-firing'] + ' setting(s) saved — take effect at the next firing', { type: 'info' });
      }
      if (counts['restart-required']) {
        $.bootstrapGrowl(counts['restart-required'] + ' setting(s) saved — restart required', { type: 'warning' });
      }
      refresh(); // re-renders; banner driven by server-side restart_pending (D115)
    }).fail(function (xhr) {
      // Per-key errors: mark rows, keep edits so the user can fix them.
      var errors = (xhr.responseJSON || {}).errors || {};
      $('.setting-error').remove();
      Object.keys(errors).forEach(function (k) {
        var $row = $('.setting-row[data-key="' + k + '"]');
        if ($row.length) {
          $row.append($('<p class="setting-error">').text(errors[k]));
        } else {
          $.bootstrapGrowl(k + ': ' + errors[k], { type: 'danger' });
        }
      });
    });
  }

  // --- restart ------------------------------------------------------------
  function pollUntilBack(attempt) {
    if (attempt > 30) {
      $('#restart-status').text('Server did not come back — check the Pi.');
      return;
    }
    $.getJSON(API).done(function () {
      window.location.reload();
    }).fail(function () {
      setTimeout(function () { pollUntilBack(attempt + 1); }, 2000);
    });
  }

  function bindSaveAndRestart() {
    $('#btn_save_kiln').on('click', function () { saveScope('kiln'); });
    $('#btn_save_global').on('click', function () { saveScope('global'); });
    $('#btn_restart').on('click', function () {
      if (!window.confirm('Restart the kiln controller now? (Only possible while idle.)')) return;
      post(API + '/restart', {}).done(function () {
        $('#btn_restart').prop('disabled', true);
        $('#restart-status').text('Restarting…');
        setTimeout(function () { pollUntilBack(0); }, 2000);
      }).fail(function (xhr) {
        var err = ((xhr.responseJSON || {}).error) || 'restart refused';
        $.bootstrapGrowl(err, { type: 'danger' });
      });
    });
  }
```

And in the `$(function () {...})` boot block, add `bindSaveAndRestart();` after `bindKilnBar();`. Remove the `window._settingsPanel` export (it was scaffolding for this task).

- [ ] **Step 2: End-to-end browser verification** (server running locally, sim mode)

1. Change **Electricity rate** → Save Global → growl "1 setting(s) applied"; badge shows `global`; `storage/settings/global.json` contains the key.
2. Change **Broker port** → Save Global → growl "restart required"; banner appears; reload the page → banner still there (server-driven `restart_pending`).
3. Click **Restart Controller** → local process exits; `pollUntilBack` shows "Restarting…" (locally there is no systemd, so restart the server by hand and confirm the page reloads and the banner is gone).
4. Create + activate a kiln, set **Kp**, Save Kiln → growl "take effect at the next firing"; badge `kiln`.
5. Set **Thermocouple offset** on the kiln → Save → restart-required outcome + banner.
6. Enter an out-of-range value (e.g. Broker port 0) → Save → inline row error, nothing else applied (check another edited field was also rejected — all-or-nothing).
7. Reset-to-default (`↺ default`) on the electricity rate → Save → badge disappears, value back to default.
8. Simulate a firing (`POST /api` cmd=run with a test firing profile, or set state via a running sim) → reload settings page → safety inputs disabled with tooltip; kiln bar disabled with hint; toggling an **Ignore:** flag prompts for typed IGNORE; typing it applies, cancelling aborts.

- [ ] **Step 3: Run the suite one more time**

Run: `python -m pytest Test/ && python -c "import lib.oven"`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add public/assets/js/settings.js
git commit -m "feat(ui): settings save flow with apply outcomes, confirmations, and restart"
```

**PHASE 3 GATE — STOP.** Report browser verification results. Wait for explicit user approval before Phase 4.

---

# Phase 4 — Design-fidelity gate + ledger closeout

### Task 12: Browser gate (fresh agent) at 320 / 375 / 1024

**Files:**
- Create: `docs/superpowers/decisions/screenshots/settings-{320,375,1024}.png` (+ interaction shots as needed)
- Modify: `docs/superpowers/decisions/settings-panel-ledger.md` (Visual gate column)

Per `~/.claude/rules/web/decision-ledger.md`: a **fresh agent** (not the implementer) drives a real browser via Playwright against the running server and verifies each of D111–D115 as decided-behavior sentences, plus the decision-independent responsive smoke check at 320 and 375:

- [ ] **Step 1: Dispatch a fresh verification agent** with this brief: server URL `http://localhost:8081/picoreflow/settings.html` (start it first: `source venv/bin/activate && python kiln-controller.py`), the five ledger rows verbatim, and these mandatory checks:
  - **D111:** From `index.html`, the gear is visible in the Data Insights title at 320/375/1024, does not overlap any other interactive control (pairwise bounding-box check), and navigates to the settings page.
  - **D112:** At 1024, `document.querySelectorAll('.setting-row').length === Object.keys(schema).length` from `GET /api/settings`.
  - **D113:** Kiln bar renders with all five actions at 320/375/1024 with no overflow; create→activate→rename→delete round-trip works.
  - **D114:** With a simulated firing running (`POST /api {"cmd":"run", "profile": "<any test profile>"}` — or use `test-fast` from Test fixtures loaded into storage/profiles), safety inputs are disabled, ignore_* flags are not, and the IGNORE prompt gates the save. Stop the firing afterwards (`{"cmd":"stop"}`).
  - **D115:** Saving an MQTT port change raises the banner; the banner survives a reload; at 320 the banner does not overflow horizontally.
  - **Smoke check (320 and 375):** no horizontal overflow (`document.documentElement.scrollWidth <= clientWidth`), no clipped text in labels, no pairwise overlap of visible interactive controls on either page.
  - Screenshot each breakpoint to the paths above.

- [ ] **Step 2: Record PASS/FAIL per (row × breakpoint) in the ledger.** On any FAIL: fix, re-run the gate for the failed rows, only then mark PASS. Never mark PASS without the committed screenshot.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/decisions/screenshots/ docs/superpowers/decisions/settings-panel-ledger.md
git commit -m "docs(ledger): visual gate results for D111-D115 with committed screenshots"
```

---

### Task 13: Ledger closeout + final verification

**Files:**
- Modify: `docs/superpowers/decisions/settings-panel-ledger.md` (D101–D110 Plan/Assertion columns)

- [ ] **Step 1: Fill the remaining ledger columns** with these values:

| Row | Plan task(s) | Assertion |
|---|---|---|
| D101 | Tasks 2, 6 | `test_global_overlay_applies`, `test_kiln_overlay_applies_over_defaults` (setattr onto config; `lib/oven.py` untouched — verify with `git diff main -- lib/oven.py` being empty) |
| D102 | Tasks 1, 10 | `test_every_schema_key_exists_in_config`, `test_entry_shapes`; browser gate D112 (row count == schema count) |
| D103 | Tasks 4, 9–11 | grep gate: `grep -n '"profile"' public/assets/js/settings.js` returns nothing; UI copy says "kiln settings profile" |
| D104 | Task 1 | `test_hardware_settings_are_not_exposed` |
| D105 | Task 1 | `test_entry_shapes` (scope field) + spec §3 tables mirrored in schema |
| D106 | Tasks 2, 3 | `test_kiln_overlay_applies_over_defaults` (untouched key keeps default), `test_reset_removes_override_and_restores_default` |
| D107 | Tasks 3, 5 | `test_restart_key_persisted_but_not_applied`, `test_pending_reports_unapplied_restart_keys` |
| D108 | Tasks 3, 4 | `test_safety_key_blocked_while_firing`, `test_ignore_flag_editable_while_firing_with_confirm`, `test_ignore_flag_blocked_while_firing_without_confirm`, `test_activate_blocked_while_firing` |
| D109 | Task 3 | `test_all_or_nothing_on_any_invalid_value` |
| D110 | Tasks 7, 8 | restart handler comment + `deploy/kiln-controller.service` with `Restart=always`; curl 409 check while firing |

- [ ] **Step 2: Final verification sweep**

```bash
python -m pytest Test/ -v
python -c "import lib.oven"
git diff main -- lib/oven.py   # MUST be empty (D101)
python kiln-controller.py & sleep 3; curl -s http://localhost:8081/api/settings | python -m json.tool > /dev/null && echo API_OK; kill %1
```
Expected: all tests pass, empty oven diff, `API_OK`.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/decisions/settings-panel-ledger.md
git commit -m "docs(ledger): fill plan/assertion columns for settings panel (D101-D110)"
```

**PHASE 4 GATE — STOP.** Feature complete. Offer merge/PR options per superpowers:finishing-a-development-branch (PRs go to `omegalens/kiln-controller`, never upstream).

---

# Deployment notes (post-merge, Pi)

Not an implementation task — operator checklist:

1. Confirm the kiln is IDLE (check MQTT `kiln/state` first, per deploy memory).
2. `scp` changed files to `user@10.36.1.129:/home/user/kiln-controller/` (never git pull on the Pi): `lib/settings.py`, `lib/settings_schema.py`, `kiln-controller.py`, `public/settings.html`, `public/index.html`, `public/assets/js/settings.js`, `public/assets/css/settings.css`, `public/assets/css/components.css`.
3. Install the unit: `scp deploy/kiln-controller.service user@10.36.1.129:/tmp/ && ssh user@10.36.1.129 'sudo cp /tmp/kiln-controller.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart kiln-controller'`.
4. Verify: `curl http://10.36.1.129/api/settings | python -m json.tool | head`, then open the settings page and create the first kiln settings profile from the current tuning.
5. The Pi's `config.py` is the live tuned baseline — its values become the defaults; overlays start empty, so behavior is unchanged until settings are edited in the GUI.
```
