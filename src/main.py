"""
The orchestrator. Runs four things concurrently:

  1. Sensor loop        — reads sensors, computes state, updates the shared
                          `latest` dict (which the server reads).
  2. LED output loop    — steps the selected system effect, sends it to the
                          LED strip (or prints, for now).
  3. Palette build loop — processes contribute.html photo-to-palette
                          requests one at a time, off the event loop.
  4. WebSocket server   — publishes state to the browser dashboard, receives
                          canvas colours back.

All four share one asyncio event loop. Nothing blocks the others.
"""
import asyncio
import pathlib
import time

import numpy as np
import qrcode

from src.config import load_config
from src.sensing.audio import AudioSensor
from src.sensing.motion import MotionSensor
from src.sensing.multisensor import MultisensorStick
from src.sensing.pir import PIRSensor
from src.sensing.heart_rate import HeartRateSensor
from src.sensing.accel_stick import AccelStickSensor
from src.sensing.nodes import NodeSensor
from src.intelligence import rules
from src.intelligence.activation import ActivationTracker
from src.intelligence import palette_jobs
from src.net import network
from src.output.leds import LEDStrip
from src.output.effects import registry
from src.output.effects.colour_palette import PALETTES
from src.output.effects.led_effects import apply_gamma
from src.net import server


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
    if sensors_config["multisensor"]["enabled"]:
        sensors["multisensor"] = MultisensorStick()
    if sensors_config["pir"]["enabled"]:
        sensors["pir"] = PIRSensor(gpio_pin=sensors_config["pir"]["gpio_pin"])
    if sensors_config["heart_rate"]["enabled"]:
        sensors["heart_rate"] = HeartRateSensor()
    if sensors_config["accel_stick"]["enabled"]:
        accel_config = sensors_config["accel_stick"]
        sensors["accel_stick"] = AccelStickSensor(
            serial_port=accel_config["serial_port"],
            baud_rate=accel_config["baud_rate"],
        )
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

# Raw acceleration above this (from the handheld accel_stick) counts as a
# "shake" for motion_tracker below - same idea as SMOOTHING_ALPHA, a plain
# module constant rather than config, tune by feel once real hardware is in
# hand.
MOTION_BURST_THRESHOLD = 0.15


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


async def sensor_loop(sensors, infer, activation_tracker, hr_tracker, motion_tracker):
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

    hr_tracker/motion_tracker are separate, isolated ActivationTracker
    instances (heart-rate contact, handheld-stick shake) - deliberately not
    folded into activation_tracker's ambient "activated" or into
    infer_state, since these are direct per-sensor interaction signals for
    their own dedicated LED regions, not ambient presence.
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

        # "activated" is derived from raw (not smoothed) presence, does not benefit
        # from smoothing. Presence at any of the 3 PIRs (central, via pir.py, plus
        # the 2 node-mounted ones nested under raw["nodes"][node_id]["presence"])
        # keeps the installation activated, not just the central one.
        central_presence = raw.get("presence", 0.0) > 0.5
        node_presence = any(
            node_reading.get("presence", 0.0) > 0.5
            for node_reading in raw.get("nodes", {}).values()
        )
        presence = central_presence or node_presence
        now = time.time()
        raw["activated"] = activation_tracker.update(presence, now)

        # Isolated interaction signals - fed from raw, same reasoning as
        # "activated" above: a debounce needs the real, un-smoothed edge to
        # trigger on, not an EMA-lagged one.
        hr_engaged = hr_tracker.update(raw.get("pulse_detected", False), now)
        motion_burst = motion_tracker.update(raw.get("acceleration", 0.0) > MOTION_BURST_THRESHOLD, now)

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
        # bpm smoothed for a steadier bulb pulse rate; pulse_detected itself
        # stays raw going into hr_tracker above, same as PIR presence does.
        server.latest["heart_rate"] = {"bpm": smoothed.get("heart_rate"), "engaged": hr_engaged}
        server.latest["interactions"] = {"motion_burst": motion_burst}
        # Every sensor's smoothed reading, flat (see e.g. pir.py's docstring
        # on why sensors share one flat namespace, distinct keys). Lets a
        # zone's `source` reference any raw reading (e.g.
        # "sensors.temperature") without main.py needing to hand-curate a
        # publish point per field the way heart_rate/interactions above do.
        server.latest["sensors"] = smoothed

        await asyncio.sleep(0.05)  # 20 Hz


