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
