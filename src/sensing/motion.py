"""Central thermal motion sensor — Pimoroni's MLX90640 thermal camera
breakout (32x24 IR pixel array, 768 pixels total) over I2C, replacing the
original RGB/NoIR camera. A moving warm body changes the thermal pattern
frame to frame, same frame-diff principle the old grayscale-camera
version used, just on temperature deltas (°C) instead of pixel luminance.

Uses Pimoroni's own MLX90640 Python binding
(github.com/pimoroni/mlx90640-library) - a compiled C extension, not a
PyPI package. Build from source on the Pi:
  git clone https://github.com/pimoroni/mlx90640-library
  cd mlx90640-library && make && sudo make install
  cd python/library && make build && sudo make install
See requirements-pi.txt.

If no MLX90640 is present (e.g. on a dev laptop where it was never
built), falls back to a USB/laptop webcam via OpenCV — this is NOT
deployment hardware, just a convenient way to test the motion pipeline
without real thermal hardware in hand. If neither is present, falls back
to a low-level mock so read() never throws with nothing attached.
"""
import random

import numpy as np
from .base import Sensor

try:
    import MLX90640
except ImportError:
    MLX90640 = None

try:
    import cv2  # OpenCV -- dev-machine webcam fallback only, not deployment hardware
except ImportError:
    cv2 = None

FRAME_SIZE = 32 * 24  # MLX90640's fixed resolution
FRAME_RATE_HZ = 16    # passed to MLX90640.setup() - see the library's README for supported rates


class MotionSensor(Sensor):
    """sensitivity is a placeholder — thermal frame-diff magnitudes (°C)
    are a very different scale to the old 0-255 grayscale diff, and need
    calibrating against the real sensor once it's in hand, not guessed.
    webcam_sensitivity only matters on the dev-machine webcam fallback."""

    def __init__(self, sensitivity: float = 10.0, webcam_sensitivity: float = 8.0):
        super().__init__()
        self.sensitivity = sensitivity
        self.webcam_sensitivity = webcam_sensitivity
        self._prev = None
        self._active = False  # MLX90640 present
        self._cv_cam = None   # dev-machine webcam fallback

        if MLX90640 is not None:
            try:
                MLX90640.setup(FRAME_RATE_HZ)
                self._active = True
            except Exception:
                self._active = False  # no MLX90640 on this bus

        if not self._active and cv2 is not None:
            cam = cv2.VideoCapture(0)  # open the default camera (webcam)
            if cam.isOpened():
                self._cv_cam = cam

    def _grab_frame(self):
        """Returns (frame, is_thermal). frame is None if nothing's attached."""
        if self._active:
            return np.array(MLX90640.get_frame()), True  # flat array of 768 temperatures (°C)

        if self._cv_cam is not None:
            ok, frame = self._cv_cam.read()
            if not ok:
                return None, False
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            return cv2.resize(gray, (160, 120)).astype(float), False

        return None, False

    def read(self) -> dict:
        try:
            frame, is_thermal = self._grab_frame()
        except Exception as exc:
            # Sensor was working, then a live read failed (e.g. disconnected
            # mid-run) — same fallback as "nothing attached at all".
            self._mark_failed(exc)
            return {"motion": random.uniform(0.0, 0.05)}

        if frame is None:
            return {"motion": random.uniform(0.0, 0.05)}  # nothing attached

        if self._prev is None:
            self._prev = frame
            self._mark_ok()
            return {"motion": 0.0}

        diff = np.abs(frame - self._prev)  # per-pixel change between frames
        self._prev = frame
        if is_thermal:
            motion = min(1.0, diff.mean() * self.sensitivity)  # diff is in °C
        else:
            motion = min(1.0, (diff.mean() / 255.0) * self.webcam_sensitivity)  # diff is 0-255 grayscale

        self._mark_ok()
        return {"motion": motion}
