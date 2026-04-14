import logging
import time
import threading
import json

log = logging.getLogger(__name__)


class MQTTClient:
    def __init__(self, oven, host="localhost", port=1883, topic_prefix="kiln",
                 publish_interval=2, username=None, password=None):
        self.oven = oven
        self.host = host
        self.port = port
        self.prefix = topic_prefix
        self.publish_interval = publish_interval
        self.username = username
        self.password = password
        self.client = None
        self.connected = False
        self._last_publish = 0
        self._last_values = {}

    def start(self):
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            log.warning("paho-mqtt not installed, MQTT disabled")
            return False

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

        if self.username:
            self.client.username_pw_set(self.username, self.password)

        self.client.will_set(
            f"{self.prefix}/available", "offline", qos=1, retain=True
        )

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        self.client.loop_start()
        try:
            self.client.connect_async(self.host, self.port)
        except Exception as e:
            log.error("MQTT connect failed: %s" % e)
            return False

        log.info("MQTT client started (broker %s:%d)" % (self.host, self.port))
        return True

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            log.info("MQTT connected to broker")
            self.connected = True
            client.publish(f"{self.prefix}/available", "online", qos=1, retain=True)
            client.subscribe(f"{self.prefix}/command")
            # Publish zone names (retained, once on connect)
            zone_configs = []
            try:
                from lib.oven import get_zone_configs
                zone_configs = get_zone_configs()
            except Exception:
                pass
            for i, zc in enumerate(zone_configs):
                client.publish(f"{self.prefix}/zone/{i}/name",
                               zc.get("name", f"Zone {i}"), qos=1, retain=True)
        else:
            log.error("MQTT connect returned rc=%s" % rc)

    def _on_disconnect(self, client, userdata, flags, rc, properties=None):
        self.connected = False
        log.warning("MQTT disconnected (rc=%s), will auto-reconnect" % rc)

    def _on_message(self, client, userdata, msg):
        payload = msg.payload.decode("utf-8", errors="replace").strip().lower()
        log.info("MQTT command received: %s" % payload)

        if payload == "stop":
            self.oven.abort_run()
        elif payload == "pause":
            if self.oven.state == "RUNNING":
                self.oven.state = "PAUSED"
                log.info("MQTT: paused firing")
            else:
                log.warning("MQTT: cannot pause, state is %s" % self.oven.state)
        elif payload == "resume":
            if self.oven.state == "PAUSED":
                self.oven.state = "RUNNING"
                log.info("MQTT: resumed firing")
            else:
                log.warning("MQTT: cannot resume, state is %s" % self.oven.state)
        elif payload == "run":
            log.warning("MQTT: 'run' command rejected — use the web UI to start firings")
        else:
            log.warning("MQTT: unknown command '%s'" % payload)

    def publish_state(self, state_dict):
        if not self.connected or not self.client:
            return

        now = time.time()
        if now - self._last_publish < self.publish_interval:
            return
        self._last_publish = now

        # Full state blob (not retained)
        try:
            self.client.publish(
                f"{self.prefix}/status", json.dumps(state_dict), qos=0, retain=False
            )
        except Exception as e:
            log.error("MQTT publish error: %s" % e)
            return

        # Individual retained topics
        retained = {
            "temperature": state_dict.get("temperature"),
            "target": state_dict.get("target"),
            "heat": state_dict.get("heat"),
            "heat_rate": state_dict.get("heat_rate"),  # actual heating rate (°/hr)
            "target_rate": state_dict.get("target_heat_rate"),  # target heating rate (°/hr)
            "eta": state_dict.get("totaltime"),
        }
        for key, value in retained.items():
            if value is not None:
                self._publish_retained(key, str(value))

        # Change-only retained topics
        change_only = {
            "state": state_dict.get("state"),
            "profile": state_dict.get("profile"),
            "emergency": state_dict.get("emergency", ""),
        }

        # Build segment info if available
        segment_info = {}
        if state_dict.get("segment_index") is not None:
            segment_info = {
                "index": state_dict.get("segment_index"),
                "phase": state_dict.get("phase", ""),
                "total": state_dict.get("total_segments", 0),
            }
        change_only["segment"] = json.dumps(segment_info) if segment_info else ""

        for key, value in change_only.items():
            str_val = str(value) if value is not None else ""
            if self._last_values.get(key) != str_val:
                self._last_values[key] = str_val
                self._publish_retained(key, str_val)

        # Per-zone topics
        zones = state_dict.get("zones", [])
        for zone in zones:
            i = zone["index"]
            zone_topics = {
                "temperature": zone.get("temperature"),
                "target": zone.get("target"),
                "heat": round(zone.get("heat", 0), 3),
            }
            for key, value in zone_topics.items():
                topic = f"{self.prefix}/zone/{i}/{key}"
                last_key = f"zone/{i}/{key}"
                if value is not None and self._last_values.get(last_key) != value:
                    self.client.publish(topic, str(value), qos=0, retain=True)
                    self._last_values[last_key] = value

        # Aggregate zone metrics
        if zones:
            for key in ("zone_spread", "zone_max_deviation"):
                value = state_dict.get(key)
                if value is not None and self._last_values.get(key) != value:
                    self.client.publish(
                        f"{self.prefix}/{key}", str(round(value, 1)),
                        qos=0, retain=True)
                    self._last_values[key] = value

    def _publish_retained(self, subtopic, payload):
        try:
            self.client.publish(
                f"{self.prefix}/{subtopic}", payload, qos=0, retain=True
            )
        except Exception as e:
            log.error("MQTT publish %s error: %s" % (subtopic, e))

    def stop(self):
        if self.client:
            try:
                self.client.publish(
                    f"{self.prefix}/available", "offline", qos=1, retain=True
                )
                self.client.disconnect()
                self.client.loop_stop()
            except Exception:
                pass
            self.connected = False
            log.info("MQTT client stopped")
