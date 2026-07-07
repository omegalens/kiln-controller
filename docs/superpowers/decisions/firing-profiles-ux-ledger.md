# Decision Ledger — Firing Profiles UX (duplicate / drag / insert)

Created 2026-07-07 during brainstorming. Interactive mockup reviewed and approved by Galen
(artifact: claude.ai/code/artifact/1a344485-f071-49c7-b712-128108aaef80).
IDs are append-only and immutable. Later stages fill their own column against the same row.

| ID | Decision | Mockup ref | Breakpoints | Spec § | Plan task(s) | Assertion | Visual gate |
|----|----------|------------|-------------|--------|--------------|-----------|-------------|
| D001 | Profiles panel gets a **Duplicate** button between "+ New" and "Edit" that opens edit mode pre-filled with a deep copy of the selected profile, name "\<name\> (copy)", saved only on Save — explicitly NOT an instant-save copy. | [mockups/D001.png](mockups/D001.png) | 320, 1440 | | | | PENDING |
| D002 | Segment rows are reordered by a **grip drag handle in a new far-left column**, implemented with pointer events (works for mouse and touch) — explicitly NOT the ▲▼ move-up/move-down buttons (removed) and NOT HTML5 drag-and-drop. | [mockups/D002.png](mockups/D002.png) | 320, 768, 1440 | | | | PENDING |
| D003 | Segments are added via **always-visible "+" insert zones** above the first row, between every pair of rows, and after the last row — explicitly NOT hover-revealed zones and NOT the single bottom "+ Segment" button (removed). | [mockups/D003.png](mockups/D003.png) | 320, 768, 1440 | | | | PENDING |
| D004 | A segment inserted between two rows is pre-filled by **interpolating its neighbors**: target = rounded midpoint of previous target (or start temp) and next target; rate = 100 with sign matching direction; hold = 0. Terminal insert keeps today's defaults (prev target + 100, rate 100, hold 0) — explicitly NOT a clone of the previous row and NOT a fixed default mid-list. | N/A (behavioral — values, not layout) | N/A | | | | N/A |
| D005 | The per-row **duplicate ⎘ button is removed** from segment rows; row actions are delete × only — explicitly NOT keeping ⎘ alongside the drag handle. | [mockups/D005.png](mockups/D005.png) | 320, 768, 1440 | | | | PENDING |

Notes:
- D002/D003/D005 share one editor mockup image; each row's committed copy is its own file per the ledger convention.
- Rejected alternatives were considered live in the mockup: hover-reveal insert zones (rejected for D003 — studio tablet has no hover) and instant-save duplicate (rejected for D001).
