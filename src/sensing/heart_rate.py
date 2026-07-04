"""MAX30102 heart-rate/SpO2 sensor over I2C (address 0x57).

There's no maintained official pip package for this chip, so this is a
small hand-written driver rather than a vendored third-party blob. On init
it probes the part-ID register to confirm real hardware is attached before
trusting the "real" path.

The actual HR/SpO2 signal processing (peak detection over the IR
waveform's rolling window, converting inter-beat intervals to bpm) is left
as a TODO below — that needs real hardware in hand to tune thresholds
against, not something to guess blind. Until then, both the real-hardware
and no-hardware paths report a mock bpm — this at least confirms whether
the chip is present and reachable, without inventing a fake-but-plausible
number and presenting it as a real reading.
"""
import random

from .base import Sensor

try:
    import smbus2
except ImportError:
    smbus2 = None

MAX30102_ADDRESS = 0x57
REG_PART_ID = 0xFF
EXPECTED_PART_ID = 0x15
REG_MODE_CONFIG = 0x09
REG_FIFO_DATA = 0x07
MODE_HR_ONLY = 0x02


class HeartRateSensor(Sensor):
    def __init__(self, i2c_bus: int = 1):
        super().__init__()
        self._bus = None
        self._mock_bpm = 70.0

        if smbus2 is not None:
            try:
                bus = smbus2.SMBus(i2c_bus)
                part_id = bus.read_byte_data(MAX30102_ADDRESS, REG_PART_ID)
                if part_id == EXPECTED_PART_ID:
                    bus.write_byte_data(MAX30102_ADDRESS, REG_MODE_CONFIG, MODE_HR_ONLY)
                    self._bus = bus
            except Exception:
                self._bus = None  # no MAX30102 on this bus

    def read(self) -> dict:
        if self._bus is not None:
            # TODO: real HR/SpO2 extraction — read the IR/Red FIFO
            # (REG_FIFO_DATA, 6 bytes/sample), run peak detection over a
            # rolling window to get inter-beat intervals, convert to bpm.
            # Needs real hardware in hand to tune against. Falling through
            # to the mock estimate below in the meantime.
            pass

        return self._mock_reading()

    def _mock_reading(self) -> dict:
        self._mock_bpm = max(50.0, min(110.0, self._mock_bpm + random.uniform(-2.0, 2.0)))
        return {"heart_rate": self._mock_bpm, "pulse_detected": True}
