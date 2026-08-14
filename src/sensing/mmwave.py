"""Waveshare HMMD mmWave presence sensor (S3KM1110-based, 24GHz FMCW radar) -
an alternative central presence source to pir.py's PIRSensor, feeding the
same "presence" key into main.py's sensor_loop (central_presence ->
ActivationTracker -> state.activated). Being considered as a PIR
replacement (2026-08-14) since PIR only fires on movement - someone
standing/sitting still can "disappear" from presence and let `activated`
time out mid-interaction, which matters more now that the weather zone's
replay mode depends on sustained presence. This sensor's onboard FMCW radar
detects micro-motion (breathing, small shifts) so a stationary person keeps
registering, unlike PIR.

NOT meant to run alongside pir.py's PIRSensor at the same time - both
publish the same "presence" key, and main.py's sensor_loop just does
`raw.update(s.read())` per sensor in dict order, so whichever one's read()
happens to run later would silently overwrite the other's value every tick.
Enable one or the other in config.yaml's sensors block, not both.

Uses the sensor's simple digital presence-output GPIO pin (active-high per
github.com/2Grey/s3km1110's Arduino library README - "an 'active high'
output on the 'OUT' pin which can be used to signal presence"), not its
UART interface - same "just read a GPIO pin" simplicity as PIRSensor, and
the sensor's own onboard radar processing already does the human-vs-not
decision before it ever reaches this pin. It also exposes a richer UART
protocol at 115200 baud (per pishop.us's listing: "Supports UART port and
GPIO header output") that could give distance/confidence data instead of
just a boolean, but the exact frame format (header bytes, checksum, field
layout) wasn't available from public docs when this was written -
Waveshare's own wiki blocks automated fetches, and the third-party Arduino
library referenced above explicitly hasn't implemented that parsing either.
Worth revisiting with the physical unit's printed manual if richer data is
wanted later; the GPIO pin alone already delivers this sensor's main
advantage over PIR.

Wiring (confirm against the physical board's own silkscreen labels - the
exact pin ORDER wasn't independently verifiable from public docs, only
that these pins exist): VCC -> Pi 3.3V (NOT 5V - this is a 3.3V-only
module, per its 3.0-3.6V supply spec), GND -> Pi GND, presence-output pin
(labelled OUT/GPIO/MOT depending on silkscreen revision) -> gpio_pin below.
TX/RX are for the UART interface this driver doesn't use - leave
unconnected.

Same gpiozero Pi 5 lgpio quirk as pir.py - see that file's own docstring
for the github.com/gpiozero/gpiozero/issues/1166 background; duplicated
here rather than shared since it's only a few lines and this sensor may
outlive pir.py's use in this project (or vice versa) - not worth a shared
helper for something this small.
"""

import random

from .base import Sensor

try:
    from gpiozero import Device, DigitalInputDevice
except ImportError:
    DigitalInputDevice = None
    Device = None

try:
    from gpiozero.pins.lgpio import LGPIOFactory
except ImportError:
    LGPIOFactory = None


class MMWaveSensor(Sensor):
    def __init__(self, gpio_pin: int = 27):
        super().__init__()
        self._sensor = None
        if DigitalInputDevice is not None:
            try:
                self._sensor = DigitalInputDevice(gpio_pin)
            except Exception:
                # See pir.py's docstring - same gpiozero-on-Pi-5 gpiochip
                # mismatch, confirmed against real Pi 5 hardware for
                # MotionSensor; applies equally to any gpiozero device class
                # since the bug is in gpiozero's pin factory auto-detection,
                # not anything PIR-specific.
                try:
                    if LGPIOFactory is not None:
                        Device.pin_factory = LGPIOFactory(chip=0)
                        self._sensor = DigitalInputDevice(gpio_pin)
                except Exception:
                    self._sensor = None  # not wired up, or wrong pin factory

    def read(self) -> dict:
        if self._sensor is None:
            return {"presence": 1.0 if random.random() < 0.05 else 0.0}

        try:
            presence = 1.0 if self._sensor.value else 0.0
        except Exception as exc:
            self._mark_failed(exc)
            return {"presence": 1.0 if random.random() < 0.05 else 0.0}

        self._mark_ok()
        return {"presence": presence}
