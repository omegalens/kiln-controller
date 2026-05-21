# Kiln Controller Quick Wins Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship three independent, scoped improvements identified in the Tana backlog: (1) MQTT announcement when a firing finishes, (2) clear "HOLD" indicator on the firing display during hold phases, (3) schedule editor duplicate/reorder controls.

**Architecture:** Three orthogonal features. Feature 1 extends the existing change-only retained-topic plumbing in `lib/mqtt.py` with a new `firing_status` topic — no new MQTT mechanism. Feature 2 is a one-liner in the frontend status handler that branches on the existing `segment_phase` field already exposed in state. Feature 3 is pure frontend: row controls in the profile editor table, wired to in-memory `profile_segments` mutations that already exist.

**Tech Stack:** Python 3 (Bottle + gevent-websocket backend, pytest tests), vanilla JavaScript + jQuery + Flot (frontend), MQTT via paho-mqtt.

---

## File Structure

**Modified files:**
- `lib/mqtt.py` — add `firing_status` to change-only retained topics
- `public/assets/js/picoreflow.js` — render HOLD, profile editor row controls
- `docs/mqtt.md` — document the new topic
- `Test/test_mqtt.py` — extend with `firing_status` topic tests

**No new files.** Every change extends an existing module along its established pattern.

---

## Feature 1 — MQTT Firing-Complete Announcement

### Task 1: Test that `firing_status` is published as a change-only retained topic

**Files:**
- Test: `Test/test_mqtt.py` (add a new class at the bottom)

- [ ] **Step 1: Write the failing test**

Append to `Test/test_mqtt.py`:

```python
class TestFiringStatusTopic:
    """The firing_status topic lets MQTT subscribers distinguish completed /
    aborted / emergency_stop runs after the kiln transitions to IDLE.

    Like other change-only topics (state, profile, emergency), it publishes
    only when the value changes — so subscribers see a single retained value
    that sticks across reconnects until the next firing starts.
    """

    def test_firing_status_published_when_set(self, mqtt_client):
        mqtt_client._last_publish = 0
        state = {
            "temperature": 70,
            "state": "IDLE",
            "last_firing_status": "completed",
        }
        mqtt_client.publish_state(state)

        calls = [
            c for c in mqtt_client.client.publish.call_args_list
            if c[0][0] == "kiln/firing_status"
        ]
        assert len(calls) == 1
        assert calls[0][0][1] == "completed"
        # Retained so late subscribers see the outcome immediately
        assert calls[0][1].get("retain", calls[0][0][3] if len(calls[0][0]) > 3 else False) is True

    def test_firing_status_not_republished_when_unchanged(self, mqtt_client):
        state = {"temperature": 70, "state": "IDLE", "last_firing_status": "completed"}

        mqtt_client._last_publish = 0
        mqtt_client.publish_state(state)
        mqtt_client._last_publish = 0
        mqtt_client.publish_state(state)

        calls = [
            c for c in mqtt_client.client.publish.call_args_list
            if c[0][0] == "kiln/firing_status"
        ]
        assert len(calls) == 1

    def test_firing_status_republished_when_changed(self, mqtt_client):
        mqtt_client._last_publish = 0
        mqtt_client.publish_state({"state": "RUNNING", "last_firing_status": "in_progress"})
        mqtt_client._last_publish = 0
        mqtt_client.publish_state({"state": "IDLE", "last_firing_status": "completed"})

        calls = [
            c for c in mqtt_client.client.publish.call_args_list
            if c[0][0] == "kiln/firing_status"
        ]
        assert len(calls) == 2
        assert calls[0][0][1] == "in_progress"
        assert calls[1][0][1] == "completed"

    def test_firing_status_omitted_when_none(self, mqtt_client):
        """Before any firing has happened, last_firing_status is None.
        Don't publish an empty string in that case — leave the topic clean."""
        mqtt_client._last_publish = 0
        mqtt_client.publish_state({"state": "IDLE", "last_firing_status": None})

        calls = [
            c for c in mqtt_client.client.publish.call_args_list
            if c[0][0] == "kiln/firing_status"
        ]
        assert len(calls) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest Test/test_mqtt.py::TestFiringStatusTopic -v`
Expected: All four tests FAIL (no `kiln/firing_status` calls found).

