"""Tests for OvenWatcher curve-anchoring behavior.

Regression coverage for the "graph dip" bug: when a firing starts with the
kiln already past the first segment's target, the displayed firing curve
must not dip downward through the already-passed targets.
"""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'lib')))

import config  # noqa: E402
from lib.oven import Profile  # noqa: E402
from lib.ovenWatcher import OvenWatcher  # noqa: E402


class _FakeOven:
    """Minimal oven stub: enough surface for OvenWatcher to read state."""

    def __init__(self, temperature, current_segment_index=0, time_step=60):
        self.temperature = temperature
        self.current_segment_index = current_segment_index
        self.time_step = time_step

    def get_state(self):
        return {
            'temperature': self.temperature,
            'state': 'IDLE',
            'runtime': 0,
            'target': 0,
        }


def _make_profile():
    return Profile(json.dumps({
        "name": "dip-test",
        "version": 2,
        "start_temp": 65,
        "temp_units": "f",
        "segments": [
            {"rate": 100, "target": 200, "hold": 0},
            {"rate": 250, "target": 1900, "hold": 0},
            {"rate": 500, "target": 2167, "hold": 30},
            {"rate": -200, "target": 1900, "hold": 60},
            {"rate": -100, "target": 1400, "hold": 0},
        ],
    }))


def _ramp_to_peak_is_monotonic(data):
    """The pre-peak portion of the curve must never decrease."""
    temps = [pt[1] for pt in data]
    peak_idx = temps.index(max(temps))
    return all(temps[i] <= temps[i + 1] for i in range(peak_idx))


def _make_watcher(oven):
    watcher = OvenWatcher(oven)
    # The watcher starts a background thread; tests only need record() to run.
    # Mark daemon so it doesn't block teardown (already daemon=True per code).
    return watcher


def test_hot_start_with_stale_segment_index_does_not_dip():
    """Bug repro: kiln is hot (881F) but current_segment_index stuck at 0.

    Before the fix to_legacy_format produced a downward ramp from 881 -> 200
    (already-passed seg0 target), visible as a "dip" in the live graph.
    """
    profile = _make_profile()
    oven = _FakeOven(temperature=881, current_segment_index=0)
    watcher = _make_watcher(oven)

    watcher.record(profile)
    data = watcher.adjusted_profile_data

    assert data[0] == [0, 881], "Curve must start at the kiln's actual temperature"
    assert _ramp_to_peak_is_monotonic(data), (
        "Curve must not dip below the start temp before reaching the peak; "
        f"got {data}"
    )


def test_cold_start_unchanged():
    """Cold fresh start should still render the full profile from segment 0."""
    profile = _make_profile()
    oven = _FakeOven(temperature=65, current_segment_index=0)
    watcher = _make_watcher(oven)

    watcher.record(profile)
    data = watcher.adjusted_profile_data

    assert data[0] == [0, 65]
    # Full profile shape preserved: drying step to 200 is present.
    temps = [pt[1] for pt in data]
    assert 200 in temps


def test_hot_start_with_correct_segment_index_unchanged():
    """When seek already advanced current_segment_index, no double-skip."""
    profile = _make_profile()
    oven = _FakeOven(temperature=881, current_segment_index=1)
    watcher = _make_watcher(oven)

    watcher.record(profile)
    data = watcher.adjusted_profile_data

    assert data[0] == [0, 881]
    assert _ramp_to_peak_is_monotonic(data)
    # Should land at 1900 next (segment 1's target), not back through 200.
    assert data[1][1] == 1900


def test_resume_mid_cool_respects_oven_segment_index():
    """Resuming partway through a cooling segment must not climb back up.

    If the oven says we're at segment 3 (cooling) the curve should start at
    the current temp and continue cooling, not pretend to climb to the peak.
    """
    profile = _make_profile()
    # Mid crystal-cool: kiln at 2000F, segment 3 (cooling to 1900)
    oven = _FakeOven(temperature=2000, current_segment_index=3)
    watcher = _make_watcher(oven)

    watcher.record(profile)
    data = watcher.adjusted_profile_data

    assert data[0] == [0, 2000]
    # No re-climb back through 2167 (that would mean we used seek_idx instead
    # of respecting the resume segment).
    temps = [pt[1] for pt in data]
    assert max(temps) == 2000