def _resolve_one_source(latest: dict, spec) -> float:
    """Walk a dot-path (e.g. "heart_rate.engaged") into server.latest and
    return a 0..1 value. `spec` is either the dot-path string directly, or
    a {"path", "min", "max"} dict - the latter linearly rescales a reading
    that isn't already 0..1 (e.g. temperature in °C) before clamping.
    Missing path -> 0.0 (fail soft, e.g. a zone configured before its
    source sensor's first tick has landed); bool -> 1.0/0.0. Doesn't
    validate that an unscaled path "makes sense" as 0..1 (e.g. bpm isn't) -
    the zone config is trusted the same way config.py's _deep_merge trusts
    config.yaml."""
    if isinstance(spec, dict):
        path, lo, hi = spec["path"], spec.get("min"), spec.get("max")
    else:
        path, lo, hi = spec, None, None

    value = latest
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return 0.0
        value = value[part]

    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if not isinstance(value, (int, float)):
        return 0.0

    value = float(value)
    if lo is not None and hi is not None and hi != lo:
        value = (value - lo) / (hi - lo)
    return min(max(value, 0.0), 1.0)


def _resolve_sources(latest: dict, source_map: dict) -> dict:
    """{name: 0..1 value, ...} for every named source a zone declares -
    see _resolve_one_source. Keys must match the zone's chosen effect's
    step() parameter names (led_loop calls effect.step(**sources))."""
    return {name: _resolve_one_source(latest, spec) for name, spec in source_map.items()}


def _zone_pixel_ranges(zones: list, num_pixels: int) -> list:
    """[(start, end), ...] per zone, in config order. If the configured
    pixel counts don't sum to num_pixels, pad/clamp the last zone so the
    strip is always exactly filled - a config typo shouldn't crash the
    installation."""
    ranges = []
    start = 0
    for i, zone in enumerate(zones):
        is_last = i == len(zones) - 1
        end = num_pixels if is_last else min(num_pixels, start + zone["pixels"])
        ranges.append((start, end))
        start = end
    if start != num_pixels:
        print(f"[led_loop] zone pixel counts sum to {start}, not num_pixels={num_pixels} — last zone padded/clamped to fit")
    return ranges


