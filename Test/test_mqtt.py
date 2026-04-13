import sys
import os
import time
import json
import pytest
from unittest.mock import MagicMock, patch, call

# Add lib/ to path so we can import mqtt
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))

from mqtt import MQTTClient


@pytest.fixture
def mock_oven():
    oven = MagicMock()
    oven.state = "IDLE"
    return oven


@pytest.fixture
def mqtt_client(mock_oven):
    client = MQTTClient(
        mock_oven,
        host="localhost",
        port=1883,
        topic_prefix="kiln",
        publish_interval=2,
    )
    # Simulate a connected client with a mock paho client
    client.client = MagicMock()
    client.connected = True
    client._last_publish = 0
    return client


class TestPublishThrottling:
    def test_respects_publish_interval(self, mqtt_client):
        state = {"temperature": 100, "state": "RUNNING"}

        mqtt_client._last_publish = time.time()
        mqtt_client.publish_state(state)
        # Should not have published (too soon)
        mqtt_client.client.publish.assert_not_called()

    def test_publishes_after_interval(self, mqtt_client):
        state = {"temperature": 100, "state": "RUNNING"}

        mqtt_client._last_publish = time.time() - 10
        mqtt_client.publish_state(state)
        # Should have published
        assert mqtt_client.client.publish.called

    def test_no_publish_when_disconnected(self, mqtt_client):
        mqtt_client.connected = False
        mqtt_client.publish_state({"temperature": 100})
        mqtt_client.client.publish.assert_not_called()


class TestRetainedTopics:
    def test_individual_topics_published_retained(self, mqtt_client):
        mqtt_client._last_publish = 0
        state = {
            "temperature": 200,
            "target": 500,
            "heat": 75,
            "state": "RUNNING",
            "totaltime": 3600,
        }
        mqtt_client.publish_state(state)

        calls = mqtt_client.client.publish.call_args_list
        # Find retained calls (retain=True)
        retained_calls = [c for c in calls if c[1].get("retain", False) or (len(c[0]) > 3 and c[0][3])]

        # Should have retained topics for temperature, target, heat, eta, state
        retained_topics = [c[0][0] for c in retained_calls]
        assert "kiln/temperature" in retained_topics
        assert "kiln/target" in retained_topics
        assert "kiln/heat" in retained_topics
        assert "kiln/state" in retained_topics

    def test_status_blob_not_retained(self, mqtt_client):
        mqtt_client._last_publish = 0
        state = {"temperature": 200, "state": "RUNNING"}
        mqtt_client.publish_state(state)

        # Find the status call
        for c in mqtt_client.client.publish.call_args_list:
            if c[0][0] == "kiln/status":
                assert c[1].get("retain", c[0][3] if len(c[0]) > 3 else False) is False
                break


class TestChangeOnlyTopics:
    def test_state_not_republished_when_unchanged(self, mqtt_client):
        state = {"temperature": 200, "state": "RUNNING"}

        # First publish
        mqtt_client._last_publish = 0
        mqtt_client.publish_state(state)

        first_count = mqtt_client.client.publish.call_count

        # Second publish with same state
        mqtt_client._last_publish = 0
        mqtt_client.publish_state(state)

        second_count = mqtt_client.client.publish.call_count

        # The change-only topics (state, profile, emergency, segment) should not repeat
        # but status blob + always-retained topics still publish
        # Count state topic appearances
        state_calls = [
            c for c in mqtt_client.client.publish.call_args_list
            if c[0][0] == "kiln/state"
        ]
        assert len(state_calls) == 1  # Only published once

    def test_state_republished_when_changed(self, mqtt_client):
        # First publish RUNNING
        mqtt_client._last_publish = 0
        mqtt_client.publish_state({"temperature": 200, "state": "RUNNING"})

        # Second publish PAUSED
        mqtt_client._last_publish = 0
        mqtt_client.publish_state({"temperature": 200, "state": "PAUSED"})

        state_calls = [
            c for c in mqtt_client.client.publish.call_args_list
            if c[0][0] == "kiln/state"
        ]
        assert len(state_calls) == 2


