"""Pimoroni Multi-Sensor Stick (BME280 + LTR559 + LSM6DS3, PIM745), wired
over I2C on a longer cable so it can sit away from the Pi and read true
room conditions instead of the Pi's own heat/vibration - replaces the old
Sense HAT, which sat right next to the Pi.

Only the BME280 (temperature/humidity/pressure) and LTR559 (light/
proximity) are read here. The board's onboard LSM6DS3 accelerometer is
deliberately NOT used - movement/interaction detection comes from a
separate handheld accelerometer stick instead (see accel_stick.py), and
reading two "acceleration" sources would be ambiguous about which one is
authoritative.

Uses Pimoroni's official pip-installable, MIT-licensed drivers:
  pip install pimoroni-bme280 ltr559
(see requirements-pi.txt). Falls back to a mock if either library or the
board isn't present, same pattern as every other sensor here.
"""
import random

from .base import Sensor

try:
    import smbus2
except ImportError:
    smbus2 = None

try:
    from bme280 import BME280
except ImportError:
    BME280 = None

try:
    from ltr559 import LTR559
except ImportError:
    LTR559 = None


class MultisensorStick(Sensor):
    def __init__(self, i2c_bus: int = 1):
        super().__init__()
        self._bme280 = None
        self._ltr559 = None

        bus = None
        if smbus2 is not None and (BME280 is not None or LTR559 is not None):
            try:
                bus = smbus2.SMBus(i2c_bus)
            except Exception:
                bus = None  # no I2C bus available on this machine

        if bus is not None and BME280 is not None:
            try:
                self._bme280 = BME280(i2c_dev=bus)
            except Exception:
                self._bme280 = None  # no BME280 on this bus

        if bus is not None and LTR559 is not None:
            try:
                self._ltr559 = LTR559(i2c_dev=bus)
            except Exception:
                self._ltr559 = None  # no LTR559 on this bus

    def read(self) -> dict:
        if self._bme280 is None and self._ltr559 is None:
            return _mock_reading()

        try:
            reading = {}
            if self._bme280 is not None:
                reading["temperature"] = self._bme280.get_temperature()
                reading["humidity"] = self._bme280.get_humidity()
                reading["pressure"] = self._bme280.get_pressure()
            if self._ltr559 is not None:
                self._ltr559.update_sensor()
                reading["lux"] = self._ltr559.get_lux()
                reading["proximity"] = self._ltr559.get_proximity()
        except Exception as exc:
            self._mark_failed(exc)
            return _mock_reading()

        self._mark_ok()
        return reading


def _mock_reading() -> dict:
    return {
        "temperature": random.uniform(19.0, 23.0),
        "humidity": random.uniform(35.0, 55.0),
        "pressure": random.uniform(1000.0, 1020.0),
        "lux": random.uniform(50.0, 400.0),
        "proximity": 0.0,
    }
