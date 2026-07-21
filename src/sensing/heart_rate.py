"""DFRobot Gravity/Fermion MAX30102 Heart Rate and Oximeter Sensor
(SEN0344/SEN0518) over I2C, at I2C address 0x57 - DFRobot_BloodOxygen_S_I2C's
actual default. (0x20/0x0020 only appear in that same driver's *UART/Modbus*
mode, as the Modbus slave address - unrelated to the I2C address, despite
the similar-looking name.)

This is *not* a bare MAX30102 - the module has its own onboard MCU that
runs the HR/SpO2 algorithm itself and exposes finished values over I2C.
No peak-detection/calibration code needed on our side: the vendor's
firmware already reports -1 for heartbeat/SpO2 whenever it doesn't have a
valid finger lock, which is exactly the idle/contact signal this module
needs - "is someone touching it right now", not a precise reading.

The I2C register protocol below (start-collection write at register 0x20,
get_heartbeat_SPO2 read at register 0x0C) is adapted from DFRobot's own
MIT-licensed driver:
  https://github.com/DFRobot/DFRobot_BloodOxygen_S
  Copyright (c) 2010 DFRobot Co.Ltd, MIT License
Reimplemented against smbus2 (already a dependency) rather than vendoring
their file directly, since that file unconditionally imports RPi.GPIO and
pyserial for a UART mode this project doesn't use - which would break the
"falls back to mock cleanly on a dev laptop" contract every other sensor in
this codebase follows. Presence is confirmed with a bare I2C probe
(write_quick) rather than a register-based device-ID check, matching the
official driver's own I2C begin() - the register-0x04 ID check only exists
in that driver's UART/Modbus mode, not its I2C mode.
"""
import random
import time

from .base import Sensor

try:
    import smbus2
except ImportError:
    smbus2 = None

DEVICE_ADDRESS = 0x57
REG_COLLECT_CONTROL = 0x20
START_COLLECT = [0x00, 0x01]
REG_HEARTBEAT_SPO2 = 0x0C


class HeartRateSensor(Sensor):
    def __init__(self, i2c_bus: int = 1):
        super().__init__()
        self._bus = None
        self._mock_bpm = 70.0
        self._mock_in_contact = False
        self._mock_contact_until = 0.0
        self._mock_next_contact_at = time.monotonic() + random.uniform(5.0, 20.0)

        if smbus2 is not None:
            try:
                bus = smbus2.SMBus(i2c_bus)
                bus.write_quick(DEVICE_ADDRESS)  # presence probe - device must ACK its address
                bus.write_i2c_block_data(DEVICE_ADDRESS, REG_COLLECT_CONTROL, START_COLLECT)
                self._bus = bus
            except Exception:
                self._bus = None  # no DFRobot MAX30102 module on this bus

    def read(self) -> dict:
        if self._bus is not None:
            try:
                rbuf = self._bus.read_i2c_block_data(DEVICE_ADDRESS, REG_HEARTBEAT_SPO2, 8)
                heartbeat = rbuf[2] << 24 | rbuf[3] << 16 | rbuf[4] << 8 | rbuf[5]
                spo2 = rbuf[0]
                pulse_detected = heartbeat != 0
                reading = {"pulse_detected": pulse_detected}
                if pulse_detected:
                    reading["heart_rate"] = float(heartbeat)
                if spo2 != 0:
                    reading["spo2"] = float(spo2)
            except Exception as exc:
                self._mark_failed(exc)
                return self._mock_reading()

            self._mark_ok()
            return reading

        return self._mock_reading()

    def _mock_reading(self) -> dict:
        """Idle<->contact bout cycle, matching the real driver's actual
        contract: pulse_detected only True while "someone's finger" is on
        the sensor, bpm only reported during that contact."""
        now = time.monotonic()

        if self._mock_in_contact:
            if now >= self._mock_contact_until:
                self._mock_in_contact = False
                self._mock_next_contact_at = now + random.uniform(10.0, 30.0)
        elif now >= self._mock_next_contact_at:
            self._mock_in_contact = True
            self._mock_contact_until = now + random.uniform(8.0, 15.0)

        if not self._mock_in_contact:
            return {"pulse_detected": False}

        self._mock_bpm = max(60.0, min(100.0, self._mock_bpm + random.uniform(-2.0, 2.0)))
        return {"pulse_detected": True, "heart_rate": self._mock_bpm}
