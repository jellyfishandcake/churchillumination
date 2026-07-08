"""
The orchestrator. Runs three things concurrently:

  1. Sensor loop        — reads sensors, computes state, updates the shared
                          `latest` dict (which the server reads).
  2. LED output loop    — steps the selected system effect, sends it to the
                          LED strip (or prints, for now).
  3. WebSocket server   — publishes state to the browser dashboard, receives
                          canvas colours back.

All three share one asyncio event loop. Nothing blocks the others.
"""
import asyncio
import time

from src.config import load_config
from src.sensing.audio import AudioSensor
from src.sensing.motion import MotionSensor
from src.sensing.sense_hat import SenseHatSensor
from src.sensing.pir import PIRSensor
from src.sensing.heart_rate import HeartRateSensor
from src.sensing.nodes import NodeSensor
from src.intelligence import rules
from src.intelligence.activation import ActivationTracker
from src.output.leds import LEDStrip
from src.output.effects import registry
from src.output.effects.colour_palette import PALETTES
from src.output.effects.led_effects import apply_gamma
from src.intelligence import server


def build_sensors(config: dict) -> dict:
    """Build the sensor dict, keyed by the same name used in config's
    `sensors.*` block, from each `enabled` flag. Every sensor class
    auto-falls-back to a mock if its hardware/library isn't present, so
    this is safe to build the same way on a dev laptop and on the Pi —
    config only controls which sensors are wired in at all.

    Only sensors starting enabled are constructed: AudioSensor/PIRSensor
    bind real hardware (a mic stream, a GPIO pin) in __init__, not in
    .read(), so a sensor disabled here can't be turned on at runtime
    without a restart — sensor_loop's per-tick enabled check only supports
    live-disabling (and re-enabling) a sensor that started enabled."""
    sensors_config = config["sensors"]
    sensors = {}

    if sensors_config["audio"]["enabled"]:
        sensors["audio"] = AudioSensor()
    if sensors_config["motion"]["enabled"]:
        sensors["motion"] = MotionSensor()
    if sensors_config["sense_hat"]["enabled"]:
        sensors["sense_hat"] = SenseHatSensor()
    if sensors_config["pir"]["enabled"]:
        sensors["pir"] = PIRSensor(gpio_pin=sensors_config["pir"]["gpio_pin"])
    if sensors_config["heart_rate"]["enabled"]:
        sensors["heart_rate"] = HeartRateSensor()
    if sensors_config["nodes"]["enabled"]:
        nodes_config = sensors_config["nodes"]
        sensors["nodes"] = NodeSensor(
            node_ids=nodes_config["node_ids"],
            mqtt_host=nodes_config["mqtt_host"],
            mqtt_port=nodes_config["mqtt_port"],
        )

    return sensors


# Exponential moving average factor for smoothing raw sensor readings before
# they reach infer_state. Lower = calmer/slower to react to a single spike,
# higher = snappier. Without this, one loud cough or a webcam auto-exposure
# blip swings activity_level (and the mood label) instantly, since nothing
# else in rules.py remembers what a reading was a moment ago.
SMOOTHING_ALPHA = 0.15


def _smooth_readings(previous: dict, current: dict, alpha: float) -> dict:
    """EMA-smooth each numeric reading against its previous value. Non-numeric
    values and any key seen for the first time pass through unsmoothed."""
    smoothed = dict(previous)
    for key, value in current.items():
        is_numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
        previous_value = previous.get(key)
        if is_numeric and isinstance(previous_value, (int, float)):
            smoothed[key] = previous_value + alpha * (value - previous_value)
        else:
            smoothed[key] = value
    return smoothed


