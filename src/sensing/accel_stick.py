"""Handheld M5Stack ESP32S3 accelerometer stick - visitors pick it up and
shake it. Wired to the Pi over USB-serial (pyserial) rather than WiFi/MQTT:
a physical tether is both simpler (no broker/WiFi provisioning) and keeps
the stick from walking off, since it can only go as far as its cable.

The M5Stack's own firmware (separate toolchain, written once the board
arrives - same "not part of this Python codebase" situation as nodes.py's
ESP32S3 presence-node firmware) does its own raw-IMU-to-normalised-
magnitude conversion on-device and prints one newline-delimited JSON object
per line, e.g.:
  {"acceleration": 0.3}\n

The Pi side just reads and clamps that number - it doesn't need the raw
axes, since the firmware already did the "deviation from 1g" math itself.
"acceleration" is [0, 1] (shake magnitude) - the only field config.py's
accelerometer zone uses (ShakeFireworkEffect, which fires every arm at
once on a vigorous shake, no direction involved).

Simplified 2026-08-19: this used to also read/clamp an "angle_deg" field
(the firmware's own swing-direction estimate, atan2 then later a full
gyro+accel+magnetometer fusion stack - see accel_stick.ino's git history),
feeding the old TriArmGlideEffect's direction-mapped arm selection.
Direction sensing on a handheld stick never got reliably working across
several real-hardware sessions, and ShakeFireworkEffect replaced that
whole design with an omnidirectional shake trigger that only ever needed
shake magnitude - so both accel_stick.ino and this file dropped
angle_deg entirely rather than carry an unused field forward."""
import json
import random
import time

from .base import Sensor

try:
    import serial
except ImportError:
    serial = None

STALE_AFTER_SECONDS = 10.0
MAX_RX_BUFFER_BYTES = 4096  # a real JSON line here is well under 100 bytes - see _poll_serial's overflow guard


class AccelStickSensor(Sensor):
    def __init__(self, serial_port: str = "/dev/ttyUSB0", baud_rate: int = 115200):
        super().__init__()
        self._serial = None
        self._latest_acceleration = 0.0
        self._latest_at = 0.0
        self._rx_buffer = b""  # bytes received so far that don't yet form a complete newline-terminated line

        if serial is not None:
            try:
                self._serial = serial.Serial(serial_port, baud_rate, timeout=0)
            except Exception:
                self._serial = None  # stick not plugged in, or wrong port

    def _poll_serial(self) -> None:
        """Drain any buffered lines, keeping only the most recent reading -
        this is a live sensor value, not a queue of history to replay.

        Reads raw bytes and buffers/splits on newlines by hand rather than
        calling pyserial's readline() directly - with timeout=0 (non-
        blocking, required so this never stalls sensor_loop's asyncio event
        loop waiting on serial I/O), readline() returns whatever partial
        bytes happen to be available the instant it's called rather than
        waiting for a full line (confirmed against pyserial's own docs/
        issue tracker, e.g. github.com/pyserial/pyserial/issues/248) - since
        this poll and the M5Stick's own ~20Hz send loop aren't synchronised,
        that was silently truncating JSON mid-line on most polls, which
        failed json.loads() and got discarded every time - acceleration
        then never left its 0.0 default despite the stick genuinely
        sending valid data the whole time (confirmed 2026-08-14: raw
        serial via `cat` showed clean JSON while this class reported exact
        zero). Buffering raw bytes across polls instead means a line split
        across two polls still gets assembled correctly before
        json.loads() ever sees it."""
        try:
            chunk = self._serial.read(self._serial.in_waiting)
            if not chunk:
                return
            self._rx_buffer += chunk
            if len(self._rx_buffer) > MAX_RX_BUFFER_BYTES:
                # No newline for way longer than any real JSON line here
                # should ever take (a garbled connection, or a firmware
                # that's stopped sending \n-terminated lines) - drop it
                # rather than let this grow unbounded over a weeks-long
                # unattended run. Losing whatever partial line was in
                # flight is fine; the next complete line still gets read
                # normally.
                self._rx_buffer = b""
            *complete_lines, self._rx_buffer = self._rx_buffer.split(b"\n")
            for line in complete_lines:
                try:
                    payload = json.loads(line.decode().strip())
                    acceleration = float(payload["acceleration"])
                except (ValueError, KeyError, UnicodeDecodeError):
                    continue  # malformed line - ignore, keep the last good reading
                self._latest_acceleration = min(1.0, max(0.0, acceleration))
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
            return {"acceleration": 0.0}  # stick connected but quiet - not stale-mocked, genuinely idle

        self._mark_ok()
        return {"acceleration": self._latest_acceleration}


def _mock_reading() -> dict:
    """Small idle jitter, punctuated by occasional simulated "shake" bursts,
    so the mock exercises the motion-burst tracker the same way a real
    handheld stick being picked up and shaken would."""
    if random.random() < 0.02:
        return {"acceleration": random.uniform(0.3, 1.0)}
    return {"acceleration": random.uniform(0.0, 0.05)}
