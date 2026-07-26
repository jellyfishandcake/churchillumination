import csv
import pathlib
import random
import threading
import time
from fractions import Fraction

import numpy as np
import sounddevice as sd
from scipy.signal import resample_poly
from .base import Sensor

# Same "Tasks-ready" YAMNet bundle Google's own Raspberry Pi audio_classifier
# sample uses (521 AudioSet classes: "Speech", "Music", "Silence", "Crowd",
# ...). Not committed to the repo (a few MB of binary). Fetch it with:
#   curl -o src/sensing/models/yamnet.tflite \
#     https://storage.googleapis.com/mediapipe-models/audio_classifier/yamnet/float32/1/yamnet.tflite
YAMNET_MODEL_PATH = pathlib.Path(__file__).parent / "models" / "yamnet.tflite"

# The model's fixed input window: 15600 raw float32 waveform samples at
# 16kHz (0.975s) - verified directly against the .tflite file's own input
# tensor shape, not assumed. There's no preprocessing to do by hand: this
# model's graph includes its own framing/mel-spectrogram step internally,
# so a plain 16kHz mono waveform is the correct input as-is.
WINDOW_SAMPLES = 15600

# 521 rows (index,mid,display_name), small enough to commit unlike the model
# itself - source: https://github.com/tensorflow/models (research/audioset/
# yamnet/yamnet_class_map.csv). `index` lines up 1:1 with the model's output
# tensor position, which is how _load_labels below turns a score index into
# a name.
YAMNET_LABELS_PATH = pathlib.Path(__file__).parent / "models" / "yamnet_class_map.csv"

# _on_audio callbacks normally arrive every ~0.1s (see InputStream's
# blocksize below) - if none has landed in this long, the stream has
# silently stopped delivering audio (USB power management suspending the
# mic, a driver hiccup, an implicit stop after a buffer overflow - all
# things that happen to USB audio over many hours of uptime on a Pi, none
# of which raise a Python exception). Generous margin over the normal
# ~0.1s cadence so ordinary scheduling jitter never trips it.
STALE_AFTER_SECONDS = 5.0

# Originally ran through mediapipe.tasks.python.audio.AudioClassifier, which
# wrapped this same model with streaming/label-lookup convenience. Switched
# to calling the .tflite file directly via ai-edge-litert because mediapipe
# currently has no working Linux aarch64 build at all (dropped in its latest
# release; piwheels' auto-builder has zero successful builds for it either -
# not a "hasn't caught up yet" gap, a real one). ai-edge-litert is Google's
# actively maintained, lighter-weight successor for just running .tflite
# models, and publishes real Python 3.13 aarch64 wheels.
try:
    from ai_edge_litert.interpreter import Interpreter
except ImportError:
    Interpreter = None


def _load_labels() -> list:
    with YAMNET_LABELS_PATH.open(newline="", encoding="utf-8") as f:
        return [row["display_name"] for row in csv.DictReader(f)]


def _resample_ratio(native_rate: int, target_rate: int = 16000) -> tuple:
    """Reduced (up, down) integer ratio for scipy.signal.resample_poly.
    Cheap USB mics commonly only support their own native rate (44100Hz,
    48000Hz, ...) over the raw ALSA hardware interface, not the 16000Hz
    YAMNet needs directly - opening an InputStream at 16000Hz on such a
    device fails at stream-open time (PortAudio error -9997, "Invalid
    sample rate"). Detecting the connected mic's actual native rate and
    resampling in software (rather than hardcoding one specific mic's rate)
    means this keeps working if the mic's ever swapped for a different one."""
    frac = Fraction(target_rate, native_rate).limit_denominator(1000)
    return frac.numerator, frac.denominator