async def sensor_loop(sensors, infer, activation_tracker):
    """Read sensors, smooth them, compute state, publish to shared dict. 20 Hz.

    Each sensor now handles its own hardware failures internally — it falls
    back to its own mock reading and records .healthy/.last_error on itself
    (see base.py's _mark_failed/_mark_ok). So the try/except below is just a
    last-resort safety net for a bug even inside that fallback path, not the
    primary way failures get handled: without it, one such bug would still
    crash the whole asyncio.gather() in main(), taking the LED loop and the
    WebSocket server down along with the sensor that actually failed.

    activation_tracker's timeout, the smoothing alpha, and an optional
    state override are all read live from server.runtime_settings each
    tick, so the admin terminal's controls take effect without a restart.
    """
    smoothed = {}
    while True:
        activation_tracker.timeout = server.runtime_settings["activation_timeout_seconds"]
        alpha = server.runtime_settings["smoothing_alpha"]

        raw = {}
        for name, s in sensors.items():
            if not server.runtime_settings["sensors_enabled"].get(name, True):
                continue  # live-disabled via the admin terminal
            try:
                raw.update(s.read())
            except Exception as exc:
                print(f"[sensor_loop] {type(s).__name__}.read() raised even past its own fallback — skipping this tick: {exc}")

        # "activated" is derived from raw (not smoothed) presence, does not benefit from smoothing
        presence = raw.get("presence", 0.0) > 0.5
        raw["activated"] = activation_tracker.update(presence, time.time())

        smoothed = _smooth_readings(smoothed, raw, alpha)

        state = infer(smoothed)
        override = server.runtime_settings["state_override"]
        if override is not None:
            state.mood = override["mood"]
            state.activity_level = override["activity_level"]

        # Update the shared dict the server reads from
        server.latest["state"] = state.to_dict()
        # Per-sensor health, so a future error/status display can show which
        # sensors are currently running on their mock due to a real failure
        # (as opposed to simply not having that hardware configured at all).
        server.latest["sensor_health"] = {
            name: {"healthy": s.healthy, "last_error": s.last_error} for name, s in sensors.items()
        }

        await asyncio.sleep(0.05)  # 20 Hz


async def led_loop(leds, num_pixels):
    """Step the currently-selected system effect and push it to the LED
    strip. 20 Hz.

    Effect/palette selection lives in server.runtime_settings (set by the
    terminal's picker). The effect instance is only rebuilt when the
    selection actually changes — effect objects hold animation state
    (self.t, self.trail, ...) that must survive across ticks, so rebuilding
    every tick would e.g. stop the comet's trail from ever accumulating.

    The browser's canvas-sampling pipeline (app.js/sketch.js/pixelMap.js,
    still sending {"pixels": [...]}) keeps running but is intentionally
    unconsumed here — kept for its own visual/demo value and as groundwork
    for a future user-sketch-upload feature, not because it's a bug.
    """
    current_effect_name = None
    current_palette_name = None
    effect = None

    while True:
        effect_name = server.runtime_settings["effect"] or registry.DEFAULT_EFFECT
        palette_name = server.runtime_settings["palette"] or registry.DEFAULT_PALETTE

        if (effect_name, palette_name) != (current_effect_name, current_palette_name):
            effect_class = registry.EFFECTS.get(effect_name, registry.EFFECTS[registry.DEFAULT_EFFECT])
            palette = PALETTES.get(palette_name, PALETTES[registry.DEFAULT_PALETTE])
            effect = effect_class(num_pixels, palette)
            current_effect_name, current_palette_name = effect_name, palette_name

        intensity = server.latest["state"]["activity_level"]
        frame = apply_gamma(effect.step(intensity)).tolist()  # numpy -> plain ints, for JSON

        leds.render_pixels(frame)
        server.latest["led_frame"] = frame

        await asyncio.sleep(0.05)  # 20 Hz


async def main():
    config = load_config()
    sensors = build_sensors(config)
    leds = LEDStrip(num_pixels=config["leds"]["num_pixels"])
    infer = rules.infer_state
    activation_tracker = ActivationTracker(timeout=config["activation"]["timeout_seconds"])

    server.latest["leds"] = {
        "num_pixels": config["leds"]["num_pixels"],
        "layout": config["leds"]["layout"],
    }
    server.latest["effects"] = list(registry.EFFECTS.keys())
    server.latest["palettes"] = list(PALETTES.keys())
    # Full colour data (not just names), so a sketch can use the exact same
    # named palettes the Python effects use - one source of truth, not a
    # second hand-typed copy of the colours living in JS.
    server.latest["palette_data"] = dict(PALETTES)

    server.admin_passcode = config["admin"]["passcode"]
    server.runtime_settings["effect"] = config["effects"]["default_effect"]
    server.runtime_settings["palette"] = config["effects"]["default_palette"]
    server.runtime_settings["sensors_enabled"] = {
        name: cfg["enabled"] for name, cfg in config["sensors"].items()
    }
    server.runtime_settings["activation_timeout_seconds"] = config["activation"]["timeout_seconds"]
    server.runtime_settings["smoothing_alpha"] = SMOOTHING_ALPHA

    # Run all three concurrently. gather() waits for all to finish
    # (they won't — they're infinite loops).
    await asyncio.gather(
        sensor_loop(sensors, infer, activation_tracker),
        led_loop(leds, config["leds"]["num_pixels"]),
        server.start_server(host=config["server"]["host"], port=config["server"]["port"]),
    )
 
 
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")