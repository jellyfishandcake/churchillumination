
from .state import EnvironmentState

# Coarse groupings from YAMNet's AudioSet ontology (521 classes total) -
# just enough to nudge the mood label toward something more specific than
# activity_level's plain lively/calm/neutral, not an exhaustive taxonomy.
# activity_level itself (below) never reads these - it stays a pure
# loudness+motion number so effects.py's brightness/speed mapping is
# unaffected either way.
LIVELY_SCENES = {"Music", "Singing", "Musical instrument", "Cheering", "Applause", "Laughter", "Crowd"}
QUIET_SCENES = {"Silence", "White noise", "Pink noise", "Static"}

def infer_state(readings: dict) -> EnvironmentState:
    loudness = readings.get("loudness", 0.0)
    motion = readings.get("motion", 0.0)
    scene = readings.get("audio_scene")

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

    # audio_scene refines the label only, never activity_level itself - see
    # the comment above LIVELY_SCENES/QUIET_SCENES. QUIET_SCENES wins
    # outright: a confident "Silence"/"Static" reading is a stronger signal
    # than a stray loudness spike (e.g. an AC hum inflating the RMS).
    if scene in QUIET_SCENES:
        mood = "calm"
    elif scene == "Music" and activity >= 0.3:
        mood = "festive"
    elif scene in LIVELY_SCENES and mood == "neutral":
        mood = "lively"

    return EnvironmentState(
        activity_level=activity,
        mood = mood,
        presence_count=int(motion*10),
        audio_scene=scene,
    )