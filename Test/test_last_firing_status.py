"""Tests for the last_firing_status field on Oven.

This field carries the outcome of the most recent firing across the
RUNNING→IDLE transition so external consumers (MQTT, dashboards) can tell
whether the kiln finished cleanly, was aborted, or hit an emergency.
"""
from unittest.mock import MagicMock

import pytest

from lib.oven import SimulatedOven


@pytest.fixture
def oven(monkeypatch):
    """Construct a SimulatedOven with disk I/O stubbed out.

    The Oven's daemon thread starts inside __init__; that's fine for these
    tests because we never transition to RUNNING and the loop just sleeps.
    """
    o = SimulatedOven()
    monkeypatch.setattr(o, "save_firing_log", lambda *a, **kw: None)
    monkeypatch.setattr(o, "save_resume_state", lambda *a, **kw: None)
    monkeypatch.setattr(o, "save_state", lambda *a, **kw: None)
    monkeypatch.setattr(o, "clear_automatic_restart_state", lambda *a, **kw: None)
    return o


class TestLastFiringStatusField:
    def test_initial_value_is_none(self, oven):
        assert oven.last_firing_status is None

    def test_get_state_includes_field(self, oven):
        state = oven.get_state()
        assert "last_firing_status" in state
        assert state["last_firing_status"] is None

    def test_get_state_reflects_updated_value(self, oven):
        oven.last_firing_status = "completed"
        assert oven.get_state()["last_firing_status"] == "completed"


class TestAbortPath:
    def test_abort_run_with_profile_sets_aborted(self, oven):
        oven.state = "RUNNING"
        oven.profile = MagicMock(name="profile")
        oven.abort_run()
        assert oven.last_firing_status == "aborted"

    def test_abort_run_no_profile_still_sets_aborted(self, oven):
        # Even without a profile (e.g. abort during startup), the outcome
        # should be recorded so consumers see "aborted" rather than stale value.
        oven.state = "RUNNING"
        oven.profile = None
        oven.abort_run()
        assert oven.last_firing_status == "aborted"


class TestEmergencyPath:
    def test_default_status_is_emergency_stop(self, oven):
        oven._emergency_shutdown("thermocouple disconnected")
        assert oven.last_firing_status == "emergency_stop"

    def test_custom_status_is_recorded(self, oven):
        oven._emergency_shutdown("thermal runaway", status="runaway")
        assert oven.last_firing_status == "runaway"


class TestResetDoesNotClearOutcome:
    """reset() runs after the outcome is recorded; it must not wipe the field."""

    def test_reset_preserves_aborted(self, oven):
        oven.last_firing_status = "aborted"
        oven.reset()
        assert oven.last_firing_status == "aborted"

    def test_reset_preserves_completed(self, oven):
        oven.last_firing_status = "completed"
        oven.reset()
        assert oven.last_firing_status == "completed"


class TestStateMachineConsumerView:
    """The intended consumer pattern: branch on last_firing_status when state
    transitions RUNNING→IDLE."""

    def test_completed_outcome_visible_with_idle_state(self, oven):
        oven.last_firing_status = "completed"
        oven.state = "IDLE"
        s = oven.get_state()
        assert s["state"] == "IDLE"
        assert s["last_firing_status"] == "completed"

    def test_aborted_outcome_visible_with_idle_state(self, oven):
        oven.last_firing_status = "aborted"
        oven.state = "IDLE"
        s = oven.get_state()
        assert s["state"] == "IDLE"
        assert s["last_firing_status"] == "aborted"

    def test_emergency_outcome_visible_with_idle_state(self, oven):
        oven.last_firing_status = "emergency_stop"
        oven.state = "IDLE"
        s = oven.get_state()
        assert s["state"] == "IDLE"
        assert s["last_firing_status"] == "emergency_stop"