- [ ] **Step 3: Implement — add `firing_status` to change-only retained topics**

In `lib/mqtt.py`, locate the `change_only` block inside `publish_state` (around line 131-136):

```python
        # Change-only retained topics
        change_only = {
            "state": state_dict.get("state"),
            "profile": state_dict.get("profile"),
            "emergency": state_dict.get("emergency", ""),
        }
```

Replace it with:

```python
        # Change-only retained topics
        change_only = {
            "state": state_dict.get("state"),
            "profile": state_dict.get("profile"),
            "emergency": state_dict.get("emergency", ""),
        }
        # firing_status: outcome of the most recent firing
        # (completed / aborted / emergency_stop / in_progress / runaway).
        # Only publish when a value is actually set — None means no firing
        # has happened yet and we shouldn't muddy the topic with empties.
        firing_status = state_dict.get("last_firing_status")
        if firing_status is not None:
            change_only["firing_status"] = firing_status
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest Test/test_mqtt.py::TestFiringStatusTopic -v`
Expected: All four tests PASS.

- [ ] **Step 5: Run the full MQTT test suite to confirm no regressions**

Run: `python -m pytest Test/test_mqtt.py -v`
Expected: All tests PASS (including the pre-existing `TestChangeOnlyTopics` and `TestRetainedTopics`).

- [ ] **Step 6: Update docs**

Edit `docs/mqtt.md`. In the "Individual Topics (Retained)" table (around line 95-106), add a row after `kiln/segment`:

```markdown
| `kiln/firing_status` | `"completed"`, `"aborted"`, `"emergency_stop"`, `"in_progress"` | Yes |
```

In the "Change-Only Publishing" list (around line 142-147), add a bullet:

```markdown
- `kiln/firing_status`
```

Add a new subsection right after the "Change-Only Publishing" section:

```markdown
### Firing-Complete Notifications

`kiln/firing_status` lets external systems detect when a firing ends and *how* it ended. The value is set when a firing starts (`in_progress`), then updated on transition to IDLE:

- `completed` — schedule ran to its final segment cleanly
- `aborted` — user stopped the firing via UI or MQTT `stop` command
- `emergency_stop` — safety shutdown (over-temp, sensor failure, stall, runaway)

The topic is retained, so subscribers see the most recent outcome immediately on connect and after reconnects. To get notified the moment a firing finishes, subscribe to `kiln/firing_status` and act on the transition from `in_progress` to any other value.

```yaml
# Home Assistant example
automation:
  - alias: "Notify when kiln firing completes"
    trigger:
      platform: mqtt
      topic: kiln/firing_status
    condition:
      template: "{{ trigger.payload in ['completed', 'aborted', 'emergency_stop'] }}"
    action:
      service: notify.mobile_app
      data:
        title: "Kiln firing {{ trigger.payload }}"
        message: "Check the controller for details"
```

- [ ] **Step 7: Commit**

```bash
git add lib/mqtt.py Test/test_mqtt.py docs/mqtt.md
git commit -m "feat(mqtt): publish firing_status topic for firing-complete notifications"
```

---

## Feature 2 — HOLD Display During Hold Phase

### Task 2: Frontend — show "HOLD" in the Set Rate card during hold phase

**Files:**
- Modify: `public/assets/js/picoreflow.js:1912-1929` (the status handler — branch on `segment_phase`)

The backend already broadcasts `segment_phase` (`'ramp'` or `'hold'`) in the live status payload — see `lib/oven.py:1475` and confirm in the JS receiver, which currently passes `x.target_heat_rate` straight through `formatRateDisplay`. That's misleading during hold: the segment's nominal rate (e.g. `100`) still shows, even though the kiln is parked at target, not climbing. Fix is one branch in the renderer — no backend change needed.

- [ ] **Step 1: Update the running-state status handler**

In `public/assets/js/picoreflow.js`, find the block that renders the running state (around line 1912-1929). It currently looks like:

```javascript
                var actualRate = clampRate(parseInt(x.heat_rate) || 0);
                $('#heat_rate_actual').html(actualRate);

                if (typeof x.current_segment !== 'undefined' && typeof x.progress !== 'undefined') {
                    $('#heat_rate_set').html(formatRateDisplay(x.target_heat_rate));
                    var elapsed = x.actual_elapsed_time || 0;
                    $('#elapsed_time').html(formatSecondsToHHMMSS(elapsed));
                    var eta = formatSecondsToHHMMSS(x.eta_seconds || 0);
                    $('#eta-time').text(eta);
                    updateProgress(x.progress);
                } else {
                    $('#heat_rate_set').html('---');
                    $('#elapsed_time').html(formatSecondsToHHMMSS(x.runtime || 0));
                    var left = Math.max(0, Math.floor(x.totaltime - x.runtime));
                    var eta = formatSecondsToHHMMSS(left);
                    $('#eta-time').text(eta);
                    updateProgress(parseFloat(x.runtime) / parseFloat(x.totaltime) * 100);
                }