async def led_loop(leds, num_pixels, zones_config):
    """Step each zone's selected effect and push the combined frame to the
    LED strip. 20 Hz.

    zones_config (config["leds"]["zones"]) splits the physical strip into
    named sections, each running its own effect+palette
    (server.runtime_settings["zones"][name], set from admin.html's Zones
    tab - the public dashboard is read-only, see web/index.html) at one or
    more named intensities pulled from its own sensor signals
    (zone["source"], resolved each tick by _resolve_sources and passed to
    effect.step() as **kwargs) - not one global activity_level shared by
    the whole strip. Per-zone effect instances are only rebuilt when that
    zone's (effect, palette) pair actually changes - same reasoning as
    before: effect objects hold animation state (self.t, self.trail, ...)
    that must survive across ticks, so rebuilding every tick would e.g.
    stop a comet's trail from ever accumulating.

    All zone frames are concatenated into one array before gamma
    correction + a single render_pixels call, since it's still one
    physical strip either way.

    The browser's canvas-sampling pipeline (app.js/sketch.js/pixelMap.js,
    still sending {"pixels": [...]}) keeps running but is intentionally
    unconsumed here — kept for its own visual/demo value and as groundwork
    for a future user-sketch-upload feature, not because it's a bug.
    """
    ranges = _zone_pixel_ranges(zones_config, num_pixels)

    # Per-zone: (current_effect_name, current_palette_name, effect_instance,
    # last_frame, broken). `broken` is set when an admin picks an effect
    # whose step() params don't match this zone's source keys (e.g.
    # temp_humidity_matrix on a zone that only defines `intensity`) - once
    # set, that zone just holds last_frame without retrying step() every
    # tick (same TypeError every time otherwise, and the same "only log on
    # the transition into failure" reasoning base.py's _mark_failed uses),
    # until the picker changes to something else and it's worth retrying.
    zone_state = {
        zone["name"]: (None, None, None, np.zeros((end - start, 3), dtype=np.uint8), False)
        for zone, (start, end) in zip(zones_config, ranges)
    }

    while True:
        frames = []
        for zone, (start, end) in zip(zones_config, ranges):
            name = zone["name"]
            n = end - start
            settings = server.runtime_settings["zones"].get(name, {})
            effect_name = settings.get("effect") or zone["effect"]
            palette_name = settings.get("palette") or zone["palette"]

            current_effect_name, current_palette_name, effect, last_frame, broken = zone_state[name]
            if (effect_name, palette_name) != (current_effect_name, current_palette_name):
                effect_class = registry.EFFECTS.get(effect_name, registry.EFFECTS[registry.DEFAULT_EFFECT])
                palette = PALETTES.get(palette_name, PALETTES[registry.DEFAULT_PALETTE])
                effect = effect_class(n, palette)
                current_effect_name, current_palette_name = effect_name, palette_name
                broken = False  # a new pick deserves a fresh attempt

            sources = _resolve_sources(server.latest, zone["source"])
            if broken:
                frame = last_frame
            else:
                try:
                    frame = effect.step(**sources)
                except TypeError as exc:
                    print(f"[led_loop] zone {name!r}: effect {effect_name!r} doesn't accept sources {list(sources)} — holding last frame until the pick changes ({exc})")
                    frame = last_frame
                    broken = True

            zone_state[name] = (current_effect_name, current_palette_name, effect, frame, broken)
            frames.append(frame)

        full_frame = np.concatenate(frames, axis=0) if frames else np.zeros((0, 3))
        frame = apply_gamma(full_frame).tolist()  # numpy -> plain ints, for JSON

        leds.render_pixels(frame)
        server.latest["led_frame"] = frame

        await asyncio.sleep(0.05)  # 20 Hz


PALETTE_BUILD_POLL_SECONDS = 0.5  # not animation-critical, unlike the 20Hz loops above


async def palette_build_loop():
    """Process contribute.html palette-build requests one at a time.

    The actual work (decode photo, extract colours, maybe call Colormind)
    runs via asyncio.to_thread so it can't stall sensor_loop/led_loop or
    any connected browser's send_loop - see palette_jobs.py's own
    docstring for why that matters. PALETTES is mutated in place, not
    reassigned, since led_loop holds the same dict via its own `from
    ...colour_palette import PALETTES` - reassigning here would silently
    stop led_loop from ever seeing new palettes.
    """
    while True:
        request = server.palette_job_request
        if request is not None:
            server.palette_job_request = None
            server.latest["palette_job"] = {
                "status": "processing", "name": request["name"],
                "hex_colors": None, "error": None, "overwritten": False,
            }
            try:
                result = await asyncio.to_thread(palette_jobs.run_palette_build, request)
                PALETTES[request["name"]] = result["hex_colors"]
                server.latest["palettes"] = list(PALETTES.keys())
                server.latest["palette_data"] = dict(PALETTES)
                server.latest["palette_job"] = {
                    "status": "done", "name": request["name"],
                    "hex_colors": result["hex_colors"], "error": None,
                    "overwritten": result["overwritten"],
                }
            except Exception as exc:
                server.latest["palette_job"] = {
                    "status": "error", "name": request["name"],
                    "hex_colors": None, "error": str(exc), "overwritten": False,
                }

        await asyncio.sleep(PALETTE_BUILD_POLL_SECONDS)


