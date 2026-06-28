
from .state import EnvironmentState

def infer_state(readings: dict) -> EnvironmentState: 
    loudness = readings.get("loudness", 0.0)
    motion = readings.get("motion", 0.0)

    activity = min(1.0, 0.6 * loudness + 0.4 * motion)
    if activity > 0.6: 
        mood = "lively"
    elif activity < 0.3:
        mood = "calm"
    else: 
        mood = "neutral"
    

    return EnvironmentState(
        activity_level=activity, 
        mood = mood, 
        presence_count=int(motion*10)
    )