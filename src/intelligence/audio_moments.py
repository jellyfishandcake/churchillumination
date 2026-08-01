"""audio_moments.py

Detects a "moment" worth celebrating in the room - laughter, applause,
cheering, singing, or music - from YAMNet's scene classification, and turns
it into a short decaying pulse (0..1, fading over decay_seconds) rather
than a flat boolean, so the ambient zone's ripple overlay (see
led_effects.AudioReactiveWaveEffect) can wash in and fade back out smoothly
instead of snapping on/off.

Deliberately fed from raw (unsmoothed) audio_scene/audio_scene_score in
main.py's sensor_loop, same reasoning as ActivationTracker's hr_tracker/
motion_tracker there: this needs to react on the actual tick a scene's
detected, not lag behind the EMA smoothing applied to numeric readings.

RIPPLE_SCENES is hand-picked rather than bucketing YAMNet's whole "Human
sounds" category wholesale - same reasoning as rules.py's LIVELY_SCENES:
that category also contains non-celebratory leaves (Speech itself,
coughing, crying), so bucketing it wholesale would ripple on the wrong
sounds. "Music" is safe to bucket wholesale via AUDIO_SCENE_CATEGORY (see
that module's own docstring) - it covers singing/choir/chant/instruments/
every genre without hand-listing each one.
"""
from .audio_scene_categories import AUDIO_SCENE_CATEGORY

RIPPLE_SCENES = {"Laughter", "Cheering", "Applause"}
CONFIDENCE_THRESHOLD = 0.6  # higher bar than rules.py's mood-labelling threshold (0.3) - a wrong ripple is far more visible than a wrong mood word
DECAY_SECONDS = 1.5


class AudioMomentTracker:
    def __init__(self, decay_seconds: float = DECAY_SECONDS):
        self.decay_seconds = decay_seconds
        self._triggered_at = None

    def update(self, scene, score: float, now: float) -> float:
        """Call once per tick with the raw audio_scene/audio_scene_score
        reading. Returns 0..1: 1.0 the instant a qualifying scene's
        detected, linearly fading to 0.0 over decay_seconds if nothing new
        re-triggers it."""
        is_moment = score >= CONFIDENCE_THRESHOLD and (
            scene in RIPPLE_SCENES or AUDIO_SCENE_CATEGORY.get(scene) == "Music"
        )
        if is_moment:
            self._triggered_at = now
        if self._triggered_at is None:
            return 0.0
        age = now - self._triggered_at
        return max(0.0, 1.0 - age / self.decay_seconds)