```

Replace with:

```javascript
                var actualRate = clampRate(parseInt(x.heat_rate) || 0);
                $('#heat_rate_actual').html(actualRate);

                if (typeof x.current_segment !== 'undefined' && typeof x.progress !== 'undefined') {
                    // During hold phase, the segment's nominal rate is misleading —
                    // the kiln is parked at target, not climbing. Show "HOLD" instead.
                    if (x.segment_phase === 'hold') {
                        $('#heat_rate_set').html('HOLD');
                    } else {
                        $('#heat_rate_set').html(formatRateDisplay(x.target_heat_rate));
                    }
                    var elapsed = x.actual_elapsed_time || 0;
                    $('#elapsed_time').html(formatSecondsToHHMMSS(elapsed));
                    var eta = formatSecondsToHHMMSS(x.eta_seconds || 0);
                    $('#eta-time').text(eta);
                    updateProgress(x.progress);
                } else {
                    $('#heat_rate_set').html('---');
                    $('#elapsed_time').html(formatSecondsToHHMMSS(x.runtime || 0));
                    var left = Math.max(0, Math.floor(x.totaltime - x.runtime));
                    var eta = formatSecondsToHHMMSS(left);
                    $('#eta-time').text(eta);
                    updateProgress(parseFloat(x.runtime) / parseFloat(x.totaltime) * 100);
                }
```

- [ ] **Step 2: Smoke-test in the browser**

Run:

```bash
source venv/bin/activate
python kiln-controller.py
```

Open `http://localhost:8081`. With `simulate = True` and `sim_speedup_factor = 100` in `config.py`, load a profile that has a hold (e.g. `test-fast.json`) and start a firing.

Verify:
- During ramp: "Set Rate" shows a number (e.g. `100`)
- When the kiln reaches the segment target and enters hold: "Set Rate" flips to `HOLD`
- When the hold completes and the next ramp begins: "Set Rate" flips back to the new segment's rate
- When firing ends: "Set Rate" resets to `---`

Stop the server when done.

- [ ] **Step 3: Commit**

```bash
git add public/assets/js/picoreflow.js
git commit -m "feat(ui): show HOLD in Set Rate card during hold phase"
```

---

## Feature 3 — Schedule Editor: Duplicate + Reorder

### Task 3: Add per-row duplicate, move-up, move-down buttons to the profile editor

**Files:**
- Modify: `public/assets/js/picoreflow.js:583-693` (the `updateProfileTable_v2` renderer and `bindSegmentEvents` handler)

- [ ] **Step 1: Update the row markup to include the new buttons**

In `public/assets/js/picoreflow.js`, find the row-building loop inside `updateProfileTable_v2` (around line 626-633). It currently looks like:

```javascript
        html += '<tr>';
        html += '<td>' + (i + 1) + '</td>';
        html += '<td><input type="text" class="form-input form-input-sm seg-rate" data-idx="' + i + '" value="' + seg.rate + '" /></td>';
        html += '<td><input type="text" class="form-input form-input-sm seg-target" data-idx="' + i + '" value="' + seg.target + '" /></td>';
        html += '<td><input type="text" class="form-input form-input-sm seg-hold" data-idx="' + i + '" value="' + (seg.hold || 0) + '" /></td>';
        html += '<td class="text-muted">' + time_str + '</td>';
        html += '<td><button class="btn-delete-segment del-segment" data-idx="' + i + '">×</button></td>';
        html += '</tr>';
```

