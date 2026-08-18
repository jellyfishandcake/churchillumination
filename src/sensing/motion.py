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
FRAME_RATE_HZ = 8     # passed to MLX90640.setup() - see the library's README for supported rates.
                      # Was 16 (the sensor's max) - dropped to 8 to give a marginal I2C
                      # connection more timing headroom; MLX90640_API.cpp's frame-read
                      # polling loop (cnt > 4) hits more retries the higher this is.


class MotionSensor(Sensor):
    """sensitivity is a placeholder — thermal frame-diff magnitudes (°C)
    are a very different scale to the old 0-255 grayscale diff, and need
    calibrating against the real sensor once it's in hand, not guessed.
    webcam_sensitivity only matters on the dev-machine webcam fallback.

    For the shadow-cast blob (web/shadow.html's projector effect - whichever
    pixels currently read as "person" are published as a 0..1 mask, see
    read()'s thermal_mask/thermal_width/thermal_height): thermal frames use
    an ABSOLUTE temperature threshold (human_temp_range), not a background-
    baseline diff. A baseline-diff approach was tried first and dropped
    (2026-08-11) after real-hardware testing showed the whole frame reading
    as foreground simultaneously - baseline drift (sensor self-heating
    after power-on, general room warming, or a few cnt>4 I2C hiccups
    feeding a noisy baseline) can push EVERY pixel "above its own baseline"
    at once, which an absolute threshold simply can't do, since it doesn't
    depend on any history at all - a pixel is either in the human range or
    it isn't, tick to tick. human_temp_range is a placeholder (a clothed/
    skin surface reading, room ambient assumed well below it) - low end
    lowered 27.0->24.0 (2026-08-18, still by feel, no real calibration data
    yet) since 27.0 risked missing a clothed/distant/cooler-reading person
    entirely as "just room temperature" rather than just under-including
    them at the edges. Configurable via config.yaml's sensors.motion.
    human_temp_range_low/high (see main.py's build_sensors) specifically so
    it doesn't need a code redeploy to retune - calibrate with
    `python -m tools.calibrate_sensor motion --live` and watch
    thermal_frame_min/max/mean with the room empty vs a person in frame,
    then set the low end to comfortably above the empty-room max.

    webcam_blob_threshold keeps the OLD baseline-diff approach (dev-only
    fallback, not deployment hardware) - a fixed "brightness above N" cutoff
    doesn't make sense for an ordinary camera the way it does for a thermal
    one, since ordinary lighting varies scene to scene in a way body-surface
    temperature doesn't. Still just as untuned as webcam_sensitivity -
    doesn't matter for the real installation."""

    # Per-tick EMA rate for the webcam fallback's background model -
    # deliberately far slower than any single frame-diff (SMOOTHING_ALPHA-
    # scale would still fade a stationary person out within seconds); this
    # is closer to "minutes" so a shadow doesn't visibly fade while someone's
    # just standing there. Thermal frames don't use this at all - see above.
    BASELINE_ALPHA = 0.005

    def __init__(self, sensitivity: float = 10.0, webcam_sensitivity: float = 8.0,
                 human_temp_range: tuple = (24.0, 34.0), webcam_blob_threshold: tuple = (15.0, 60.0)):
        super().__init__()
        self.sensitivity = sensitivity
        self.webcam_sensitivity = webcam_sensitivity
        self.human_temp_range = human_temp_range  # (low, high) °C, absolute - mask ramps 0..1 across this range
        self.webcam_blob_threshold = webcam_blob_threshold  # (low, high) °C-above-baseline-equivalent, 0-255 grayscale units - dev fallback only, see docstring
        self._prev = None
        self._baseline = None  # slow per-pixel background model, webcam fallback only - see docstring
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

    def _blob_mask(self, frame, is_thermal):
        """0..1 per-pixel foreground strength, flat (same shape as `frame`)
        - see the class docstring for why thermal and webcam frames use two
        different approaches here (absolute threshold vs background-baseline
        diff, respectively)."""
        if is_thermal:
            low, high = self.human_temp_range
            return np.clip((frame - low) / (high - low), 0.0, 1.0)

        # Webcam fallback only, from here down - baseline-diff, updated
        # (and, on the first call, seeded) as a side effect, so callers get
        # a mask "for free" alongside `motion` on each read() rather than
        # needing a second call per tick.
        if self._baseline is None:
            self._baseline = frame.astype(float).copy()
            return np.zeros_like(frame, dtype=float)

        diff = frame - self._baseline
        self._baseline += self.BASELINE_ALPHA * (frame - self._baseline)

        low, high = self.webcam_blob_threshold
        return np.clip((diff - low) / (high - low), 0.0, 1.0)

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

        if is_thermal and (np.isnan(frame).any() or np.all(frame == frame.flat[0])):
            # Pimoroni's MLX90640 C binding never raises a Python exception on
            # I2C failure - it just prints "I2C Read Error!" internally and
            # still returns its static buffer regardless, so a disconnected/
            # failing sensor is indistinguishable from a working one via
            # try/except alone. NaN, or all 768 pixels reading back exactly
            # identical, is what that stale/never-actually-written buffer
            # looks like - real thermal data always has some pixel-to-pixel
            # sensor noise even pointed at a blank wall in a static room.
            self._mark_failed(RuntimeError("thermal frame looks invalid (I2C read likely failing silently)"))
            return {"motion": random.uniform(0.0, 0.05)}

        mask = self._blob_mask(frame, is_thermal)
        if is_thermal:
            # MLX90640's fixed layout - get_frame() hands back a flat 768-
            # element array with no row/col structure attached, so this
            # width/height (and therefore row-major-ness/orientation) is
            # the datasheet's stated 32x24, not yet confirmed against a
            # real frame + a person standing in a known spot. If
            # web/shadow.html's shadow ends up mirrored or rotated relative
            # to where someone's actually standing, this is the first place
            # to check, alongside shadow.js's FLIP_X/FLIP_Y.
            width, height = 32, 24
        else:
            height, width = frame.shape  # dev-only webcam fallback - already a real 2D (height, width) array
        blob_reading = {
            "thermal_mask": mask.flatten().tolist(),
            "thermal_width": width,
            "thermal_height": height,
        }
        if is_thermal:
            # Raw absolute per-pixel temperature stats, °C - not consumed by
            # any zone or by _blob_mask's own math above, purely so
            # tools/calibrate_sensor.py's min/max/mean readout gives real
            # numbers to set human_temp_range from: run `calibrate_sensor.py
            # motion --live` and watch these with the room empty (sets your
            # floor) vs a person standing in frame (sets your ceiling).
            blob_reading["thermal_frame_min"] = float(frame.min())
            blob_reading["thermal_frame_max"] = float(frame.max())
            blob_reading["thermal_frame_mean"] = float(frame.mean())

        if self._prev is None:
            self._prev = frame
            self._mark_ok()
            return {"motion": 0.0, **blob_reading}

        diff = np.abs(frame - self._prev)  # per-pixel change between frames
        self._prev = frame
        if is_thermal:
            motion = min(1.0, diff.mean() * self.sensitivity)  # diff is in °C
        else:
            motion = min(1.0, (diff.mean() / 255.0) * self.webcam_sensitivity)  # diff is 0-255 grayscale

        self._mark_ok()
        # motion_raw_diff: the unscaled diff.mean() in native units (°C for
        # thermal, 0-255 grayscale for webcam), before sensitivity/clamping -
        # not consumed by any zone (config.yaml only ever references
        # `motion`), purely so tools/calibrate_sensor.py's min/max/mean
        # readout is actually useful here. `sensitivity`/`webcam_sensitivity`
        # are untuned placeholders (see class docstring) and easy to
        # saturate: sensitivity=10.0 clips `motion` to 1.0 at just a 0.1°C
        # mean frame-to-frame diff, well within ordinary sensor read noise -
        # if `motion` is pinned at 1.0 with nobody around, watch
        # motion_raw_diff for a stretch (calibrate_sensor.py motion) and
        # raise sensitivity's denominator (i.e. lower the constant) until
        # idle noise reads near 0 and only real movement pushes it toward 1.
        return {"motion": motion, "motion_raw_diff": float(diff.mean()), **blob_reading}
