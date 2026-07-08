# Decision Ledger — GUI Settings Panel & Kiln Settings Profiles

Spec: `docs/superpowers/specs/2026-07-07-settings-panel-design.md`

IDs are append-only and immutable. To reverse a decision, add a new row
naming the one it supersedes. Visual rows (mockup-backed, with breakpoints)
will be appended during implementation planning when the panel's visual form
is designed; the rows below are the architectural decisions from
brainstorming.

| ID | Decision | Mockup ref | Breakpoints | Spec § | Plan task(s) | Assertion | Visual gate |
|----|----------|------------|-------------|--------|--------------|-----------|-------------|
| D101 | Settings are edited as a JSON overlay applied via `setattr` onto the `config` module, explicitly NOT by editing/rewriting `config.py` and NOT by refactoring call sites to a `Settings` class. | N/A | — | §1 | | | N/A |
| D102 | A single Python schema (`lib/settings_schema.py`) drives both server-side validation and frontend form rendering; the frontend hardcodes NO setting names, explicitly NOT hand-built per-setting form markup. | N/A | — | §2 | | | N/A |
| D103 | Per-kiln bundles are called "kilns" / "kiln settings profiles", explicitly NOT "profiles" (reserved for firing profiles). | N/A | — | Naming | | | N/A |
| D104 | Hardware settings (GPIO/SPI pins, thermocouple type) stay SSH-only in `config.py`, explicitly NOT exposed in the GUI or kiln overlays (wiring is fixed; only the kiln changes). | N/A | — | §3 | | | N/A |
| D105 | Scope split: PID, wattage, emergency temp, TC offset, throttle, rate-control tuning are kiln-scope; cost, units, MQTT, graph, cooling, stall/runaway detection are global-scope — explicitly NOT one flat namespace. | N/A | — | §3 | | | N/A |
| D106 | Overlays are sparse (only overridden keys stored); unset keys track `config.py` defaults — explicitly NOT full snapshots of all values. | N/A | — | §1, §4 | | | N/A |
| D107 | Each setting has a declared apply mode (live / next-firing / restart) surfaced in the UI, explicitly NOT a blanket "restart to apply" and NOT silent partial application. | N/A | — | §2, §5 | | | N/A |
| D108 | Safety-critical settings and kiln switching are IDLE-only; `ignore_*` TC-error flags are the sole exception, editable mid-firing behind a typed confirmation — explicitly NOT blanket editability and NOT a blanket firing lockout of the whole panel. | N/A | — | §6 | | | N/A |
| D109 | Settings writes are all-or-nothing per request with per-key validation errors, explicitly NOT best-effort partial persistence. | N/A | — | §5 | | | N/A |
| D110 | GUI restart button relies on systemd `Restart=always` (unit file fix committed under `deploy/`), explicitly NOT self-exec/respawn logic in Python. | N/A | — | §7 | | | N/A |
