# MQTT Integration

The kiln controller can publish real-time state to an MQTT broker for remote monitoring, home automation integration, or AI agent orchestration. It also accepts remote commands (pause, resume, stop) via MQTT.

## Setup

### Enable MQTT

Edit `config.py`:

```python
mqtt_enabled = True
mqtt_host = "localhost"
mqtt_port = 1883
mqtt_topic_prefix = "kiln"
mqtt_publish_interval = 2
mqtt_username = None
mqtt_password = None
```

### Install Dependency

```bash
pip install paho-mqtt
```

If paho-mqtt is not installed, the controller starts normally but skips MQTT with a warning.

## Configuration Reference

| Setting | Default | Description |
|---------|---------|-------------|
| `mqtt_enabled` | `False` | Master enable/disable |
| `mqtt_host` | `"localhost"` | Broker hostname or IP |
| `mqtt_port` | `1883` | Broker port |
| `mqtt_topic_prefix` | `"kiln"` | Topic namespace (customize for multiple kilns) |
| `mqtt_publish_interval` | `2` | Seconds between full state publishes |
| `mqtt_username` | `None` | Broker username (None = no auth) |
| `mqtt_password` | `None` | Broker password |

### Multiple Kilns

For multiple kilns on the same broker, give each a unique prefix:

```python
# Kiln 1
mqtt_topic_prefix = "kiln_top"

# Kiln 2
mqtt_topic_prefix = "kiln_bottom"
```

## Topic Structure

All topics are prefixed with `mqtt_topic_prefix` (default: `kiln`).

### Connection

| Topic | Retained | Payload | Description |
|-------|----------|---------|-------------|
| `kiln/available` | Yes | `"online"` or `"offline"` | Last Will Testament — automatically set to `"offline"` if connection drops |

### Full State (Non-Retained)

| Topic | Payload | Frequency |
|-------|---------|-----------|
| `kiln/status` | Full JSON blob (see below) | Every `mqtt_publish_interval` seconds |

**Status JSON payload:**

```json
{
  "temperature": 1250.5,
  "target": 2200,
  "heat": 0.85,
  "heat_rate": 125.0,
  "target_heat_rate": 150.0,
  "totaltime": 3600,
  "state": "RUNNING",
  "profile": "Cone 6 Slow",
  "emergency": "",
  "segment_index": 2,
  "phase": "ramp",
  "total_segments": 5,
  "zone_spread": 25,
  "zone_max_deviation": 60,
  "zones": [...]
}
```

### Individual Topics (Retained)

These persist on the broker so new subscribers get current state immediately.

| Topic | Payload | Change-Only |
|-------|---------|-------------|
| `kiln/temperature` | Current temperature | No (always published) |
| `kiln/target` | Target temperature | No |
| `kiln/heat` | Heat output 0.0-1.0 | No |
| `kiln/heat_rate` | Actual heating rate (deg/hr) | No |
| `kiln/target_rate` | Target heating rate (deg/hr) | No |
| `kiln/eta` | Estimated time remaining (seconds) | No |
| `kiln/state` | `"RUNNING"`, `"PAUSED"`, `"IDLE"` | Yes (only on change) |
| `kiln/profile` | Current profile name | Yes |
| `kiln/emergency` | Emergency message (empty if none) | Yes |
| `kiln/segment` | JSON: `{"index":2,"phase":"ramp","total":5}` | Yes |

### Per-Zone Topics (Multi-Zone Only)

| Topic | Payload | Description |
|-------|---------|-------------|
| `kiln/zone/{i}/name` | Zone name | Published once on connect |
| `kiln/zone/{i}/temperature` | Zone temperature | Per-zone reading |
| `kiln/zone/{i}/target` | Zone target | Per-zone target |
| `kiln/zone/{i}/heat` | Zone duty cycle 0.0-1.0 | Per-zone heat output |
| `kiln/zone_spread` | Max-min temperature spread | Hottest minus coldest zone |
| `kiln/zone_max_deviation` | Largest deviation from target | Worst-performing zone |

## Commands

Send plain text commands to `kiln/command`:

| Command | Effect | Required State |
|---------|--------|---------------|
| `stop` | Abort current firing | RUNNING or PAUSED |
| `pause` | Pause firing (heat off) | RUNNING |
| `resume` | Resume from pause | PAUSED |

Commands are case-insensitive. Leading/trailing whitespace is trimmed.

**Starting a firing via MQTT is intentionally not supported** for safety. Use the web UI to start firings.

```bash
# Examples using mosquitto_pub
mosquitto_pub -h localhost -t kiln/command -m "stop"
mosquitto_pub -h localhost -t kiln/command -m "pause"
mosquitto_pub -h localhost -t kiln/command -m "resume"
```

## Change-Only Publishing

To reduce broker traffic, these topics only publish when their value changes:

- `kiln/state`
- `kiln/profile`
- `kiln/emergency`
- `kiln/segment`

Numeric topics (temperature, heat, rate) publish on every interval since they change continuously during a firing. This optimization reduces traffic by ~70% during stable hold phases.

## Integration Examples

### Home Assistant

```yaml
mqtt:
  sensor:
    - name: "Kiln Temperature"
      state_topic: "kiln/temperature"
      unit_of_measurement: "°F"
    - name: "Kiln State"
      state_topic: "kiln/state"
    - name: "Kiln Zone Spread"
      state_topic: "kiln/zone_spread"
      unit_of_measurement: "°F"

automation:
  - alias: "Alert on high zone spread"
    trigger:
      platform: mqtt
      topic: kiln/zone_spread
    condition:
      template: "{{ trigger.payload | float > 100 }}"
    action:
      service: notify.mobile_app
      data:
        message: "Kiln zone spread is {{ trigger.payload }}° — check for element failure"
```

### Python Monitoring Script

```python
import paho.mqtt.client as mqtt
import json

def on_message(client, userdata, msg):
    if msg.topic == "kiln/status":
        state = json.loads(msg.payload.decode())
        print(f"Temp: {state['temperature']}°F, "
              f"Target: {state['target']}°F, "
              f"State: {state['state']}")

client = mqtt.Client()
client.on_message = on_message
client.connect("localhost", 1883)
client.subscribe("kiln/#")
client.loop_forever()
```

### Monitor All Topics

```bash
mosquitto_sub -h localhost -t "kiln/#" -v
```

## Connection Resilience

- **Auto-reconnect**: paho-mqtt automatically reconnects on disconnect
- **Last Will Testament**: `kiln/available` is set to `"offline"` by the broker if the controller disconnects unexpectedly
- **Retained topics**: Last-known values persist on the broker during disconnects
- **Graceful shutdown**: `kiln/available` is set to `"offline"` when the controller stops normally

## Troubleshooting

| Issue | Cause | Fix |
|-------|-------|-----|
| MQTT not publishing | `mqtt_enabled = False` | Set to `True` in config.py |
| "paho-mqtt not installed" | Missing dependency | `pip install paho-mqtt` |
| Connection refused | Broker not running | Verify broker at `mqtt_host:mqtt_port` |
| Commands ignored | Wrong kiln state | `pause` requires RUNNING, `resume` requires PAUSED |
| Topics empty after restart | Broker lost retained messages | Wait for next publish cycle |
