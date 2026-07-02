"""
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
"""

"""
The orchestrator. Runs three things concurrently:
 
  1. Sensor loop        — reads sensors, computes state + visual, updates
                          the shared `latest` dict (which the server reads).
  2. LED output loop    — reads colours coming back from the browser, sends
                          them to the LED strip (or prints, for now).
  3. WebSocket server   — publishes state to the browser dashboard, receives
                          canvas colours back.
 
All three share one asyncio event loop. Nothing blocks the others.
"""
import asyncio
 
from src.sensing.audio import AudioSensor
from src.intelligence import rules
from src.mapping.visual import state_to_visual
from src.output.leds import LEDStrip
from src import server
 
 
async def sensor_loop(sensors, infer):
    """Read sensors, compute state, publish to shared dict. 20 Hz."""
    while True:
        readings = {}
        for s in sensors:
            readings.update(s.read())
 
        state = infer(readings)
        visual = state_to_visual(state)
 
        # Update the shared dict the server reads from
        server.latest["state"] = state.to_dict()
        server.latest["visual"] = visual
 
        await asyncio.sleep(0.05)  # 20 Hz
 
 
async def led_loop(leds):
    """Take pixels the browser sent back and push to LEDs. 20 Hz."""
    while True:
        pixels = server.incoming["pixels"]
        if pixels is not None:
            leds.render_pixels(pixels)
        await asyncio.sleep(0.05)
 
 
async def main():
    sensors = [AudioSensor()]
    leds = LEDStrip(num_pixels=60)
    infer = rules.infer_state
 
    # Run all three concurrently. gather() waits for all to finish
    # (they won't — they're infinite loops).
    await asyncio.gather(
        sensor_loop(sensors, infer),
        led_loop(leds),
        server.start_server(host="localhost", port=8000),
    )
 
 
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")