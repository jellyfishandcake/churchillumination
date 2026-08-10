"""Outdoor weather via Open-Meteo (open-meteo.com) - free, no API key or
account needed, matching every other sensor here staying zero-config out
of the box.

Fetches on a slow background thread rather than every 20Hz tick like the
rest of sensor_loop - weather doesn't change fast enough to justify more,
and hammering a free public API at 20Hz would be both wasteful and rude.
read() just returns whatever the last successful fetch cached - same
"background thread populates, read() returns instantly, never blocks the
sensor loop on network I/O" pattern as nodes.py's MQTT listener.
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
# and config.yaml's temp_humidity zone source) - reordering this list
# without updating led_effects.py's matching CONDITION_ORDER would silently
# relabel every effect's condition-texture branch.
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

# Rolling past-24h window for the replay effect - see main.py's
# TempHumidityBarEffect / config.yaml's temp_humidity zone `history` source.
# forecast_days=1 keeps the response small (we only use the past_days=1
# portion) while still giving Open-Meteo's API a valid "hourly" request.
HISTORY_HOURS = 24


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
                # Same request also pulls the past-24h hourly series for the
                # replay effect - one extra param set, not a second HTTP
                # round-trip. forecast_days=1 (Open-Meteo's minimum) keeps
                # the payload small; only the past_days=1 portion is used.
                "hourly": "temperature_2m,relative_humidity_2m,weather_code",
                "past_days": 1,
                "forecast_days": 1,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        current = payload["current"]
        hourly = payload["hourly"]

        # hourly.time is chronological; slice the HISTORY_HOURS entries
        # ending at "now"'s slot for a rolling past-24h window (rather than
        # a calendar-day slice, which would go stale/short near midnight).
        try:
            now_idx = hourly["time"].index(current["time"])
        except ValueError:
            now_idx = len(hourly["time"]) - 1  # fall back to the latest hour Open-Meteo actually returned
        start_idx = max(0, now_idx - (HISTORY_HOURS - 1))
        history = [
            {
                "temperature": float(hourly["temperature_2m"][i]),
                "humidity": float(hourly["relative_humidity_2m"][i]),
                "condition_code": _condition_code(_bucket_condition(int(hourly["weather_code"][i]))),
            }
            for i in range(start_idx, now_idx + 1)
        ]

        condition_name = _bucket_condition(int(current["weather_code"]))
        return {
            "outdoor_temperature": float(current["temperature_2m"]),
            "outdoor_humidity": float(current["relative_humidity_2m"]),
            "outdoor_condition": condition_name,
            "outdoor_condition_code": _condition_code(condition_name),
            "outdoor_history": history,  # oldest -> newest, see TempHumidityBarEffect
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
        # Fabricated but shaped like a real HISTORY_HOURS reading, so
        # TempHumidityBarEffect's replay has something to animate on a dev
        # laptop / while the real fetch is unreachable, same "safe on a dev
        # laptop" contract every sensor here follows.
        "outdoor_history": [
            {
                "temperature": random.uniform(5.0, 25.0),
                "humidity": random.uniform(30.0, 90.0),
                "condition_code": _condition_code(random.choice(["clear", "cloudy", "rain"])),
            }
            for _ in range(HISTORY_HOURS)
        ],
    }
