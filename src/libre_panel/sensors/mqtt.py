"""MQTT: values your devices publish, as readings (needs ``libre-panel[mqtt]``).

``[sensors.mqtt]``::

    host = "192.168.1.10"
    port = 1883
    username = ""                # if the broker wants one
    password = ""
    topics = ["home/+/temperature", "zigbee2mqtt/#"]
    units = { "home/+/temperature" = "°C" }   # optional, by topic pattern

A message on ``home/kitchen/temperature`` becomes ``mqtt.home.kitchen.temperature``.
A JSON object is taken apart: ``{"temperature": 21.5, "humidity": 40}`` on
``zigbee2mqtt/sensor`` gives ``mqtt.zigbee2mqtt.sensor.temperature`` and
``...humidity``.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from libre_panel.sensors.base import Reading, SensorProvider

log = logging.getLogger(__name__)

MAX_KEYS = 2000


def topic_matches(pattern: str, topic: str) -> bool:
    """MQTT wildcards: ``+`` one level, ``#`` the rest."""
    want, have = pattern.split("/"), topic.split("/")
    for i, part in enumerate(want):
        if part == "#":
            return True
        if i >= len(have) or (part != "+" and part != have[i]):
            return False
    return len(want) == len(have)


def _key(topic: str) -> str:
    return "mqtt." + ".".join(part.replace(".", "_").replace(" ", "_") for part in topic.split("/"))


def _scalar(value: Any) -> float | str | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        try:
            return float(text)
        except ValueError:
            return text[:200]
    return None


def readings_from_message(
    topic: str, payload: bytes, units: dict[str, str] | None = None
) -> dict[str, Reading]:
    unit = next((u for p, u in (units or {}).items() if topic_matches(p, topic)), "")
    text = payload.decode("utf-8", "replace").strip()
    key = _key(topic)
    try:
        data = json.loads(text)
    except ValueError:
        data = text
    out: dict[str, Reading] = {}
    if isinstance(data, dict):
        for name, value in data.items():
            value = _scalar(value)
            if value is not None:
                sub = f"{key}.{str(name).replace(' ', '_')}"
                out[sub] = Reading(sub, value, unit, f"{topic} {name}")
    else:
        value = _scalar(data)
        if value is not None:
            out[key] = Reading(key, value, unit, topic)
    return out


class MqttProvider(SensorProvider):
    name = "mqtt"

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.topics = [str(t) for t in self.options.get("topics", [])]
        self.units = {str(k): str(v) for k, v in (self.options.get("units") or {}).items()}
        self._latest: dict[str, Reading] = {}
        self._lock = threading.Lock()
        self._client: Any = None
        if not self.topics:
            log.warning("MQTT: no topics in [sensors.mqtt]")
            return
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            log.warning('MQTT needs paho-mqtt: pip install "libre-panel[mqtt]"')
            return
        version = getattr(mqtt, "CallbackAPIVersion", None)
        client = mqtt.Client(version.VERSION2) if version else mqtt.Client()
        if self.options.get("username"):
            client.username_pw_set(
                str(self.options["username"]), str(self.options.get("password", ""))
            )
        if self.options.get("tls"):
            client.tls_set()
        client.on_connect = self._connected
        client.on_message = self._message
        client.reconnect_delay_set(1, 60)
        host = str(self.options.get("host", "localhost"))
        client.connect_async(host, int(self.options.get("port", 1883)), keepalive=60)
        client.loop_start()
        self._client = client

    def _connected(
        self, client: Any, _userdata: Any, _flags: Any, reason: Any, *_rest: Any
    ) -> None:
        if getattr(reason, "is_failure", False) or (isinstance(reason, int) and reason != 0):
            log.warning("MQTT: the broker refused the connection (%s)", reason)
            return
        for topic in self.topics:
            client.subscribe(topic)

    def _message(self, _client: Any, _userdata: Any, message: Any) -> None:
        found = readings_from_message(message.topic, message.payload, self.units)
        with self._lock:
            if len(self._latest) < MAX_KEYS or all(k in self._latest for k in found):
                self._latest.update(found)

    def read(self) -> dict[str, Reading]:
        with self._lock:
            return dict(self._latest)

    def close(self) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
