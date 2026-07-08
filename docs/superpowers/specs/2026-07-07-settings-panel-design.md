# GUI Settings Panel with Kiln Settings Profiles — Design

**Date:** 2026-07-07
**Status:** Approved design, pre-implementation
**Decision ledger:** `docs/superpowers/decisions/settings-panel-ledger.md`

## Problem

All runtime configuration lives in `config.py`, editable only over SSH. The
controller (one Pi) is physically moved between different kilns, each needing
its own tuning (PID gains, element wattage, emergency shutoff temperature,
thermocouple offset, throttling). We want:

1. A settings panel in the web UI to view and change settings without SSH.
2. Named per-kiln settings profiles, switchable from the UI.

`config.py` is executable Python (hardware imports, `board.D17` pin objects,
enum values, computed paths), so it cannot be round-tripped by a GUI, and a
web endpoint that writes imported Python would be remote code execution by
design. The server has no authentication (LAN-trust model per the earlier
red-team audit), so the editable surface must be data-only and strictly
validated.

## Naming

This app already uses "profile" to mean *firing profile*. To avoid collision,
the per-kiln settings bundles are called **kilns** in the UI (a "Kiln"
selector) and **kiln settings profiles** in code/docs. Never bare "profile".

## Architecture

### 1. Config layering

`config.py` keeps its current role: code-level defaults plus hardware
bootstrap. A new module `lib/settings.py`:

1. Loads JSON overlays at startup (global overlay, then the active kiln
   overlay on top).
2. Validates every value against the schema (type, range, enum membership).
3. Applies valid values via `setattr(config, key, value)` onto the imported
   `config` module.

All existing `config.x` call sites (120+ in `lib/oven.py` alone) keep working
with **zero changes** to `lib/oven.py`. Invalid or corrupt overlay files are
rejected per-key (or per-file if unparseable) with a logged warning; defaults
win. A bad settings file must never prevent the controller from starting.

Overlays are **sparse**: they store only overridden keys. Untouched settings
keep tracking `config.py` defaults, so pulling new code with changed defaults
behaves sensibly.

### 2. Settings schema — single source of truth

`lib/settings_schema.py` declares every GUI-exposed setting:

```python
SETTINGS_SCHEMA = {
    "pid_kp": {
        "type": "float", "min": 0.0, "max": 1000.0,
        "scope": "kiln",            # "kiln" | "global"
        "apply": "next-firing",     # "live" | "next-firing" | "restart"
        "category": "PID Tuning",
        "label": "Proportional gain (Kp)",
        "help": "…",
        "safety": False,            # True → confirmation + IDLE-only guard
    },
    ...
}
```

The schema drives **both** server-side validation and frontend form
rendering. The UI hardcodes no setting names; adding a setting later is a
schema entry plus (if new) a `config.py` default.

Types: `float`, `int`, `bool`, `str`, `enum` (with `choices`). Numeric
entries must declare `min`/`max`. Units are display metadata (`"unit": "°F"`,
`"°/hr"`, `"s"`, …); temperature-typed settings display in `temp_scale`
units but are stored in the scale `config.py` documents (file values are in
`temp_scale` — unchanged semantics).

### 3. Scope split

**Kiln scope** (stored per kiln settings profile):

| Setting | Apply | Notes |
|---|---|---|
| `pid_kp`, `pid_ki`, `pid_kd` | next-firing | `PID()` is rebuilt at each run start |
| `kw_elements` | live | cost calc |
| `emergency_shutoff_temp` | live | safety; read every control loop |
| `thermocouple_offset` | restart | cached onto zone objects at construction (`lib/oven.py:63,76`) |
| `pid_control_window` | live | |
| `kiln_must_catch_up` | live | |
| `throttle_below_temp`, `throttle_percent` | live | |
| `segment_complete_tolerance` | live | |
| `rate_deviation_warning` | live | |
| `estimated_max_heating_rate`, `estimated_natural_cooling_rate` | live | ETA estimation |
| `rate_lookahead_seconds`, `max_target_divergence` | live | |
| `use_rate_based_control` | next-firing | |

**Global scope** (stored in the global overlay):