Replace the last action-cell line with a cell that bundles four buttons. The new row becomes:

```javascript
        html += '<tr>';
        html += '<td>' + (i + 1) + '</td>';
        html += '<td><input type="text" class="form-input form-input-sm seg-rate" data-idx="' + i + '" value="' + seg.rate + '" /></td>';
        html += '<td><input type="text" class="form-input form-input-sm seg-target" data-idx="' + i + '" value="' + seg.target + '" /></td>';
        html += '<td><input type="text" class="form-input form-input-sm seg-hold" data-idx="' + i + '" value="' + (seg.hold || 0) + '" /></td>';
        html += '<td class="text-muted">' + time_str + '</td>';
        html += '<td class="seg-actions">';
        html += '<button class="btn-seg-action move-up" data-idx="' + i + '" title="Move up"' + (i === 0 ? ' disabled' : '') + '>▲</button>';
        html += '<button class="btn-seg-action move-down" data-idx="' + i + '" title="Move down"' + (i === profile_segments.length - 1 ? ' disabled' : '') + '>▼</button>';
        html += '<button class="btn-seg-action dup-segment" data-idx="' + i + '" title="Duplicate">⎘</button>';
        html += '<button class="btn-delete-segment del-segment" data-idx="' + i + '" title="Delete">×</button>';
        html += '</td>';
        html += '</tr>';
```

- [ ] **Step 2: Wire up the new button handlers in `bindSegmentEvents`**

In `bindSegmentEvents` (around line 648-693), the existing handlers are `$('.seg-rate, .seg-target, .seg-hold').change(...)`, `$('.del-segment').click(...)`, and `$('#add_segment').click(...)`. Append three new handlers immediately before the `$('#add_segment').click(...)` block. The final shape of `bindSegmentEvents` becomes:

```javascript
function bindSegmentEvents() {
    $('#start_temp_input').change(function () {
        profile_start_temp = parseFloat($(this).val()) || 65;
        updateGraphFromSegments();
        updateProfileTable_v2();
    });

    $('.seg-rate, .seg-target, .seg-hold').change(function () {
        var idx = $(this).data('idx');
        var value = $(this).val();

        if ($(this).hasClass('seg-rate')) {
            if (value === 'max' || value === 'cool') {
                profile_segments[idx].rate = value;
            } else {
                profile_segments[idx].rate = parseFloat(value) || 0;
            }
        } else if ($(this).hasClass('seg-target')) {
            profile_segments[idx].target = parseFloat(value) || 0;
        } else if ($(this).hasClass('seg-hold')) {
            profile_segments[idx].hold = parseFloat(value) || 0;
        }

        updateGraphFromSegments();
        updateProfileTable_v2();
    });

    $('.del-segment').click(function () {
        var idx = $(this).data('idx');
        profile_segments.splice(idx, 1);
        updateGraphFromSegments();
        updateProfileTable_v2();
    });

    $('.dup-segment').click(function () {
        var idx = $(this).data('idx');
        var src = profile_segments[idx];
        // Shallow copy is fine — rate/target/hold are all primitives.
        var copy = { rate: src.rate, target: src.target, hold: src.hold };
        profile_segments.splice(idx + 1, 0, copy);
        updateGraphFromSegments();
        updateProfileTable_v2();
    });

    $('.move-up').click(function () {
        var idx = $(this).data('idx');
        if (idx <= 0) return;
        var seg = profile_segments.splice(idx, 1)[0];
        profile_segments.splice(idx - 1, 0, seg);
        updateGraphFromSegments();
        updateProfileTable_v2();
    });

    $('.move-down').click(function () {
        var idx = $(this).data('idx');
        if (idx >= profile_segments.length - 1) return;
        var seg = profile_segments.splice(idx, 1)[0];
        profile_segments.splice(idx + 1, 0, seg);
        updateGraphFromSegments();
        updateProfileTable_v2();
    });

    $('#add_segment').click(function () {
        var last_temp = profile_segments.length > 0 ?
            profile_segments[profile_segments.length - 1].target : profile_start_temp;
        profile_segments.push({
            rate: 100,
            target: last_temp + 100,
            hold: 0
        });
        updateGraphFromSegments();
        updateProfileTable_v2();
    });
}
```

