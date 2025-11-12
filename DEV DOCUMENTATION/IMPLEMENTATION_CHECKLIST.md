# Bug Fix Implementation Checklist

Use this checklist to track progress during implementation.

## Pre-Implementation Setup

- [ ] Create backup of entire project directory
- [ ] Create backup of `storage/profiles/` directory
- [ ] Create backup of `config.py`
- [ ] Document current config settings
- [ ] Create git branch `fix/critical-safety-bugs`
- [ ] Ensure working in virtual environment
- [ ] Install all dependencies from `requirements.txt`
- [ ] Run existing tests to establish baseline

---

## Phase 1: Critical Safety Fixes

### Bug #1: List Modification During Iteration
**File:** `lib/ovenWatcher.py`

- [ ] **Backup file:** `cp lib/ovenWatcher.py lib/ovenWatcher.py.backup`
- [ ] **Change line 83:** `for wsock in self.observers:` → `for wsock in self.observers[:]:`
- [ ] **Change line 38:** Add better exception logging
- [ ] **Test:** Create unit test for multiple socket failures
- [ ] **Verify:** No iteration errors with mock sockets
- [ ] **Commit:** `git commit -m "Fix list modification during iteration in notify_all"`

---

### Bug #2: Schedule Completion Crash
**File:** `lib/oven.py`

- [ ] **Backup file:** `cp lib/oven.py lib/oven.py.backup`
- [ ] **Modify `get_surrounding_points` (lines 763-776):**
  - [ ] Add check for `time >= self.data[-1][0]`
  - [ ] Return last two points for end-of-schedule
  - [ ] Handle single-point profiles
- [ ] **Modify `get_target_temperature` (lines 778-786):**
  - [ ] Add None checks for prev_point and next_point
  - [ ] Add check for identical points (flat segment)
  - [ ] Add error logging
- [ ] **Test:** Create profile, get temp at exact duration
- [ ] **Test:** Create single-point profile
- [ ] **Verify:** No crashes at schedule completion
- [ ] **Commit:** `git commit -m "Fix schedule completion crash with edge case handling"`

---

### Bug #4: Double Temperature Conversion
**File:** `kiln-controller.py`

- [ ] **Backup file:** `cp kiln-controller.py kiln-controller.py.backup`
- [ ] **Import at top:** Add `import copy`
- [ ] **Modify `add_temp_units` (lines 288-300):**
  - [ ] Simplify to just add field if missing
  - [ ] Assume profiles without field are in Fahrenheit (matches existing)
- [ ] **Modify `convert_to_c` (lines 302-308):**
  - [ ] Add idempotency check at start
  - [ ] Return early if already in C
  - [ ] Fix formula: `(temp - 32) * 5 / 9`
- [ ] **Modify `convert_to_f` (lines 310-316):**
  - [ ] Add idempotency check at start
  - [ ] Return early if already in F
  - [ ] Fix formula: `(temp * 9 / 5) + 32`
- [ ] **Modify `normalize_temp_units` (lines 318-326):**
  - [ ] Use `copy.deepcopy()` for each profile
  - [ ] Add temp_units to profiles missing it
  - [ ] Convert based on comparison of profile vs config scale
- [ ] **Modify `save_profile` (lines 274-286):**
  - [ ] Always convert to Fahrenheit before saving (maintain F standard)
  - [ ] Set temp_units to "f" in saved file
  - [ ] Note: Thermocouple reads C, converts automatically - leave unchanged
- [ ] **Test:** Load profile twice, verify temps unchanged
- [ ] **Test:** Convert C→F→C, verify within 0.1°
- [ ] **Verify:** All existing profiles still load correctly
- [ ] **Commit:** `git commit -m "Fix temperature conversion with idempotent operations"`

---

### Phase 1 Testing

- [ ] **Run simulation:**
  - [ ] Full schedule from start to finish
  - [ ] Verify no crashes at completion
  - [ ] Verify temperatures displayed correctly
- [ ] **Test temperature conversion:**
  - [ ] Switch config C→F, reload profiles
  - [ ] Switch config F→C, reload profiles
  - [ ] Verify temps correct both ways
- [ ] **Test websockets:**
  - [ ] Connect multiple clients
  - [ ] Disconnect clients during operation
  - [ ] Verify no errors in logs
- [ ] **Merge to main:** `git merge fix/critical-safety-bugs`
- [ ] **Tag release:** `git tag v1.1.0-critical-fixes`

---

## Phase 2: High Priority Fixes

- [ ] **Create branch:** `git checkout -b fix/high-priority-bugs`

### Bug #3: Ambiguous Return Value
**File:** `lib/oven.py`

- [ ] **Modify `find_x_given_y_on_line_from_two_points` (lines 742-747):**
  - [ ] Add docstring explaining return values
  - [ ] Return `None` for invalid point order
  - [ ] Return `None` for non-positive slope
  - [ ] Return `None` for y out of range
  - [ ] Add debug logging for each case
  - [ ] Keep existing formula for valid case
