import random

import numpy as np
import cv2  # OpenCV -- the library for computer vision
from .base import Sensor

try:
    from picamera2 import Picamera2  # Pi Camera Module 3 / NoIR 3
except ImportError:
    Picamera2 = None


class MotionSensor(Sensor):
    """Frame-diff motion detector. Tries the Pi camera (picamera2) first —
    that's the real deployment hardware — and falls back to a USB/laptop
    webcam via OpenCV (today's dev-machine path) if picamera2 isn't
    available or no Pi camera is attached. If neither camera is present,
    falls back to a low-level mock so read() never throws with zero
    cameras attached."""

    def __init__(self, sensitivity: float = 8.0):
        self.sensitivity = sensitivity
        self._prev = None
        self._picam = None
        self._cv_cam = None

        if Picamera2 is not None:
            try:
                self._picam = Picamera2()
                config = self._picam.create_preview_configuration(
                    main={"size": (160, 120), "format": "RGB888"}
                )
                self._picam.configure(config)
                self._picam.start()
            except Exception:
                self._picam = None  # no Pi camera attached

        if self._picam is None:
            cam = cv2.VideoCapture(0)  # open the default camera (webcam)
            if cam.isOpened():
                self._cv_cam = cam

    def _grab_gray_frame(self):
        if self._picam is not None:
            frame = self._picam.capture_array()
            return cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)

        if self._cv_cam is not None:
            ok, frame = self._cv_cam.read()
            if not ok:
                return None
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            return cv2.resize(gray, (160, 120))  # resize for faster processing

        return None

    def read(self) -> dict:
        gray = self._grab_gray_frame()
        if gray is None:
            return {"motion": random.uniform(0.0, 0.05)}  # no camera attached

        if self._prev is None:
            self._prev = gray
            return {"motion": 0.0}

        diff = np.abs(gray.astype(float) - self._prev.astype(float))  # pixel changes between frames
        self._prev = gray
        motion = min(1.0, (diff.mean() / 255.0) * self.sensitivity)  # scale + clamp to a max of 1

        return {"motion": motion}