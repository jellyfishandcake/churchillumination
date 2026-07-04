"""Mini PIR motion sensor (e.g. AM312-style), wired to a GPIO pin. Uses
gpiozero's MotionSensor, which reads high when motion is detected. Falls
back to a mock when gpiozero (or the board's GPIO chip) isn't available —
e.g. on a dev laptop with no GPIO header.

Reading key is "presence", not "motion" — motion.py's MotionSensor already
owns "motion" for camera-based frame-diff, and main.py merges every
sensor's dict together with `readings.update(...)`, so distinct sensors
must use distinct keys.

On a Raspberry Pi 5, gpiozero's default pin factory (RPi.GPIO) doesn't
support the Pi 5's GPIO chip — install `lgpio` too (see
requirements-pi.txt).
"""
import random

from .base import Sensor

try:
    from gpiozero import MotionSensor as GPIOMotionSensor
except ImportError:
    GPIOMotionSensor = None


class PIRSensor(Sensor):
    def __init__(self, gpio_pin: int = 4):
        super().__init__()
        self._pir = None
        if GPIOMotionSensor is not None:
            try:
                self._pir = GPIOMotionSensor(gpio_pin)
            except Exception:
                self._pir = None  # no PIR wired up, or wrong pin factory

    def read(self) -> dict:
        if self._pir is None:
            return {"presence": 1.0 if random.random() < 0.05 else 0.0}

        try:
            presence = 1.0 if self._pir.motion_detected else 0.0
        except Exception as exc:
            self._mark_failed(exc)
            return {"presence": 1.0 if random.random() < 0.05 else 0.0}

        self._mark_ok()
        return {"presence": presence}
