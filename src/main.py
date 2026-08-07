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
from src.sensing.weather import WeatherSensor
from src.intelligence import rules
from src.intelligence.activation import ActivationTracker
from src.intelligence.audio_moments import AudioMomentTracker
from src.intelligence import palette_jobs
from src.net import network
from src.output.leds import LEDStrip
from src.output.dmx import DMXInterface
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
    if sensors_config["weather"]["enabled"]:
        weather_config = sensors_config["weather"]
        sensors["weather"] = WeatherSensor(
            latitude=weather_config["latitude"],
            longitude=weather_config["longitude"],
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


async def sensor_loop(sensors, infer, activation_tracker, hr_tracker, motion_tracker, audio_moment_tracker):
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
    their own dedicated LED regions, not ambient presence. audio_moment_tracker
    is the same idea for a laughter/applause/cheering/music moment (see
    intelligence/audio_moments.py) - also fed raw, not smoothed, so the
    ambient zone's ripple overlay reacts the instant a scene's detected.
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
        audio_ripple = audio_moment_tracker.update(raw.get("audio_scene"), raw.get("audio_scene_score", 0.0), now)

        smoothed = _smooth_readings(smoothed, raw, alpha)

        # How far indoor conditions have drifted from outdoor - magnitude
        # only (not signed), since _resolve_one_source's {path, min, max}
        # rescale always clamps to unsigned 0..1 (see main.py's own
        # docstring on it). Only set when both readings are actually
        # present (weather.py's WeatherSensor always returns something,
        # real or mocked, once enabled - so this is really just "weather
        # sensor enabled at all") - a zone referencing this path when it's
        # absent just resolves to 0.0 via the same fail-soft path every
        # other missing source already takes, so this is a no-op rather
        # than an error if weather's disabled.
        if "temperature" in smoothed and "outdoor_temperature" in smoothed:
            smoothed["indoor_outdoor_temp_diff"] = abs(smoothed["temperature"] - smoothed["outdoor_temperature"])

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
        server.latest["interactions"] = {"motion_burst": motion_burst, "audio_ripple": audio_ripple}
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


def _led_zone_pixel_ranges(zones_config: list, num_pixels: int) -> dict:
    """{zone_name: (start, end), ...} for zones whose output.type is "led" -
    a "dmx" zone has no position on the strip, so it's skipped here (and
    doesn't consume any of num_pixels). In config order. If the configured
    led-zone pixel counts don't sum to num_pixels, pad/clamp the last led
    zone so the strip is always exactly filled - a config typo shouldn't
    crash the installation."""
    led_zones = [zone for zone in zones_config if zone["output"]["type"] == "led"]
    ranges = {}
    start = 0
    for i, zone in enumerate(led_zones):
        is_last = i == len(led_zones) - 1
        end = num_pixels if is_last else min(num_pixels, start + zone["output"]["pixels"])
        ranges[zone["name"]] = (start, end)
        start = end
    if start != num_pixels:
        print(f"[output_loop] led-zone pixel counts sum to {start}, not num_pixels={num_pixels} — last led zone padded/clamped to fit")
    return ranges


def _fixture_channel_values(channels_layout: list, rgb) -> list:
    """Map one (r,g,b) triple to a DMX zone's fixture channel layout (its
    output.channels config list, e.g. ["r","g","b"] or ["dimmer","r","g","b"]
    - see the fixture's own manual/DIP-switch chart for which mode it's set
    to). Roles not derived from colour default to a fixed constant:
    "dimmer" holds fully open (many fixtures show black regardless of RGB
    if their master dimmer channel is 0) and "strobe" holds at 0 (no
    strobe) - neither is animated, since no zone source currently drives
    them."""
    r, g, b = (int(c) for c in rgb)
    values = []
    for role in channels_layout:
        if role == "r":
            values.append(r)
        elif role == "g":
            values.append(g)
        elif role == "b":
            values.append(b)
        elif role == "w":
            values.append(min(r, g, b))  # crude RGB->W: brightness shared across all three, not a true colour-mixing model
        elif role == "dimmer":
            values.append(255)
        elif role == "strobe":
            values.append(0)
        else:
            values.append(0)
    return values


def _dmx_zone_pixel_count(output: dict) -> int:
    """Segment count for a `dmx` zone - defaults to 1 (a single-colour
    fixture/whole-bar mode) but a segment-addressable fixture (confirmed via
    tools/test_dmx.py - see the fixture's own manual/mode, e.g. a bar whose
    manual advertises "24 channel mode = 8 segments x RGB") sets
    output.pixels to N, same field name/meaning as an `led` zone's pixels -
    it's still "how many independently-addressable points of light does
    this zone have", just realised as N consecutive 3-channel DMX groups
    instead of N strip pixels."""
    return output.get("pixels", 1)


async def output_loop(leds, dmx, num_pixels, zones_config, brightness: float = 1.0):
    """Step every zone's selected effect once, then route each zone's frame
    to whichever hardware its own `output` config points at - an `led` zone
    (output: {type: led, pixels: N}) contributes a slice of the APA102
    strip; a `dmx` zone (output: {type: dmx, start_address, channels,
    pixels: N}) is mapped into the shared 512-channel DMX universe instead,
    as N consecutive groups of `channels` starting at start_address (N
    defaults to 1 - one whole-bar point of colour - for fixtures/modes with
    no independent segments; set pixels to match a confirmed segment count
    for one that has them, see _dmx_zone_pixel_count). Replaces the old
    led_loop/dmx_loop split now that a zone's hardware target is just a
    config field on the same zone, not a reason to duplicate the whole
    effect-stepping loop - see CLAUDE.md/the conversation that led to this
    for why they used to be separate. 20 Hz.

    Every zone still runs its own effect+palette
    (server.runtime_settings["zones"][name], live-swappable from admin.html's
    Zones tab for both `led` and `dmx` zones - see the output.type=="dmx"
    branch below for how a dmx zone's frame separately reaches
    server.latest["dmx_frame"] for its own dashboard swatch, since it has no
    slot in led_frame) at one or more named intensities pulled from its own
    sensor signals (zone["source"],
    resolved each tick by _resolve_sources and passed to effect.step() as
    **kwargs). Per-zone effect instances are only rebuilt when that zone's
    (effect, palette) pair actually changes - effect objects hold animation
    state (self.t, self.trail, ...) that must survive across ticks, so
    rebuilding every tick would e.g. stop a comet's trail from ever
    accumulating.

    The browser's canvas-sampling pipeline (app.js/sketch.js/pixelMap.js,
    still sending {"pixels": [...]}) keeps running but is intentionally
    unconsumed here — kept for its own visual/demo value and as groundwork
    for a future user-sketch-upload feature, not because it's a bug.
    """
    led_ranges = _led_zone_pixel_ranges(zones_config, num_pixels)

    # Per-zone: (current_effect_name, current_palette_name, effect_instance,
    # last_frame, broken). `broken` is set when an effect's step() params
    # don't match this zone's source keys (e.g. temp_humidity_bar on a
    # zone that only defines `intensity`) - once set, that zone just holds
    # last_frame without retrying step() every tick (same TypeError every
    # time otherwise, and the same "only log on the transition into
    # failure" reasoning base.py's _mark_failed uses), until the effect
    # pick changes to something else and it's worth retrying.
    zone_state = {}
    for zone in zones_config:
        output = zone["output"]
        n = (led_ranges[zone["name"]][1] - led_ranges[zone["name"]][0]) if output["type"] == "led" else _dmx_zone_pixel_count(output)
        zone_state[zone["name"]] = (None, None, None, np.zeros((n, 3), dtype=np.uint8), False)

    while True:
        led_frames = {}  # zone name -> frame, assembled into the full strip below, in led_ranges' order
        dmx_frames = {}  # zone name -> graded frame, published for admin.js's dmx zone swatches (not part of led_frame - dmx zones have no slot there)
        dmx_universe = [0] * dmx.universe_size if dmx is not None else None

        for zone in zones_config:
            name = zone["name"]
            output = zone["output"]
            n = (led_ranges[name][1] - led_ranges[name][0]) if output["type"] == "led" else _dmx_zone_pixel_count(output)

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
                    print(f"[output_loop] zone {name!r}: effect {effect_name!r} doesn't accept sources {list(sources)} — holding last frame until the pick changes ({exc})")
                    frame = last_frame
                    broken = True

            zone_state[name] = (current_effect_name, current_palette_name, effect, frame, broken)

            if output["type"] == "led":
                led_frames[name] = frame
            elif output["type"] == "dmx":
                graded_frame = apply_gamma(frame)
                if brightness != 1.0:
                    # Same post-gamma multiplier the led strip gets below -
                    # previously only applied there, so a dmx zone never got
                    # any louder no matter how high leds.brightness was set.
                    # Clips (not rescales) for the same reason as the led
                    # path: a boosted highlight should flatten toward the
                    # fixture's actual max, not dim everything else to
                    # compensate.
                    graded_frame = np.clip(graded_frame.astype(np.float32) * brightness, 0, 255).astype(np.uint8)
                # Populated unconditionally (not just when dmx_universe is
                # real) - this is the dashboard's preview of what the zone
                # WOULD send, so it should reflect the effect running even
                # before the interface is enabled/wired up. Only the actual
                # hardware write below is gated on dmx_universe existing.
                dmx_frames[name] = graded_frame
                if dmx_universe is not None:
                    channels_layout = output["channels"]
                    # Segment i's channels sit right after segment i-1's, in
                    # the same order tools/test_dmx.py's --start probing
                    # confirms for the real fixture (e.g. 3 channels/segment:
                    # segment 0 = channels start_address..+2, segment 1 =
                    # the next 3, ...).
                    base = output["start_address"] - 1  # DMX addresses are 1-based; universe list is 0-based
                    for seg, rgb in enumerate(graded_frame):
                        values = _fixture_channel_values(channels_layout, rgb)
                        start = base + seg * len(channels_layout)
                        for i, value in enumerate(values):
                            if 0 <= start + i < len(dmx_universe):
                                dmx_universe[start + i] = value

        ordered_led_frames = [led_frames[name] for name in led_ranges]
        full_frame = np.concatenate(ordered_led_frames, axis=0) if ordered_led_frames else np.zeros((0, 3))
        graded = apply_gamma(full_frame)
        if brightness != 1.0:
            # Applied after gamma, not before - this is the final "how bright
            # does the strip actually look" scale, not a second gamma pass.
            # Clips rather than rescales so a boosted highlight can flatten
            # to solid white instead of the whole frame dimming to compensate.
            graded = np.clip(graded.astype(np.float32) * brightness, 0, 255).astype(np.uint8)
        led_frame = graded.tolist()  # numpy -> plain ints, for JSON

        leds.render_pixels(led_frame)
        server.latest["led_frame"] = led_frame
        # {zone_name: [[r,g,b], ...]} - separate from led_frame (which is one
        # flat concatenated strip) since dmx zones don't share that strip's
        # pixel space. admin.js paints each dmx zone's swatch straight from
        # here instead of slicing led_frame by offset.
        server.latest["dmx_frame"] = {name: frame.tolist() for name, frame in dmx_frames.items()}
        if dmx_universe is not None:
            dmx.send_channels(dmx_universe)

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
    # Only constructed if a DMX fixture is actually enabled - see
    # config.yaml's dmx block. Not attempted to open the serial port at all
    # otherwise, so this stays silent on setups that don't have the USB-DMX
    # interface plugged in.
    dmx = DMXInterface(port=config["dmx"]["port"]) if config["dmx"]["enabled"] else None
    infer = rules.infer_state
    activation_tracker = ActivationTracker(timeout=config["activation"]["timeout_seconds"])
    hr_tracker = ActivationTracker(timeout=config["interaction"]["hr_contact_timeout_seconds"])
    motion_tracker = ActivationTracker(timeout=config["interaction"]["motion_burst_timeout_seconds"])
    audio_moment_tracker = AudioMomentTracker()

    server.latest["leds"] = {
        "num_pixels": config["leds"]["num_pixels"],
        "layout": config["leds"]["layout"],
        # Name + pixel count + source, per zone whose output.type is "led" -
        # so the browser can build one card per zone, slice led_frame into
        # per-zone swatches, and show a live readout of what's driving each
        # zone, without duplicating the pixel-range/source-resolution logic
        # output_loop already does. "dmx" zones are kept in a separate list
        # below (dmx_zones) rather than mixed into this one: admin.js's
        # swatch slicing here walks led_frame by zone.pixels in order, and a
        # dmx zone has no slot in led_frame at all - mixing it in would
        # desync every led zone's swatch after it.
        "zones": [
            {"name": z["name"], "pixels": z["output"]["pixels"], "source": z["source"]}
            for z in config["leds"]["zones"] if z["output"]["type"] == "led"
        ],
        # Same shape, for "dmx" zones - admin.js paints these from
        # server.latest["dmx_frame"][name] instead (see output_loop),
        # live-swappable effect/palette same as led zones (set_zone_effect
        # is zone-name-generic server-side - see server.py's ADMIN_ACTIONS).
        "dmx_zones": [
            {"name": z["name"], "pixels": _dmx_zone_pixel_count(z["output"]), "source": z["source"]}
            for z in config["leds"]["zones"] if z["output"]["type"] == "dmx"
        ],
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

    tasks = [
        sensor_loop(sensors, infer, activation_tracker, hr_tracker, motion_tracker, audio_moment_tracker),
        output_loop(leds, dmx, config["leds"]["num_pixels"], config["leds"]["zones"], config["leds"]["brightness"]),
        palette_build_loop(),
        server.start_server(host=config["server"]["host"], port=config["server"]["port"]),
    ]

    try:
        # Run all tasks concurrently. gather() waits for all to finish
        # (they won't — they're infinite loops).
        await asyncio.gather(*tasks)
    finally:
        # Ctrl+C cancels this coroutine at whatever await it's sitting on -
        # that raises right here, past the gather, so this always runs
        # before the process actually exits. Without it the strip just
        # holds whatever colour it last received (APA102 chips have no
        # "power off" tied to the SPI line going quiet).
        leds.render_pixels([[0, 0, 0]] * config["leds"]["num_pixels"])
        if dmx is not None:
            dmx.blackout()
 
 
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")