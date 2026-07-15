
from .state import EnvironmentState
from .audio_scene_categories import AUDIO_SCENE_CATEGORY

# AUDIO_SCENE_CATEGORY covers all 521 possible YAMNet leaf labels by rolling
# each up to one of AudioSet's 7 top-level categories - see that module's
# docstring. Two of those categories are internally consistent enough to
# bucket wholesale:
#   - "Channel, environment and background" is background noise almost by
#     definition (Silence, Static, Hum, Environmental noise, ...) -> calm.
#   - "Music" covers every genre/instrument/singing-style leaf -> festive.
# The other 5 (Human sounds, Animal, Natural sounds, Sounds of things,
# Source-ambiguous sounds) mix energetic and non-energetic leaves too
# broadly to bucket as a whole (e.g. "Human sounds" contains both
# "Cheering" and "Crying, sobbing") - LIVELY_SCENES below is a small,
# hand-picked subset of specific leaves from those categories that
# genuinely signal a lively room.
#
# activity_level itself (below) never reads any of this - it stays a pure
# loudness+motion number so effects.py's brightness/speed mapping is
# unaffected either way; audio_scene only refines the descriptive mood
# label shown on the dashboard.
LIVELY_SCENES = {"Cheering", "Applause", "Laughter", "Crowd", "Shout", "Children shouting", "Chatter"}
# handpicked lively scenes we want to override the default mood interpretation

def infer_state(readings: dict) -> EnvironmentState:
    loudness = readings.get("loudness", 0.0)
    motion = readings.get("motion", 0.0)
    scene = readings.get("audio_scene")
    category = AUDIO_SCENE_CATEGORY.get(scene)

    # Default True so infer_state stays usable on its own (e.g. in tests)
    # without needing an ActivationTracker-derived reading present.
    if not readings.get("activated", True):
        return EnvironmentState(activity_level=0.0, mood="idle", presence_count=0, audio_scene=scene)

    activity = min(1.0, 0.6 * loudness + 0.4 * motion)
    if activity > 0.6:
        mood = "lively"
    elif activity < 0.3:
        mood = "calm"
    else:
        mood = "neutral"

    # audio_scene/category refines the label only, never activity_level
    # itself. The background category wins outright: a confident
    # "Silence"/"Static"/"Hum" reading is a stronger signal than a stray
    # loudness spike (e.g. an AC hum inflating the RMS).
    if category == "Channel, environment and background":
        mood = "calm"
    elif category == "Music" and activity >= 0.3:
        mood = "festive"
    elif scene in LIVELY_SCENES and mood == "neutral":
        mood = "lively"

    return EnvironmentState(
        activity_level=activity,
        mood = mood,
        presence_count=int(motion*10),
        audio_scene=scene,
    )