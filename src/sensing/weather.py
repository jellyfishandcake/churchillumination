"""Outdoor weather via Open-Meteo (open-meteo.com) - free, no API key or
account needed, matching every other sensor here staying zero-config out
of the box.

Fetches on a slow background thread rather than every 20Hz tick like the
rest of sensor_loop - weather doesn't change fast enough to justify more,
and hammering a free public API at 20Hz would be both wasteful and rude.
read() just returns whatever the last successful fetch cached - same
"background thread populates, read() returns instantly, never blocks the
sensor loop on network I/O" pattern as nodes.py's MQTT listener.

Current conditions only (temperature/humidity/condition) - an earlier
version also pulled a rolling past-24h hourly series for a "replay" LED
mode that's since been dropped (see led_effects.py's TempHumidityBarEffect,
rebuilt 2026-08-18); no consumer needs the history anymore, so the request
stays a plain `current`-only call rather than fetching data nothing reads.
"""
import random
import threading
import time

import requests

from .base import Sensor

FETCH_INTERVAL_SECONDS = 600.0  # 10 minutes - weather doesn't change fast enough to justify more
REQUEST_TIMEOUT_SECONDS = 10.0
STALE_AFTER_SECONDS = FETCH_INTERVAL_SECONDS * 3  # tolerate a couple of missed fetches before falling back to mock

# Open-Meteo's weather_code (WMO code) collapsed to a coarse bucket - the
# effect layer only needs "is it raining/clear/cloudy", not the full WMO
# table. See https://open-meteo.com/en/docs for the full code list. Order
# matters here (not just naming): TempHumidityBarEffect's `condition` source
# arrives as this list's index rescaled to 0..1 (see _condition_code below
# and config.yaml's weather zone source) - reordering this list
# without updating led_effects.py's matching CONDITION_ORDER would silently
# relabel every effect's condition-colour/character branch.
_CONDITION_BUCKETS = [
    (range(0, 1), "clear"),
    (range(1, 4), "cloudy"),
    (range(45, 49), "fog"),
    (range(51, 68), "rain"),
    (range(71, 78), "snow"),
    (range(80, 100), "storm"),
]
_CONDITION_ORDER = [name for _range, name in _CONDITION_BUCKETS]
_UNKNOWN_CONDITION_INDEX = _CONDITION_ORDER.index("cloudy")  # visually-neutral fallback for an unrecognised WMO code


def _bucket_condition(code: int) -> str:
    for code_range, name in _CONDITION_BUCKETS:
        if code in code_range:
            return name
    return "unknown"


def _condition_code(name: str) -> int:
    try:
        return _CONDITION_ORDER.index(name)
    except ValueError:
        return _UNKNOWN_CONDITION_INDEX


class WeatherSensor(Sensor):
    def __init__(self, latitude: float, longitude: float):
        super().__init__()
        self.latitude = latitude
        self.longitude = longitude
        self._latest = None
        self._latest_at = 0.0
        self._stop = threading.Event()

        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def _fetch_once(self) -> dict:
        response = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": self.latitude,
                "longitude": self.longitude,
                "current": "temperature_2m,relative_humidity_2m,weather_code",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        current = response.json()["current"]

        condition_name = _bucket_condition(int(current["weather_code"]))
        return {
            "outdoor_temperature": float(current["temperature_2m"]),
            "outdoor_humidity": float(current["relative_humidity_2m"]),
            "outdoor_condition": condition_name,
            "outdoor_condition_code": _condition_code(condition_name),
        }

    def _poll_loop(self) -> None:
        """Runs for the sensor's whole lifetime on its own daemon thread -
        fetches immediately on startup, then every FETCH_INTERVAL_SECONDS.
        Health tracking happens here rather than in read() (unlike most
        sensors) since read() itself never does any I/O to fail - what
        actually can fail is this background fetch, so that's what
        healthy/last_error should reflect."""
        while not self._stop.is_set():
            try:
                self._latest = self._fetch_once()
                self._latest_at = time.monotonic()
                self._mark_ok()
            except Exception as exc:
                self._mark_failed(exc)
            self._stop.wait(FETCH_INTERVAL_SECONDS)

    def read(self) -> dict:
        if self._latest is None or time.monotonic() - self._latest_at > STALE_AFTER_SECONDS:
            return _mock_reading()
        return dict(self._latest)


def _mock_reading() -> dict:
    condition_name = random.choice(["clear", "cloudy", "rain"])
    return {
        "outdoor_temperature": random.uniform(5.0, 25.0),
        "outdoor_humidity": random.uniform(30.0, 90.0),
        "outdoor_condition": condition_name,
        "outdoor_condition_code": _condition_code(condition_name),
    }
