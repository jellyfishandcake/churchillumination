from ..intelligence.state import EnvironmentState 

def state_to_visual(state: EnvironmentState) -> dict: 
    hue = 200 - int(state.activity_level * 200)
    brightness = 0.2 + 0.8 * state.activity_level 
    return {"hue": hue, "brightness": brightness}