async def main():
    config = load_config()
    sensors = build_sensors(config)
    leds = LEDStrip(num_pixels=config["leds"]["num_pixels"])
    infer = rules.infer_state
    activation_tracker = ActivationTracker(timeout=config["activation"]["timeout_seconds"])
    hr_tracker = ActivationTracker(timeout=config["interaction"]["hr_contact_timeout_seconds"])
    motion_tracker = ActivationTracker(timeout=config["interaction"]["motion_burst_timeout_seconds"])

    server.latest["leds"] = {
        "num_pixels": config["leds"]["num_pixels"],
        "layout": config["leds"]["layout"],
        # Name + pixel count + source per zone, so the browser can build one
        # card per zone, slice led_frame into per-zone swatches, and show a
        # live readout of what's driving each zone - without duplicating
        # the pixel-range/source-resolution logic led_loop already does.
        "zones": [{"name": z["name"], "pixels": z["pixels"], "source": z["source"]} for z in config["leds"]["zones"]],
    }
    server.latest["effects"] = list(registry.EFFECTS.keys())
    server.latest["palettes"] = list(PALETTES.keys())
    # Previously-uploaded sketches (web/sketches/*.js, see server.py's
    # upload_sketch control action) so they're still selectable on the
    # dashboard after a restart, not just for the session that uploaded them.
    sketches_dir = pathlib.Path("web/sketches")
    server.latest["sketches"] = sorted(p.stem for p in sketches_dir.glob("*.js")) if sketches_dir.is_dir() else []
    # Full colour data (not just names), so a sketch can use the exact same
    # named palettes the Python effects use - one source of truth, not a
    # second hand-typed copy of the colours living in JS.
    server.latest["palette_data"] = dict(PALETTES)

    server.admin_passcode = config["admin"]["passcode"]
    # Seeded from each zone's own default effect+palette - live-swappable
    # per zone from here on via the "set_zone_effect" control action.
    server.runtime_settings["zones"] = {
        z["name"]: {"effect": z["effect"], "palette": z["palette"]} for z in config["leds"]["zones"]
    }
    server.runtime_settings["sensors_enabled"] = {
        name: cfg["enabled"] for name, cfg in config["sensors"].items()
    }
    server.runtime_settings["activation_timeout_seconds"] = config["activation"]["timeout_seconds"]
    server.runtime_settings["smoothing_alpha"] = SMOOTHING_ALPHA

    # QR code linking straight to contribute.html, so people can scan it on
    # the monitor instead of typing a URL. Best-effort: a machine with no
    # network route at all (no interface up) can't be reached by anyone
    # else anyway, so skipping the image there is fine, not fatal.
    try:
        lan_ip = network.get_lan_ip()
        contribute_url = f"http://{lan_ip}:{config['server']['port']}/contribute.html"
        qrcode.make(contribute_url).save("web/qr.png")
        print(f"Contribute-a-palette QR code points to {contribute_url}")
    except OSError as exc:
        print(f"[main] couldn't generate QR code (no network route?): {exc}")

    try:
        # Run all four concurrently. gather() waits for all to finish
        # (they won't — they're infinite loops).
        await asyncio.gather(
            sensor_loop(sensors, infer, activation_tracker, hr_tracker, motion_tracker),
            led_loop(leds, config["leds"]["num_pixels"], config["leds"]["zones"]),
            palette_build_loop(),
            server.start_server(host=config["server"]["host"], port=config["server"]["port"]),
        )
    finally:
        # Ctrl+C cancels this coroutine at whatever await it's sitting on -
        # that raises right here, past the gather, so this always runs
        # before the process actually exits. Without it the strip just
        # holds whatever colour it last received (APA102 chips have no
        # "power off" tied to the SPI line going quiet).
        leds.render_pixels([[0, 0, 0]] * config["leds"]["num_pixels"])
 
 
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")