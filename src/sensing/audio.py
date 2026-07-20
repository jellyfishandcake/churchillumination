import csv
import pathlib
import threading

import numpy as np
import sounddevice as sd
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


class AudioSensor(Sensor):
    # here we read laptop mic and turns volume into a number
    def __init__(self, sensitivity: float = 20.0, score_threshold: float = 0.3): ## Calibrate sensitivity to get a good range of loudness - between 0 and 1 (max)
        super().__init__()
        self._loudness = 0.0
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

        self._stream = sd.InputStream(
            channels=1,
            samplerate = 16000,
            blocksize = 1600,
            callback = self._on_audio,
        )
        self._stream.start()

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
        rms = float(np.sqrt(np.mean(indata**2)))
        self._loudness = min(1.0, rms*self.sensitivity) # scale + clamp to a max of 1

        if self._interpreter is not None:
            # indata is already float32 in [-1, 1] (sounddevice's default
            # dtype), matching what the model expects directly. Keep only
            # the most recent WINDOW_SAMPLES - a sliding window, not an
            # ever-growing buffer.
            self._buffer = np.concatenate([self._buffer, indata[:, 0]])[-WINDOW_SAMPLES:]
            if len(self._buffer) == WINDOW_SAMPLES and not self._classifying:
                self._classifying = True
                threading.Thread(target=self._classify, args=(self._buffer.copy(),), daemon=True).start()

    def read(self) -> dict:
        reading = {"loudness": self._loudness}
        if self._interpreter is not None:
            reading["audio_scene"] = self._scene
            reading["audio_scene_score"] = self._scene_score
        return reading
