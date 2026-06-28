import time
from src.sensing.audio import AudioSensor
from src.sensing.motion import MotionSensor
from src.intelligence import rules
from src.mapping.visuals import state_to_visual
from src.output.leds import LEDstrip

def main():
    sensors = [AudioSensor(), MotionSensor()]
    leds = LEDstrip(num_pixels = 60)
    infer = rules.infer_state

    while True: 
        readings = {} 
        for s in sensors: 
            readings.update(s.read())
        state = infer(readings)
        visual = state_to_visual(state) 
        leds.render(visual)
        time.sleep(0.1) 

if __name__ == "__main__":
    main()