- [ ] **Modify `find_next_time_from_temperature` (lines 749-761):**
  - [ ] Change check from `if time == 0:` to `if result is None:`
  - [ ] Handle flat segment case explicitly
  - [ ] Add comments explaining logic
- [ ] **Test:** Cooling segment returns None
- [ ] **Test:** Valid rising segment returns time
- [ ] **Test:** Seek start with various profiles
- [ ] **Commit:** `git commit -m "Use None for errors in temperature seek calculations"`

---

### Bug #5: Backwards Temperature Conversion
**File:** `kiln-tuner.py`

- [ ] **Backup file:** `cp kiln-tuner.py kiln-tuner.py.backup`
- [ ] **Modify argument parser (line 186-191):**
  - [ ] Add `--input-scale` argument
  - [ ] Set default to `config.temp_scale`
  - [ ] Add help text
- [ ] **Modify temperature handling (lines 193-197):**
  - [ ] Remove incorrect conversion
  - [ ] Add correct conversion logic
  - [ ] Convert FROM input_scale TO config.temp_scale
- [ ] **Test:** F input with C config
- [ ] **Test:** C input with C config
- [ ] **Test:** Verify PID calculations reasonable
- [ ] **Commit:** `git commit -m "Fix temperature conversion logic in kiln-tuner"`

---

### Bug #7: Multiple If Instead of Elif
**File:** `kiln-controller.py`

- [ ] **Modify `/api` handler (lines 59-115):**
  - [ ] Add JSON validation at top
  - [ ] Add cmd field validation
  - [ ] Change all `if` to `elif` (except first)
  - [ ] Add state validation for pause/resume
  - [ ] Add `else:` clause for unknown commands
  - [ ] Make all commands return immediately
  - [ ] Add specific error messages
- [ ] **Test:** Send each command type
- [ ] **Test:** Send invalid commands
- [ ] **Test:** Try to pause when not running
- [ ] **Test:** Try to resume when not paused
- [ ] **Verify:** Only one command executes per request
- [ ] **Commit:** `git commit -m "Fix API handler to use elif and validate state"`

---

### Bug #6: No File Locking
**File:** `lib/oven.py`

- [ ] **Add imports at top:**
  - [ ] `import tempfile`
  - [ ] `import fcntl` (in try/except for Windows)
- [ ] **Modify `save_state` (lines 489-491):**
  - [ ] Create temp file in same directory
  - [ ] Write to temp file with fsync
  - [ ] Atomic rename to target file
  - [ ] Add try/except for cleanup
  - [ ] Add error logging
- [ ] **Modify `should_i_automatic_restart` (lines 512-525):**
  - [ ] Add try/except for file operations
  - [ ] Add exception logging
  - [ ] Return False on any error
- [ ] **Test:** Verify state file always valid JSON
- [ ] **Test:** Rapid writes don't corrupt file
- [ ] **Verify:** .tmp files cleaned up
- [ ] **Commit:** `git commit -m "Add atomic writes for state file safety"`

---

### Phase 2 Testing

- [ ] **Run simulation:**
  - [ ] Test seek start feature
  - [ ] Test automatic restart
  - [ ] Test API commands
  - [ ] Verify state file integrity
- [ ] **Test API:**
  - [ ] All commands work correctly
  - [ ] State transitions validated
  - [ ] Error messages clear
- [ ] **Test tuner:**
  - [ ] Run tuning with C config
  - [ ] Run tuning with F config
  - [ ] Verify PID values reasonable
- [ ] **Merge to main:** `git merge fix/high-priority-bugs`
- [ ] **Tag release:** `git tag v1.2.0-high-priority-fixes`

---

## Phase 3: Quality Improvements

- [ ] **Create branch:** `git checkout -b fix/quality-improvements`

### Bug #8: PID Integral Windup
**File:** `lib/oven.py`

- [ ] **Modify PID `compute` method (lines 832-840):**
  - [ ] Calculate i_contribution separately
  - [ ] Calculate output_unclamped before clamp
  - [ ] Clamp output
  - [ ] Compare unclamped vs clamped
  - [ ] Only add to iterm if not saturated
  - [ ] Add debug logging when saturated
- [ ] **Test:** Large sustained error
- [ ] **Test:** Verify integral doesn't grow unbounded
- [ ] **Test:** Normal operation still works
- [ ] **Verify:** Better temperature control
- [ ] **Commit:** `git commit -m "Add PID anti-windup to prevent overshoot"`

---

### Phase 3 Testing

- [ ] **Run simulation:**
  - [ ] Full schedule with various profiles
  - [ ] Compare overshoot before/after
  - [ ] Verify oscillation reduced
  - [ ] Check settle time
- [ ] **Consider re-tuning:**
  - [ ] Note current PID values
  - [ ] Run tuner if needed
  - [ ] Update config if improved