class AudioSensor(Sensor):
    # here we read laptop mic and turns volume into a number
    def __init__(self, sensitivity: float = 20.0, score_threshold: float = 0.3): ## Calibrate sensitivity to get a good range of loudness - between 0 and 1 (max)
        super().__init__()
        self._loudness = 0.0
        self._mock_loudness = 0.05  # used only if no mic is ever found - see read()
        self.sensitivity = sensitivity
        self.score_threshold = score_threshold
        self._scene = None        # top YAMNet class name, e.g. "Speech" - None until the model's loaded and a result's arrived
        self._scene_score = 0.0

        self._interpreter = None
        self._input_index = None
        self._output_index = None
        self._labels = None
        self._buffer = np.zeros(0, dtype=np.float32)  # rolling window fed to the interpreter
        self._classifying = False  # guards against overlapping background inference calls
        self._samples_since_classify = WINDOW_SAMPLES  # forces one right away once the buffer first fills
        self._latest_callback_at = None  # set once the stream's first callback actually lands - see read()

        if Interpreter is not None and YAMNET_MODEL_PATH.is_file():
            try:
                self._interpreter = Interpreter(model_path=str(YAMNET_MODEL_PATH))
                self._interpreter.allocate_tensors()
                self._input_index = self._interpreter.get_input_details()[0]["index"]
                self._output_index = self._interpreter.get_output_details()[0]["index"]
                self._labels = _load_labels()
            except Exception as exc:
                # Loudness (below) still works without this - scene classification
                # is additive, not load-bearing for the rest of the pipeline.
                print(f"[AudioSensor] couldn't load YAMNet ({exc}) - scene classification disabled")
                self._interpreter = None

        # See _resample_ratio's docstring - the input stream runs at
        # whatever rate the connected mic actually supports natively, not a
        # hardcoded 16000, and _on_audio resamples down before it reaches
        # the classifier (which does need exactly 16000Hz).
        self._resample_up = 1
        self._resample_down = 1
        self._stream = None
        try:
            native_rate = int(sd.query_devices(kind="input")["default_samplerate"])
            self._resample_up, self._resample_down = _resample_ratio(native_rate)
            self._stream = sd.InputStream(
                channels=1,
                samplerate=native_rate,
                blocksize=round(native_rate * 0.1),  # ~0.1s per callback, same cadence as before
                callback=self._on_audio,
            )
            self._stream.start()
        except Exception as exc:
            print(f"[AudioSensor] couldn't open an audio input stream ({exc}) - loudness/scene readings disabled")
            self._stream = None

    def _classify(self, window: np.ndarray) -> None:
        """Runs off the audio callback thread (see _on_audio) - a TFLite
        Interpreter call takes a few tens of ms on a Pi, too slow to do
        inline in a real-time audio callback without risking dropouts."""
        try:
            self._interpreter.set_tensor(self._input_index, window)
            self._interpreter.invoke()
            scores = self._interpreter.get_tensor(self._output_index)[0]
            top_idx = int(np.argmax(scores))
            top_score = float(scores[top_idx])
            if top_score >= self.score_threshold:
                self._scene = self._labels[top_idx]
                self._scene_score = top_score
            # Below threshold: leave the previous scene/score in place, same
            # as mediapipe's AudioClassifier silently omitting low-confidence
            # results rather than reporting "nothing."
        except Exception as exc:
            print(f"[AudioSensor] classification failed: {exc}")
        finally:
            self._classifying = False

    def _on_audio(self, indata, frames, time_info, status):
        self._latest_callback_at = time.monotonic()
        mono = indata[:, 0]
        rms = float(np.sqrt(np.mean(mono**2)))
        self._loudness = min(1.0, rms*self.sensitivity) # scale + clamp to a max of 1 - computed on the mic's native rate directly, resampling doesn't meaningfully change RMS

        if self._interpreter is not None:
            # Resample from the mic's native rate to the 16000Hz the model
            # needs (see _resample_ratio) - only bothers doing this work
            # when there's actually a classifier loaded to feed. indata is
            # already float32 in [-1, 1] (sounddevice's default dtype),
            # matching what the model expects directly, just at the wrong
            # rate. Keep only the most recent WINDOW_SAMPLES - a sliding
            # window, not an ever-growing buffer.
            resampled = resample_poly(mono, self._resample_up, self._resample_down).astype(np.float32)
            self._buffer = np.concatenate([self._buffer, resampled])[-WINDOW_SAMPLES:]
            self._samples_since_classify += len(resampled)

            # len(self._buffer) == WINDOW_SAMPLES stays true on every single
            # callback once the rolling buffer first fills (it's a sliding
            # window, not a one-shot fill) - without also gating on
            # _samples_since_classify, that fires a fresh YAMNet inference
            # ~10x/second forever instead of roughly once per window. That
            # pins a background thread near 100% CPU continuously, which on
            # a Pi is enough to starve the single asyncio event loop thread
            # of the GIL - the "mic reading gets stuck and nothing updates,
            # lights included" symptom this was causing. Gating to one
            # trigger per full WINDOW_SAMPLES worth of new audio restores
            # the intended ~1x/second cadence.
            if (
                len(self._buffer) == WINDOW_SAMPLES
                and self._samples_since_classify >= WINDOW_SAMPLES
                and not self._classifying
            ):
                self._classifying = True
                self._samples_since_classify = 0
                threading.Thread(target=self._classify, args=(self._buffer.copy(),), daemon=True).start()

    def read(self) -> dict:
        if self._stream is None:
            # No mic at construction time (unplugged, or never present) -
            # same "still something to react to on a dev machine/without
            # hardware" contract every other sensor here follows, rather
            # than freezing at 0. A gentle bounded random walk, not
            # PIRSensor's occasional-spike style - loudness is a continuous
            # level, not a discrete event.
            self._mock_loudness = min(1.0, max(0.0, self._mock_loudness + random.uniform(-0.03, 0.03)))
            return {"loudness": self._mock_loudness}

        # The stream object existing doesn't mean it's still actually
        # delivering audio - see STALE_AFTER_SECONDS' docstring. Checked
        # here rather than only in _on_audio since the whole point is
        # detecting when that callback has stopped firing at all.
        if (
            self._latest_callback_at is not None
            and time.monotonic() - self._latest_callback_at > STALE_AFTER_SECONDS
        ):
            self._mark_failed(RuntimeError("no audio callback in over 5s - stream likely died silently"))
            self._mock_loudness = min(1.0, max(0.0, self._mock_loudness + random.uniform(-0.03, 0.03)))
            return {"loudness": self._mock_loudness}

        reading = {"loudness": self._loudness}
        if self._interpreter is not None:
            reading["audio_scene"] = self._scene
            reading["audio_scene_score"] = self._scene_score
        self._mark_ok()
        return reading
