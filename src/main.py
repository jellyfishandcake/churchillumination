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

from src.config import load_config
from src.sensing.audio import AudioSensor
from src.sensing.motion import MotionSensor
from src.sensing.sense_hat import SenseHatSensor
from src.sensing.pir import PIRSensor
from src.sensing.heart_rate import HeartRateSensor
from src.sensing.nodes import NodeSensor
from src.intelligence import rules
from src.mapping.visuals import state_to_visual
from src.output.leds import LEDStrip
from src.intelligence import server


def build_sensors(config: dict) -> list:
    """Build the sensor list from config's `sensors.*.enabled` flags. Every
    sensor class auto-falls-back to a mock if its hardware/library isn't
    present, so this list is safe to build the same way on a dev laptop and
    on the Pi — config only controls which sensors are wired in at all."""
    sensors_config = config["sensors"]
    sensors = []

    if sensors_config["audio"]["enabled"]:
        sensors.append(AudioSensor())
    if sensors_config["motion"]["enabled"]:
        sensors.append(MotionSensor())
    if sensors_config["sense_hat"]["enabled"]:
        sensors.append(SenseHatSensor())
    if sensors_config["pir"]["enabled"]:
        sensors.append(PIRSensor(gpio_pin=sensors_config["pir"]["gpio_pin"]))
    if sensors_config["heart_rate"]["enabled"]:
        sensors.append(HeartRateSensor())
    if sensors_config["nodes"]["enabled"]:
        nodes_config = sensors_config["nodes"]
        sensors.append(
            NodeSensor(
                node_ids=nodes_config["node_ids"],
                mqtt_host=nodes_config["mqtt_host"],
                mqtt_port=nodes_config["mqtt_port"],
            )
        )

    return sensors


# Exponential moving average factor for smoothing raw sensor readings before
# they reach infer_state. Lower = calmer/slower to react to a single spike,
# higher = snappier. Without this, one loud cough or a webcam auto-exposure
# blip swings activity_level (and the mood label) instantly, since nothing
# else in rules.py/visuals.py remembers what a reading was a moment ago.
SMOOTHING_ALPHA = 0.15


def _smooth_readings(previous: dict, current: dict, alpha: float) -> dict:
    """EMA-smooth each numeric reading against its previous value. Non-numeric
    values (e.g. `pulse_detected`, the nested `nodes` dict) and any key seen
    for the first time pass through unsmoothed."""
    smoothed = dict(previous)
    for key, value in current.items():
        is_numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
        previous_value = previous.get(key)
        if is_numeric and isinstance(previous_value, (int, float)):
            smoothed[key] = previous_value + alpha * (value - previous_value)
        else:
            smoothed[key] = value
    return smoothed


async def sensor_loop(sensors, infer):
    """Read sensors, smooth them, compute state, publish to shared dict. 20 Hz."""
    smoothed = {}
    while True:
        raw = {}
        for s in sensors:
            raw.update(s.read())

        smoothed = _smooth_readings(smoothed, raw, SMOOTHING_ALPHA)

        state = infer(smoothed)
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
    config = load_config()
    sensors = build_sensors(config)
    leds = LEDStrip(num_pixels=config["leds"]["num_pixels"])
    infer = rules.infer_state

    # Run all three concurrently. gather() waits for all to finish
    # (they won't — they're infinite loops).
    await asyncio.gather(
        sensor_loop(sensors, infer),
        led_loop(leds),
        server.start_server(host=config["server"]["host"], port=config["server"]["port"]),
    )
 
 
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")