- [ ] **Step 3: Add minimal styling so the action buttons fit on one row**

Find the existing `btn-delete-segment` style in `public/assets/css/components.css` (or `widgets.css` — search both):

```bash
grep -n "btn-delete-segment" /Users/galen/Documents/Code/kiln-controller/public/assets/css/*.css
```

Open whichever file owns it. Immediately after the `.btn-delete-segment { ... }` block, append a small rule for the new action buttons:

```css
.seg-actions {
  display: flex;
  gap: 4px;
  align-items: center;
}

.btn-seg-action {
  background: transparent;
  border: 1px solid var(--color-border, #555);
  color: var(--color-text, #ddd);
  border-radius: 3px;
  padding: 2px 6px;
  cursor: pointer;
  font-size: 0.9em;
  line-height: 1;
}

.btn-seg-action:hover:not(:disabled) {
  background: var(--color-surface-hover, rgba(255,255,255,0.08));
}

.btn-seg-action:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}
```

If the codebase doesn't define those CSS variables, substitute the fallback values (`#555`, `#ddd`, `rgba(255,255,255,0.08)`) directly.

- [ ] **Step 4: Smoke-test in the browser**

Run:

```bash
source venv/bin/activate
python kiln-controller.py
```

Open `http://localhost:8081`, open a profile in the editor, and verify:
- Each segment row shows ▲ ▼ ⎘ × buttons
- ▲ is disabled on the first row, ▼ is disabled on the last
- Clicking ⎘ inserts a copy immediately below the source row
- Clicking ▲ / ▼ swaps the segment with its neighbor; the graph updates in real time
- Clicking × deletes the row (unchanged behavior)
- Saving the profile and reloading it preserves the new order

Stop the server when done.

- [ ] **Step 5: Commit**

```bash
git add public/assets/js/picoreflow.js public/assets/css/
git commit -m "feat(ui): add duplicate / move-up / move-down buttons to schedule editor"
```

---

## Verification

After all three tasks are committed, run the full verification gate:

- [ ] **Run all tests:**

```bash
python -m pytest Test/ -v
```

Expected: every test passes (the existing ~80+ tests plus the 4 new ones from Task 1).

- [ ] **Smoke-check imports:**

```bash
python -c "import lib.oven; import lib.mqtt; import lib.ovenWatcher; print('imports OK')"
```

Expected: prints `imports OK`.

- [ ] **Smoke-check server startup:**

```bash
python kiln-controller.py &
sleep 3
curl -s http://localhost:8081/api/stats | head -c 200
kill %1
```

Expected: JSON response with current temperature/state — no traceback in the log.

- [ ] **Manual end-to-end smoke (one short simulated firing):**

Set `sim_speedup_factor = 100` in `config.py` (revert after). Start the server, load any profile with a hold (e.g. `test-fast.json`), start firing. Confirm:
- "Set Rate" shows the ramp rate during ramp and "HOLD" during hold
- An MQTT subscriber on `kiln/firing_status` receives `in_progress` at start and `completed` at end (skip if MQTT not configured locally)
- Schedule editor duplicate/reorder buttons work in the editor view

---

## Self-Review Notes

- All three tasks are independent and can run in parallel via subagents — Task 1 touches only `lib/mqtt.py` + `Test/test_mqtt.py` + `docs/mqtt.md`; Task 2 touches only the live-status branch in `picoreflow.js`; Task 3 touches only the profile-editor functions in `picoreflow.js` + one CSS file. The two `picoreflow.js` tasks edit non-overlapping line ranges (~1912-1929 vs. ~583-693), so they can run concurrently without merge conflicts.
- No placeholders: every code block is complete and matches the patterns already in the repo (jQuery, vanilla JS in `picoreflow.js`; pytest with MagicMock in `Test/`).
- Type consistency: `last_firing_status` and `firing_status` are intentionally different names — the former is the internal Python attribute (already exists, see `oven.py:471`), the latter is the MQTT topic suffix. The plan uses each name consistently.
- Feature 2 has no automated tests because it's a one-branch render decision against an already-tested state field (`segment_phase`). A pytest for vanilla DOM rendering would require pulling in a headless browser harness that the project does not currently have — disproportionate to the change.