| Setting | Apply | Notes |
|---|---|---|
| `kwh_rate`, `currency_type` | live | |
| `temp_scale`, `time_scale_slope`, `time_scale_profile` | restart | UI banner also tells user to reload the page |
| `graph_cutoff_temp` | live | |
| `cooling_ambient_temp`, `cooling_target_temp`, `cooling_min_samples` | live | |
| `seek_start` | live | |
| `stall_detect_time`, `stall_min_temp_rise` | live | safety |
| `runaway_detect_time`, `runaway_min_temp_rise` | live | safety |
| `state_save_interval` | live | |
| `automatic_restarts`, `automatic_restart_window` | live | |
| `mqtt_enabled`, `mqtt_host`, `mqtt_port`, `mqtt_topic_prefix`, `mqtt_publish_interval`, `mqtt_username`, `mqtt_password` | restart | client wired at startup |
| `sensor_time_wait`, `temperature_average_samples`, `ac_freq_50hz` | restart | cached at thread construction |
| `allow_legacy_profiles` | live | |
| `ignore_*` thermocouple-error flags (all 12) | live | safety, special mid-firing rule below |

**Simulation category** (global scope, rendered only when `config.simulate`
is true; apply: restart): `sim_initial_temp`, `sim_t_env`, `sim_c_heat`,
`sim_c_oven`, `sim_p_heat`, `sim_R_o_nocool`, `sim_R_o_cool`,
`sim_R_ho_noair`, `sim_R_ho_air`, `sim_speedup_factor`.

**Excluded from the GUI** (SSH-only, unchanged in `config.py`): GPIO/SPI
pins, `thermocouple_type`, `listening_port`, `log_level`/`log_format`, file
paths, `simulate`, deprecated `stop_integral_windup`, and the multi-zone
`zones` list / `zone_control_strategy` (structured per-zone config is future
work; noted, not designed here).

### 4. Storage

```
storage/settings/
├── global.json          # global-scope overrides (sparse)
├── active_kiln.json     # {"active": "<name>"} or {"active": null}
└── kilns/
    ├── skutt-1027.json  # kiln-scope overrides (sparse)
    └── test-kiln.json
```

- Kiln names: 1–40 chars, `[A-Za-z0-9 _-]`, unique case-insensitively;
  filename is a slug of the name. Name validation is server-side.
- `active: null` (or missing/dangling pointer) → no kiln overlay applied;
  kiln-scope settings fall back to global overlay? **No** — kiln-scope keys
  live only in kiln files; with no active kiln they fall back to `config.py`
  defaults. This is the "fresh install" state and must work.
- All writes are atomic (write temp file in same directory, `os.replace`)
  to survive power loss — this is an SD card on a kiln controller.
- A `gevent` lock serializes settings writes; reads are lock-free (dict
  replacement is atomic).

### 5. API

REST endpoints following the existing `/api` conventions in
`kiln-controller.py`:

| Endpoint | Method | Behavior |
|---|---|---|
| `/api/settings` | GET | Schema + effective values + per-key source (`default` / `global` / `kiln`) + active kiln + kiln list + oven state |
| `/api/settings` | POST | `{scope, values: {key: value}}` — validate all, apply all-or-nothing per request, persist, `setattr` live/next-firing keys, return per-key apply outcome (`applied` / `restart-required`) |
| `/api/settings/kilns` | POST | Create kiln (empty overlay, or `duplicate_from`) |
| `/api/settings/kilns/<name>` | PUT | Rename |
| `/api/settings/kilns/<name>` | DELETE | Delete (refused if active) |
| `/api/settings/active_kiln` | POST | `{"name": string\|null}` — switch active kiln (null = back to defaults, so the last kiln remains deletable); re-applies overlay stack; IDLE-only. Response includes `restart_required: true` when a restart-apply kiln key (e.g. `thermocouple_offset`) differs between old and new effective values |
| `/api/settings/restart` | POST | Clean shutdown for systemd restart; IDLE-only |

Validation failures return HTTP 400 with per-key error messages; nothing is
persisted or applied from a request containing any invalid value.

### 6. Guards and safety rules