- [ ] **Merge to main:** `git merge fix/quality-improvements`
- [ ] **Tag release:** `git tag v1.3.0-quality-improvements`

---

## Post-Implementation

### Documentation

- [ ] **Update README.md:**
  - [ ] Add temperature scale notes
  - [ ] Document automatic restart behavior
  - [ ] Document seek start behavior
  - [ ] Update version number
- [ ] **Create TEMPERATURE_SCALES.md:**
  - [ ] Explain conversion policy
  - [ ] Document storage format
  - [ ] Provide migration instructions
- [ ] **Create TESTING.md:**
  - [ ] Document test procedures
  - [ ] List manual test checklist
  - [ ] Explain simulation testing
- [ ] **Update inline code comments:**
  - [ ] Add docstrings to modified functions
  - [ ] Explain design decisions
  - [ ] Document thread safety

---

### Final Testing

- [ ] **Full simulation suite:**
  - [ ] Test each profile in storage/profiles/
  - [ ] Verify all complete without errors
  - [ ] Check temperature accuracy
  - [ ] Verify cost calculations
- [ ] **Manual testing:**
  - [ ] Start/stop/pause/resume
  - [ ] Emergency shutdown
  - [ ] Automatic restart
  - [ ] WebSocket connect/disconnect
  - [ ] Profile save/load/delete
  - [ ] Temperature scale switching
- [ ] **Real kiln test (if available):**
  - [ ] Run short test profile
  - [ ] Monitor closely
  - [ ] Compare to external thermometer
  - [ ] Verify proper shutdown
  - [ ] Check automatic restart

---

### Code Review

- [ ] **Review all changes:**
  - [ ] Check for unintended side effects
  - [ ] Verify error handling complete
  - [ ] Ensure logging adequate
  - [ ] Check thread safety
- [ ] **Review tests:**
  - [ ] Verify test coverage
  - [ ] Check edge cases covered
  - [ ] Ensure tests are meaningful
- [ ] **Review documentation:**
  - [ ] Verify accuracy
  - [ ] Check for clarity
  - [ ] Ensure completeness

---

### Deployment

- [ ] **Prepare release notes:**
  - [ ] List all bugs fixed
  - [ ] Note any breaking changes
  - [ ] Provide migration instructions
  - [ ] Thank contributors
- [ ] **Create release:**
  - [ ] Tag version `v1.3.0`
  - [ ] Create GitHub release
  - [ ] Attach release notes
- [ ] **Notify users:**
  - [ ] Post to forum/mailing list
  - [ ] Update project page
  - [ ] Note critical nature of fixes

---

### Monitoring

- [ ] **First week:**
  - [ ] Monitor logs daily
  - [ ] Watch for new error patterns
  - [ ] Collect user feedback
  - [ ] Address issues promptly
- [ ] **First month:**
  - [ ] Monitor weekly
  - [ ] Track PID performance
  - [ ] Note any regressions
  - [ ] Plan future improvements

---

## Rollback Procedures

### If Critical Issue Found

1. **Identify scope:**
   - [ ] Which fix caused the issue?
   - [ ] Which phase?
   - [ ] All fixes or specific one?

2. **Quick rollback:**
   - [ ] `git revert <commit-hash>`
   - [ ] Or `git checkout <previous-tag>`
   - [ ] Test reverted version
   - [ ] Notify users

3. **Fix forward:**
   - [ ] Diagnose root cause
   - [ ] Fix the fix
   - [ ] Test thoroughly
   - [ ] Re-deploy

### Restore from Backup

If git unavailable:
- [ ] Copy from backup directory
- [ ] Restore profiles from backup
- [ ] Restore config from backup
- [ ] Test restored version

---

## Success Criteria

### All phases complete when:

- [ ] ✅ All 8 bugs fixed
- [ ] ✅ All tests passing
- [ ] ✅ Full simulation successful
- [ ] ✅ Documentation updated
- [ ] ✅ Code reviewed
- [ ] ✅ Real kiln test passed (if available)
- [ ] ✅ No regressions detected
- [ ] ✅ Performance acceptable
- [ ] ✅ Users notified
- [ ] ✅ Release published

---

## Estimated Time

| Phase | Coding | Testing | Total |
|-------|--------|---------|-------|
| **Phase 1** | 4 hours | 4 hours | 8 hours |
| **Phase 2** | 4 hours | 4 hours | 8 hours |
| **Phase 3** | 2 hours | 2 hours | 4 hours |
| **Documentation** | 2 hours | 1 hour | 3 hours |
| **Final Testing** | 0 hours | 4 hours | 4 hours |
| **Total** | 12 hours | 15 hours | **27 hours** |

Spread over 7 days = ~4 hours per day

---

## Notes Section

Use this space for notes during implementation:

### Issues Encountered:
- 

### Decisions Made:
- 

### Deviations from Plan:
- 

### Things to Remember:
- 

---

**Status:** Ready to begin  
**Start Date:** ___________  
**Target Completion:** ___________  
**Actual Completion:** ___________

