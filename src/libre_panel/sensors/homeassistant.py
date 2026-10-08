"""Home Assistant: the states of your entities as readings.

``[sensors.homeassistant]`` names the server and a long-lived access token
(Home Assistant: your profile -> Security -> Long-lived access tokens)::

    url = "http://homeassistant.local:8123"
    token = "eyJ..."
    entities = ["sensor.*", "binary_sensor.front_door"]   # which ones (patterns)

Each entity becomes ``ha.<entity id>`` (``ha.sensor.living_room_temperature``)
with its unit and friendly name; numbers become numbers. Numeric attributes of
other entities (a thermostat's ``current_temperature``) come as
``ha.<entity id>.<attribute>``.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from libre_panel import __version__
from libre_panel.sensors.base import Reading, SensorProvider

log = logging.getLogger(__name__)

_MISSING = {"unavailable", "unknown", "none", ""}
_SKIP_ATTRIBUTES = {"friendly_name", "unit_of_measurement", "icon", "supported_features",
                    "attribution", "entity_picture", "device_class", "state_class"}  # fmt: skip


def _value(raw: Any) -> float | str | None:
    if raw is None or isinstance(raw, bool):
        return None if raw is None else (1.0 if raw else 0.0)
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if text.lower() in _MISSING:
        return None
    try:
        return float(text)
    except ValueError:
        return text


def readings_from_states(states: list[dict[str, Any]], patterns: list[str]) -> dict[str, Reading]:
    out: dict[str, Reading] = {}
    for state in states:
        entity = str(state.get("entity_id", ""))
        if not entity or not any(fnmatch.fnmatchcase(entity, p) for p in patterns):
            continue
        attributes = state.get("attributes") or {}
        name = str(attributes.get("friendly_name") or entity)
        unit = str(attributes.get("unit_of_measurement") or "")
        key = f"ha.{entity}"
        out[key] = Reading(key, _value(state.get("state")), unit, name)
        if entity.startswith(("sensor.", "binary_sensor.")):
            continue
        for attribute, raw in attributes.items():
            if attribute in _SKIP_ATTRIBUTES or isinstance(raw, (bool, str, list, dict)):
                continue
            if isinstance(raw, (int, float)):
                sub = f"{key}.{attribute}"
                out[sub] = Reading(sub, float(raw), "", f"{name} {attribute.replace('_', ' ')}")
    return out


class HomeAssistantProvider(SensorProvider):
    name = "homeassistant"

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.url = str(self.options.get("url", "http://homeassistant.local:8123")).rstrip("/")
        self.token = str(self.options.get("token", ""))
        entities = self.options.get("entities", ["sensor.*", "binary_sensor.*"])
        self.patterns = [entities] if isinstance(entities, str) else [str(e) for e in entities]
        self.poll_seconds = max(2.0, float(self.options.get("poll_seconds", 10)))
        self._latest: dict[str, Reading] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll, name="sensors-ha", daemon=True)
        if self.token:
            self._thread.start()
        else:
            log.warning("Home Assistant: no token in [sensors.homeassistant]")

    def _fetch(self) -> list[dict[str, Any]]:
        request = urllib.request.Request(
            f"{self.url}/api/states",
            headers={"Authorization": f"Bearer {self.token}",
                     "User-Agent": f"libre-panel/{__version__}"},
        )  # fmt: skip
        with urllib.request.urlopen(request, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data if isinstance(data, list) else []

    def _poll(self) -> None:
        warned = False
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                readings = readings_from_states(self._fetch(), self.patterns)
                with self._lock:
                    self._latest = readings
                warned = False
            except Exception as exc:  # noqa: BLE001 - the server may be away for a while
                if not warned:
                    hint = " (is the token right?)" if "401" in str(exc) else ""
                    log.warning("Home Assistant not reachable at %s: %s%s", self.url, exc, hint)
                    warned = True
            self._stop.wait(max(0.5, self.poll_seconds - (time.monotonic() - started)))

    def read(self) -> dict[str, Reading]:
        with self._lock:
            return dict(self._latest)

    def close(self) -> None:
        self._stop.set()
