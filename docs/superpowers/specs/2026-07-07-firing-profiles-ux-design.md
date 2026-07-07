# Firing Profiles UX — Duplicate, Drag Handles, In-Between Insert

**Date:** 2026-07-07
**Status:** Approved by Galen (mockup iterated live; final revision removes the per-row ⎘ button)
**Decision ledger:** [../decisions/firing-profiles-ux-ledger.md](../decisions/firing-profiles-ux-ledger.md)
**Mockup:** claude.ai/code/artifact/1a344485-f071-49c7-b712-128108aaef80 (snapshots committed under `../decisions/mockups/`)

## Scope

Frontend-only changes to the firing-profiles UI. No backend, WebSocket-protocol, or profile-format
changes: all three features operate on the client-side `profile_segments` array and reuse the
existing `saveProfile()` PUT path over `ws_storage`.

Files touched:

- `public/index.html` — add the Duplicate button to `.profile-actions`
- `public/assets/js/picoreflow.js` — `updateProfileTable_v2()`, `bindSegmentEvents()`, new
  `enterDuplicateMode()`, new drag/insert logic, button wiring in the ready handler
- `public/assets/css/widgets.css` — this is where `.segment-table`, `.btn-seg-action`, and
  `.btn-delete-segment` live; replace segment-table styles with grid-row styles; add drag-handle
  and insert-zone styles
- `public/assets/css/responsive.css` — touch/narrow-width rules for the new controls if needed

## Feature 1 — Duplicate profile (D001)

A `Duplicate` button sits between `+ New` and `Edit` in the Firing Profiles panel
(`#btn_dup`, same `.btn btn-icon` styling as its neighbors).

Behavior — `enterDuplicateMode()`:

1. Reads `profiles[selected_profile]` exactly as `enterEditMode()` does (including the existing
   v1→segments conversion in `loadProfileForEditing()`).
2. Deep-copies the segments into `profile_segments` (no shared references with the source profile).
3. Sets `#form_profile_name` to `"<name> (copy)"`.
4. Opens the edit panel. Nothing is written to storage until the user hits Save; Save goes through
   the unchanged `saveProfile()` validation + PUT.

Name collisions keep existing PUT semantics: saving a name that already exists overwrites it,
same as today for any name. No new collision handling.

## Feature 2 — Drag-handle reordering (D002)

The ▲▼ move-up/move-down buttons and their handlers are **removed**. Each segment row gains a
**grip handle** (6-dot SVG) as the first column, followed by the row number.

Implementation:

- Pointer events only (`pointerdown` on the handle, `setPointerCapture`, `pointermove`,
  `pointerup`/`pointercancel`), with `touch-action: none` on the handle so it works identically
  with mouse and studio-tablet touch. HTML5 drag-and-drop is explicitly not used (no touch support).
- While dragging: the row becomes a fixed-position ghost following the pointer
  (`.dragging` — orange border, shadow); a dashed **drop slot** placeholder shows the landing
  position, computed by comparing pointer Y against the midpoints of the other rows' original rects.
- On drop: splice the segment from its old index to the new index in `profile_segments`, then
  `updateGraphFromSegments()` + `updateProfileTable_v2()` re-render (row numbers and the Est.
  column recompute automatically).
- Drag starts only on the handle, so the rate/target/hold inputs keep normal focus/edit behavior.
- Row actions on the right are delete × only (D005 — the ⎘ duplicate button is removed along with
  its handler).

### Markup change: table → grid rows

The current `<table class="segment-table">` cannot cleanly host fixed-position drag ghosts and
between-row insert zones. `updateProfileTable_v2()` switches to div-based rows:

- `.seg-header` — column labels (handle spacer, #, Rate, Target, Hold, Est., actions)
- `.seg-row` — CSS grid, columns `34px 30px 1fr 1fr 1fr 74px 34px`, one per segment
- `.insert-zone` — thin flex row hosting the "+" button, interleaved between `.seg-row`s

Same visual language as today (inset background, subtle border, 8px radius — exactly what the
approved mockup shows, which was built from the app's `variables.css` tokens). The old
`.segment-table` CSS is deleted in the same change (superseded, not orphaned).

## Feature 3 — In-between insert (D003, D004)

The bottom `#add_segment` "+ Segment" button and its handler are **removed**. Insert zones render:

- above the first row,
- between every adjacent pair of rows,
- after the last row (this terminal zone is the replacement for "+ Segment"),
- and as the only element when the profile has zero segments.

All zones are **always visible** (decided; hover-reveal rejected because the studio tablet has no
hover). Each zone is a small circular "+" button centered on a faint horizontal line; green accent
on hover/focus per the mockup.

Inserted values (D004):

- **Between rows:** `target` = rounded midpoint of the previous row's target (or start temp when
  inserting at position 0) and the next row's target; `rate` = 100 with sign matching direction
  (negative when the midpoint is below the previous temp); `hold` = 0. The firing curve barely
  changes shape on insert.
- **Terminal (after last row / empty profile):** today's add defaults — `target` = previous target
  (or start temp) + 100, `rate` = 100, `hold` = 0.

After insert: `updateGraphFromSegments()` + `updateProfileTable_v2()`.

## Error handling

No new error surface. Segment validation stays in `saveProfile()` (positive-rate-with-decreasing-
target etc.), which already runs after any reorder/insert since those only mutate
`profile_segments` pre-save. Drag is cancelled safely on `pointercancel` (segment stays at its
original index if the drop index never changed).

## Verification

1. `python -m pytest Test/` — must stay green (backend untouched).
2. `python -c "import lib.oven"` — imports clean.
3. Browser pass in sim mode (Playwright against `http://localhost:8081`):
   - Duplicate → editor opens pre-filled with "(copy)" name → rename → Save → new profile appears
     in list; original unchanged.
   - Drag row 1 below row 2 → order, row numbers, and Est. column update; saved profile preserves
     the new order.
   - Insert at position 0, mid-list, and terminal → interpolated/default values per D004; graph
     updates.
   - Delete × still works; no ⎘ button present in any row (D005 negative check).
   - Old controls absent: no ▲▼ buttons, no bottom "+ Segment" button (D002/D003 negative checks).
4. Design-Fidelity Gate per the ledger: fresh agent compares rendered UI against the committed
   mockup PNGs at each row's breakpoints (320/768/1440), records PASS/FAIL + screenshots, fills
   the Visual gate column. Includes the decision-independent smoke check at 320/375 (no horizontal
   overflow; no overlapping interactive controls — the always-visible "+" buttons and drag handles
   are the new risk surface at narrow widths).

## Out of scope

- Keyboard-accessible reordering — explicitly not wanted (decided 2026-07-07); reordering is
  drag-only.
- Undo/redo in the editor.
- Any change to profile storage format or backend endpoints.