class TestCommands:
    def test_stop_calls_abort(self, mqtt_client, mock_oven):
        msg = MagicMock()
        msg.payload = b"stop"
        mqtt_client._on_message(None, None, msg)
        mock_oven.abort_run.assert_called_once()

    def test_pause_changes_state(self, mqtt_client, mock_oven):
        mock_oven.state = "RUNNING"
        msg = MagicMock()
        msg.payload = b"pause"
        mqtt_client._on_message(None, None, msg)
        assert mock_oven.state == "PAUSED"

    def test_pause_ignored_when_not_running(self, mqtt_client, mock_oven):
        mock_oven.state = "IDLE"
        msg = MagicMock()
        msg.payload = b"pause"
        mqtt_client._on_message(None, None, msg)
        assert mock_oven.state == "IDLE"

    def test_resume_changes_state(self, mqtt_client, mock_oven):
        mock_oven.state = "PAUSED"
        msg = MagicMock()
        msg.payload = b"resume"
        mqtt_client._on_message(None, None, msg)
        assert mock_oven.state == "RUNNING"

    def test_resume_ignored_when_not_paused(self, mqtt_client, mock_oven):
        mock_oven.state = "RUNNING"
        msg = MagicMock()
        msg.payload = b"resume"
        mqtt_client._on_message(None, None, msg)
        assert mock_oven.state == "RUNNING"

    def test_run_rejected(self, mqtt_client, mock_oven):
        msg = MagicMock()
        msg.payload = b"run"
        mqtt_client._on_message(None, None, msg)
        # Should NOT call run_profile
        mock_oven.run_profile.assert_not_called()

    def test_unknown_command_ignored(self, mqtt_client, mock_oven):
        msg = MagicMock()
        msg.payload = b"explode"
        mqtt_client._on_message(None, None, msg)
        mock_oven.abort_run.assert_not_called()
        mock_oven.run_profile.assert_not_called()


class TestStartStop:
    def test_start_returns_false_when_paho_missing(self, mock_oven):
        client = MQTTClient(mock_oven)
        # Patch the import inside start() to raise ImportError
        with patch("builtins.__import__", side_effect=ImportError("no paho")):
            result = client.start()
        assert result is False

    def test_on_connect_publishes_online_and_subscribes(self, mqtt_client):
        mqtt_client._on_connect(mqtt_client.client, None, None, 0)
        mqtt_client.client.publish.assert_any_call(
            "kiln/available", "online", qos=1, retain=True
        )
        mqtt_client.client.subscribe.assert_called_with("kiln/command")

    def test_stop_publishes_offline(self, mqtt_client):
        mqtt_client.stop()
        mqtt_client.client.publish.assert_any_call(
            "kiln/available", "offline", qos=1, retain=True
        )
        assert mqtt_client.connected is False


class TestHeatRate:
    def test_heat_rate_published(self, mqtt_client):
        mqtt_client._last_publish = 0
        state = {
            "temperature": 200,
            "state": "RUNNING",
            "heat_rate": 125.5,
            "target_heat_rate": 150.0,
        }
        mqtt_client.publish_state(state)

        calls = mqtt_client.client.publish.call_args_list
        heat_rate_calls = [c for c in calls if c[0][0] == "kiln/heat_rate"]
        target_rate_calls = [c for c in calls if c[0][0] == "kiln/target_rate"]

        assert len(heat_rate_calls) == 1
        assert heat_rate_calls[0][0][1] == "125.5"
        assert len(target_rate_calls) == 1
        assert target_rate_calls[0][0][1] == "150.0"


class TestTopicPrefix:
    def test_custom_prefix(self, mock_oven):
        client = MQTTClient(mock_oven, topic_prefix="mykiln")
        client.client = MagicMock()
        client.connected = True
        client._last_publish = 0

        client.publish_state({"temperature": 100, "state": "IDLE"})
        topics = [c[0][0] for c in client.client.publish.call_args_list]
        assert any(t.startswith("mykiln/") for t in topics)
        assert not any(t.startswith("kiln/") for t in topics)
