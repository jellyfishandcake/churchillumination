"""Seeed XIAO ESP32S3 Sense node listener — aggregates readings published
over MQTT by the 2 ESP32S3 nodes distributed around the installation
space. These are separate microcontroller boards (their own WiFi + mic +
MLX90640 thermal camera + PIR), not wired to the Pi directly, so
ingestion happens over the network instead of a hardware library import.

MQTT contract, for the firmware side (written separately once the boards
arrive — different toolchain, not part of this Python codebase):
  topic:   esp32/<node_id>/sense
  payload: JSON, e.g. {"loudness": 0.4, "motion": 0.1, "presence": 0.0}

"motion" here is each node's own thermal-camera frame-diff (same idea as
the central MotionSensor, computed on the node's firmware since the Pi
never sees the node's raw thermal frames). "presence" is that node's own
PIR. Both get folded into the top-level activation decision in main.py's
sensor_loop alongside the central pir.py reading, so presence at any of
the 3 locations (central + 2 nodes) keeps the installation "activated".

Unlike the other sensors, "hardware absent" here doesn't show up as an
ImportError — paho-mqtt always imports fine on any platform — it shows up
as "no broker reachable" or "no node has published yet". Both cases fall
back to mock per-node readings so main.py never stalls waiting on
unflashed boards.
"""
import json
import random
import time

from .base import Sensor

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

TOPIC_PREFIX = "esp32"
STALE_AFTER_SECONDS = 10.0


class NodeSensor(Sensor):
    def __init__(self, node_ids, mqtt_host: str = "localhost", mqtt_port: int = 1883):
        super().__init__()
        self.node_ids = list(node_ids)
        self._latest_by_node = {}
        self._client = None

        if mqtt is not None:
            try:
                client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
                client.on_message = self._on_message
                client.connect_async(mqtt_host, mqtt_port)
                client.subscribe(f"{TOPIC_PREFIX}/+/sense")
                client.loop_start()  # non-blocking: connects in a background thread
                self._client = client
            except Exception:
                self._client = None  # no broker reachable right now

    def _on_message(self, client, userdata, msg):
        try:
            node_id = msg.topic.split("/")[1]
            payload = json.loads(msg.payload.decode())
        except (IndexError, ValueError):
            return  # malformed topic or payload — ignore
        payload["_received_at"] = time.time()
        self._latest_by_node[node_id] = payload

    def read(self) -> dict:
        now = time.time()
        nodes = {}
        for node_id in self.node_ids:
            reading = self._latest_by_node.get(node_id)
            if reading is not None and now - reading["_received_at"] < STALE_AFTER_SECONDS:
                nodes[node_id] = {k: v for k, v in reading.items() if k != "_received_at"}
            else:
                nodes[node_id] = _mock_node_reading()
        return {"nodes": nodes}


def _mock_node_reading() -> dict:
    return {
        "loudness": random.uniform(0.0, 0.2),
        "motion": random.uniform(0.0, 0.1),
        "presence": 1.0 if random.random() < 0.05 else 0.0,
    }
