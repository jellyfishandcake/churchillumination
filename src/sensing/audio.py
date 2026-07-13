import pathlib

import numpy as np
import sounddevice as sd
from .base import Sensor

# MediaPipe's Tasks-ready YAMNet bundle (521 AudioSet classes: "Speech",
# "Music", "Silence", "Crowd", ...) - has the metadata the Audio Classifier
# task needs baked in, unlike the plain model on TF Hub. Not committed to
# the repo (a few MB of binary) - same file Google's own Raspberry Pi
# audio_classifier sample uses:
# https://github.com/google-ai-edge/mediapipe-samples/tree/main/examples/audio_classifier/raspberry_pi
# Fetch it with:
#   curl -o src/sensing/models/yamnet.tflite \
#     https://storage.googleapis.com/mediapipe-models/audio_classifier/yamnet/float32/1/yamnet.tflite
YAMNET_MODEL_PATH = pathlib.Path(__file__).parent / "models" / "yamnet.tflite"

try:
    from mediapipe.tasks.python import BaseOptions
    from mediapipe.tasks.python.audio import AudioClassifier, AudioClassifierOptions, RunningMode
    from mediapipe.tasks.python.components.containers import AudioData
except ImportError:
    BaseOptions = None


class AudioSensor(Sensor):
    # here we read laptop mic and turns volume into a number
    def __init__(self, sensitivity: float = 20.0, max_results: int = 3, score_threshold: float = 0.3): ## Calibrate sensitivity to get a good range of loudness - between 0 and 1 (max)
        super().__init__()
        self._loudness = 0.0
        self.sensitivity = sensitivity
        self._scene = None        # top YAMNet class name, e.g. "Speech" - None until the model's loaded and a result's arrived
        self._scene_score = 0.0
        self._classifier = None
        self._samples_fed = 0     # drives the classifier's timestamp - see _on_audio

        if BaseOptions is not None and YAMNET_MODEL_PATH.is_file():
            try:
                options = AudioClassifierOptions(
                    base_options=BaseOptions(model_asset_path=str(YAMNET_MODEL_PATH)),
                    running_mode=RunningMode.AUDIO_STREAM,
                    max_results=max_results,
                    score_threshold=score_threshold,
                    result_callback=self._on_classification,
                )
                self._classifier = AudioClassifier.create_from_options(options)
            except Exception as exc:
                # Loudness (below) still works without this - scene classification
                # is additive, not load-bearing for the rest of the pipeline.
                print(f"[AudioSensor] couldn't load YAMNet ({exc}) - scene classification disabled")

        self._stream = sd.InputStream(
            channels=1,
            samplerate = 16000,
            blocksize = 1600,
            callback = self._on_audio,
        )
        self._stream.start()

    def _on_classification(self, result, timestamp_ms):
        """AudioClassifier's result_callback - called asynchronously (not
        necessarily from _on_audio's thread) whenever a classification for
        an earlier classify_async() call is ready."""
        if not result.classifications or not result.classifications[0].categories:
            return
        top = result.classifications[0].categories[0]
        self._scene = top.category_name
        self._scene_score = top.score

    def _on_audio(self, indata, frames, time_info, status):
        rms = float(np.sqrt(np.mean(indata**2)))
        self._loudness = min(1.0, rms*self.sensitivity) # scale + clamp to a max of 1

        if self._classifier is not None:
            # indata is already float32 in [-1, 1] (sounddevice's default
            # dtype) - AudioData expects normalised samples, so no int16
            # rescale needed here, just flatten the (frames, 1) mono block.
            audio_data = AudioData.create_from_array(indata[:, 0].astype(float), sample_rate=16000)
            # Must advance in lockstep with the sample count, not wall-clock
            # time - a couple ms of real-time jitter per callback is normal,
            # but the classifier's internal stream graph treats any drift
            # from the expected sample-rate-derived timestamp as an error.
            timestamp_ms = int(self._samples_fed / 16000 * 1000)
            self._classifier.classify_async(audio_data, timestamp_ms)
            self._samples_fed += frames

    def read(self) -> dict:
        reading = {"loudness": self._loudness}
        if self._classifier is not None:
            reading["audio_scene"] = self._scene
            reading["audio_scene_score"] = self._scene_score
        return reading

