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
| D111 | Settings are reached via a gear icon in the Data Insights widget title bar linking to a standalone settings.html page, explicitly NOT a modal inside index.html. | N/A (textual decision) | 320, 375, 1024 | §8 | Task 9 | — (browser gate) | PENDING |
| D112 | The settings form is rendered entirely from the server schema — every schema key appears on the page and the JS hardcodes no setting names, explicitly NOT hand-built per-setting markup. | N/A (textual decision) | 1024 | §2, §8 | Tasks 10 | — (browser gate: rendered-row count equals schema-key count) | PENDING |
| D113 | A kiln bar at the top provides select + Activate/New/Duplicate/Rename/Delete, disabled with an explanatory hint while the oven is not IDLE, explicitly NOT kiln management buried in a submenu. | N/A (textual decision) | 320, 375, 1024 | §8 | Tasks 9, 10 | — (browser gate) | PENDING |
| D114 | Safety-flagged inputs are disabled while firing, EXCEPT ignore_* flags which stay editable behind a typed IGNORE confirmation, explicitly NOT a blanket lockout of the whole panel. | N/A (textual decision) | 1024 | §6, §8 | Task 11 | — (browser gate) | PENDING |
| D115 | Saves returning restart-required outcomes raise a persistent banner with a Restart button (driven by server-side restart_pending, so it survives reloads), explicitly NOT a transient toast. | N/A (textual decision) | 320, 1024 | §8 | Task 11 | — (browser gate) | PENDING |
| D116 | Each settings category renders as its own clearly distinct container card (own surface, border, radius, internal padding), explicitly NOT a flat list with h3 dividers inside one widget; inputs and row text carry comfortable token-based padding. | N/A (user-directed refinement) | 320, 1024 | §8 | UI-fix (post-T11) | — (browser gate) | PENDING |
