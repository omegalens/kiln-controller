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

    def _require_valid_name(self, name):
        """Reject names that don't match the creation charset BEFORE any
        path construction — CRUD inputs arrive raw from the HTTP layer."""
        if not isinstance(name, str) or not KILN_NAME_RE.match(name):
            raise SettingsValidationError({"name": "invalid kiln name"})

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
                self._require_valid_name(duplicate_from)
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
            self._require_valid_name(old)
            if not os.path.exists(self._kiln_path(old)):
                raise SettingsValidationError({"name": "kiln '%s' not found" % old})
            self._require_valid_name(new)
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
            self._require_valid_name(name)
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
            if name is not None:
                self._require_valid_name(name)
                if not os.path.exists(self._kiln_path(name)):
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
