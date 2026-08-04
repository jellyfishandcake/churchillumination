"""Mini PIR motion sensor (e.g. AM312-style), wired to a GPIO pin. Uses
gpiozero's MotionSensor, which reads high when motion is detected. Falls
back to a mock when gpiozero (or the board's GPIO chip) isn't available —
e.g. on a dev laptop with no GPIO header.

This class is the *central* PIR only, wired directly to the Pi's own GPIO.
The 2 node-mounted PIRs (one per XIAO ESP32S3 node) are physically on
those boards, not the Pi, so they report over MQTT instead - see
nodes.py's per-node "presence" field - not through this class. All three
PIRs' presence readings get OR'd together in main.py's sensor_loop before
reaching ActivationTracker, so presence at any of the three locations
keeps the installation "activated".

Reading key is "presence", not "motion" — motion.py's MotionSensor already
owns "motion" for thermal-camera-based frame-diff, and main.py merges
every sensor's dict together with `readings.update(...)`, so distinct
sensors must use distinct keys.

On a Raspberry Pi 5, gpiozero's default pin factory (RPi.GPIO) doesn't
support the Pi 5's GPIO chip — install `lgpio` too (see
requirements-pi.txt). Even with lgpio installed, gpiozero 2.0.1 targets the
wrong gpiochip number on current kernels (see the retry-with-LGPIOFactory
logic below) - a real bug hit and fixed against actual Pi 5 hardware
2026-08-04, not a hypothetical.
"""

import random

from .base import Sensor

try:
    from gpiozero import Device, MotionSensor as GPIOMotionSensor
except ImportError:
    GPIOMotionSensor = None
    Device = None

try:
    from gpiozero.pins.lgpio import LGPIOFactory
except ImportError:
    LGPIOFactory = None


class PIRSensor(Sensor):
    def __init__(self, gpio_pin: int = 4):
        super().__init__()
        self._pir = None
        if GPIOMotionSensor is not None:
            try:
                self._pir = GPIOMotionSensor(gpio_pin)
            except Exception:
                # gpiozero 2.0.1 hardcodes gpiochip 4 for its Pi 5 lgpio pin
                # factory (github.com/gpiozero/gpiozero/issues/1166), but
                # current Raspberry Pi kernels (post-mid-2024) assign the
                # 40-pin header's GPIOs to gpiochip 0 instead - the mismatch
                # makes the construction above fail with "can not open
                # gpiochip" even though the device/permissions are
                # completely fine (confirmed 2026-08-04 on real hardware:
                # `lgpio.gpiochip_open(0)` succeeds directly - only
                # gpiozero's own auto-detected chip number was wrong).
                # Retry once, forcing chip 0 explicitly, before giving up -
                # harmless on hardware where the first attempt failed for a
                # real reason (e.g. Pi 4, or no PIR wired up at all), since
                # this just fails again too in that case.
                try:
                    if LGPIOFactory is not None:
                        Device.pin_factory = LGPIOFactory(chip=0)
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