- **Kiln switching**: refused unless oven state is IDLE.
- **Safety-critical settings** (`emergency_shutoff_temp`, stall/runaway
  detection, `pid_control_window`): refused unless IDLE; the UI disables
  them with an explanatory tooltip while firing.
- **`ignore_*` error flags are the exception**: their documented purpose is
  mid-firing triage ("ignore this TC error to complete a firing"). They are
  editable in any oven state, but only via a typed confirmation in the UI
  ("type IGNORE to confirm"), and every change is logged at WARNING with
  old/new value and oven state.
- **Non-safety settings** (cost, display, cooling estimation, etc.) are
  editable in any state.
- **Restart endpoint**: IDLE-only. Restart works by exiting cleanly
  (`sys.exit(0)`) and relying on systemd.
- Every settings change is logged (key, old → new, scope, oven state).

### 7. Deployment prerequisite (Pi)

The existing unit file has **no `Restart=` policy** — today a crash never
restarts the controller (only reboot does). Add to
`/etc/systemd/system/kiln-controller.service`:

```ini
[Service]
Restart=always
RestartSec=5
```

This enables the GUI restart button **and** fixes crash recovery generally.
A copy of the corrected unit file is committed at
`deploy/kiln-controller.service` as the reference. Deploy per the usual
scp method + `sudo systemctl daemon-reload`.

### 8. Frontend

A new **Settings** view in the existing SPA (`public/index.html` +
`picoreflow.js`), reachable from the main navigation, fetched from
`GET /api/settings` and rendered entirely from the schema:

- **Kiln selector** at top: dropdown of kiln settings profiles + active
  indicator; create / duplicate / rename / delete / activate actions.
  Disabled (with reason) while firing.
- **Categorized sections** (PID Tuning, Safety, Cost, Display, MQTT,
  Advanced, Simulation) with typed inputs: number inputs with min/max and
  unit suffix, toggles for booleans, selects for enums.
- **Source badge** per setting: default / global / kiln, plus a
  "reset to default" affordance (removes the override key).
- **Dirty tracking** with explicit Save per scope section; per-key apply
  outcome shown after save; persistent "Restart required" banner with a
  Restart button when any restart-key is pending.
- Safety section visually distinct; `ignore_*` flags behind the typed
  confirmation.
- Responsive at 320/375 per the house Design-Fidelity Gate; visual decision
  rows will be added to the ledger with mockups during implementation
  planning (the panel's visual form is not designed in this spec).

### 9. Testing

- **`Test/test_settings.py`**: overlay precedence (default < global < kiln),
  sparse-overlay semantics, validation (type/range/enum), corrupt-file and
  dangling-pointer fallback, atomic write behavior, kiln CRUD, name
  validation.
- **API tests**: each endpoint, including guard behavior by oven state
  (IDLE vs RUNNING), all-or-nothing rejection, per-key apply outcomes.
- **Schema-integrity test**: every schema key exists in `config.py` with a
  matching Python type; every numeric entry has min/max; every entry has
  scope/apply/category. This prevents schema–config drift.
- Existing suite (`python -m pytest Test/`) must stay green; smoke:
  `python -c "import lib.settings"` and server startup with (a) no settings
  dir, (b) valid overlays, (c) corrupt overlay.

## Rejected alternatives

- **Raw file editor in the browser** (Mainsail-style): least work, but no
  validation and no per-setting apply semantics. A typo'd
  `emergency_shutoff_temp` on a kiln is a fire-class bug. Rejected.
- **Full `Settings`-class refactor** replacing `import config` at all 120+
  call sites: architecturally purest, but the highest-risk change possible
  in the most safety-critical file for zero user-visible gain. The overlay
  approach can evolve into it later. Rejected for now.
- **Editing `config.py` via the web**: writing imported Python from an
  unauthenticated endpoint is RCE by design. Rejected outright.

## Out of scope

- Authentication (unchanged LAN-trust model; noted as pre-existing risk).
- Multi-zone `zones` configuration in the GUI.
- Hardware pin / thermocouple type configuration (SSH-only).
- Editing `config.py` defaults from the GUI.
