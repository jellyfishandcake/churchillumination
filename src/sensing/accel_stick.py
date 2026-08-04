"""Handheld M5Stack ESP32S3 accelerometer stick - visitors pick it up and
shake it. Wired to the Pi over USB-serial (pyserial) rather than WiFi/MQTT:
a physical tether is both simpler (no broker/WiFi provisioning) and keeps
the stick from walking off, since it can only go as far as its cable.

The M5Stack's own firmware (separate toolchain, written once the board
arrives - same "not part of this Python codebase" situation as nodes.py's
ESP32S3 presence-node firmware) does its own raw-IMU-to-normalised-
magnitude conversion on-device and prints one newline-delimited JSON object
per line, e.g.:
  {"acceleration": 0.3, "angle_deg": 210.0}\n

The Pi side just reads and clamps those numbers - it doesn't need the raw
axes, since the firmware already did the "deviation from 1g" and atan2 math
itself. "acceleration" is [0, 1] (shake magnitude); "angle_deg" is [0, 360)
(which way it's currently leaning/swinging in the horizontal plane - see
accel_stick.ino's own docstring for why this is a snapshot of lean, not a
true integrated trajectory). Feeds config.py's accelerometer zone
(TriArmGlideEffect), which maps this angle onto whichever of its three
120-degree-apart arms the swing is closest to.
"angle_deg" defaults to 0.0 if a line omits it - keeps this Python side
working unmodified against an accel_stick that hasn't been reflashed with
the angle-reporting firmware yet.
"""
import json
import random
import time

from .base import Sensor

try:
    import serial
except ImportError:
    serial = None

STALE_AFTER_SECONDS = 10.0


class AccelStickSensor(Sensor):
    def __init__(self, serial_port: str = "/dev/ttyUSB0", baud_rate: int = 115200):
        super().__init__()
        self._serial = None
        self._latest_acceleration = 0.0
        self._latest_angle_deg = 0.0
        self._latest_at = 0.0

        if serial is not None:
            try:
                self._serial = serial.Serial(serial_port, baud_rate, timeout=0)
            except Exception:
                self._serial = None  # stick not plugged in, or wrong port

    def _poll_serial(self) -> None:
        """Drain any buffered lines, keeping only the most recent reading -
        this is a live sensor value, not a queue of history to replay."""
        try:
            while self._serial.in_waiting:
                line = self._serial.readline()
                try:
                    payload = json.loads(line.decode().strip())
                    acceleration = float(payload["acceleration"])
                    angle_deg = float(payload.get("angle_deg", 0.0))
                except (ValueError, KeyError, UnicodeDecodeError):
                    continue  # malformed line - ignore, keep the last good reading
                self._latest_acceleration = min(1.0, max(0.0, acceleration))
                self._latest_angle_deg = angle_deg % 360.0  # circular, not clamped - wraps rather than pinning at an edge
                self._latest_at = time.monotonic()
        except Exception as exc:
            self._mark_failed(exc)
            raise

    def read(self) -> dict:
        if self._serial is None:
            return _mock_reading()

        try:
            self._poll_serial()
        except Exception:
            return _mock_reading()

        if time.monotonic() - self._latest_at > STALE_AFTER_SECONDS:
            return {"acceleration": 0.0, "angle_deg": 0.0}  # stick connected but quiet - not stale-mocked, genuinely idle

        self._mark_ok()
        return {"acceleration": self._latest_acceleration, "angle_deg": self._latest_angle_deg}


def _mock_reading() -> dict:
    """Small idle jitter, punctuated by occasional simulated "shake" bursts,
    so the mock exercises the motion-burst tracker the same way a real
    handheld stick being picked up and shaken would. angle_deg is only
    ever non-zero during a simulated burst - a real idle stick isn't
    leaning any particular way, so there's nothing to fake between bursts."""
    if random.random() < 0.02:
        return {"acceleration": random.uniform(0.3, 1.0), "angle_deg": random.uniform(0.0, 360.0)}
    return {"acceleration": random.uniform(0.0, 0.05), "angle_deg": 0.0}
