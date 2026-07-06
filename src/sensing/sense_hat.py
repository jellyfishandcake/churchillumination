"""Raspberry Pi Sense HAT v2 — accelerometer/gyro/magnetometer plus
temperature/humidity/pressure, all on one I2C board. Covers both the
"accel" and "weather" inputs from CLAUDE.md's input list in one sensor.

Falls back to a mock automatically if the sense_hat library isn't
installed, or if the board isn't attached (SenseHat() raises on init when
there's no IMU to talk to). Note: `pip install sense-hat` can be unreliable
(it depends on RTIMULib) — on the Pi, `sudo apt install sense-hat` is the
documented fallback.
"""
import math
import random

from .base import Sensor

try:
    from sense_hat import SenseHat
except ImportError:
    SenseHat = None


class SenseHatSensor(Sensor):
    def __init__(self):
        super().__init__()
        self._hat = None
        if SenseHat is not None:
            try:
                self._hat = SenseHat()
            except Exception:
                self._hat = None  # board not attached, or IMU init failed

    def read(self) -> dict:
        if self._hat is None:
            return _mock_reading()

        try:
            accel = self._hat.get_accelerometer_raw()
            magnitude = math.sqrt(accel["x"] ** 2 + accel["y"] ** 2 + accel["z"] ** 2)
            # At rest, one axis reads ~1g from gravity alone — deviation from
            # that baseline is what we care about as "activity".
            acceleration = min(1.0, abs(magnitude - 1.0))
            reading = {
                "acceleration": acceleration,
                "temperature": self._hat.get_temperature(),
                "humidity": self._hat.get_humidity(),
                "pressure": self._hat.get_pressure(),
            }
        except Exception as exc:
            # Board was working, but if a live read failed — fallback on mock values
            self._mark_failed(exc)
            return _mock_reading()

        self._mark_ok()
        return reading


def _mock_reading() -> dict:
    return {
        "acceleration": random.uniform(0.0, 0.05),
        "temperature": random.uniform(19.0, 23.0),
        "humidity": random.uniform(35.0, 55.0),
        "pressure": random.uniform(1000.0, 1020.0),
    }
# check these mock values in sensor calibration!