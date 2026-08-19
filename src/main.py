"""
The orchestrator. Runs, in parallel:

  1. Sensor producer threads — one per sensor (I2C-bus sensors grouped onto
                          one shared thread, see src/sensing/producers.py) -
                          each blocks on its own hardware at its own natural
                          rate and writes its latest reading into a shared
                          dict. Never touches server.latest directly.
  2. Render loop         — the ONE loop that's protected: plain synchronous
                          code on the main thread, never awaits, never
                          blocks on I/O it doesn't control. Each tick, reads
                          a snapshot of the sensor producers' shared dict,
                          does the (cheap) smoothing/state-inference work
                          sensor_loop used to do, then steps every zone's
                          effect and writes the result to real hardware
                          (LED strips over SPI, DMX over serial).
  3. Background asyncio thread — the websocket server (dashboard push +
                          admin controls) and the palette-build job queue
                          both need asyncio (the `websockets` library), so
                          they share one event loop together, running on a
                          single background thread instead of on the main
                          thread. Neither one is on the render loop's
                          protected path.

Every thread/loop talks to the others ONLY through a small number of shared
dicts (server.latest, server.runtime_settings, producers.raw) - one writer
per key, no queues, no locks. See src/sensing/producers.py's own docstring
for why no lock is needed there, and server.py's module docstring for the
same reasoning on server.latest/runtime_settings. This replaces an earlier
all-asyncio design (every one of the above sharing a single asyncio event
loop) - see git history/CLAUDE.md for why: a blocking call ANYWHERE in that
design (a slow sensor read, a debug print(), a slow websocket send) stalled
literally everything at once, including LED output, which is the one thing
in this whole system that most needs to stay smooth.
"""
import asyncio
import concurrent.futures
import pathlib
import threading
import time

import numpy as np
import qrcode

from src.config import load_config
from src.sensing.audio import AudioSensor
from src.sensing.motion import MotionSensor
from src.sensing.multisensor import MultisensorStick
from src.sensing.pir import PIRSensor
from src.sensing.mmwave import MMWaveSensor
from src.sensing.heart_rate import HeartRateSensor
from src.sensing.accel_stick import AccelStickSensor
from src.sensing.nodes import NodeSensor
from src.sensing.weather import WeatherSensor
from src.sensing import producers
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
    without a restart — each producer's own per-tick enabled check (see
    producers.py) only supports live-disabling (and re-enabling) a sensor
    that started enabled."""
    sensors_config = config["sensors"]
    sensors = {}

    if sensors_config["audio"]["enabled"]:
        sensors["audio"] = AudioSensor()
    if sensors_config["motion"]["enabled"]:
        motion_config = sensors_config["motion"]
        sensors["motion"] = MotionSensor(
            motion_range=(motion_config["motion_range_low"], motion_config["motion_range_high"]),
            human_temp_range=(motion_config["human_temp_range_low"], motion_config["human_temp_range_high"]),
        )
    if sensors_config["multisensor"]["enabled"]:
        sensors["multisensor"] = MultisensorStick()
    if sensors_config["pir"]["enabled"]:
        sensors["pir"] = PIRSensor(gpio_pin=sensors_config["pir"]["gpio_pin"])
    if sensors_config["mmwave"]["enabled"]:
        # Alternative to pir.py's PIRSensor, not meant to run alongside it -
        # both publish the same "presence" key, see mmwave.py's docstring.
        sensors["mmwave"] = MMWaveSensor(gpio_pin=sensors_config["mmwave"]["gpio_pin"])
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

# The render loop's target tick period - both the old sensor_loop and
# output_loop independently targeted 20Hz; now there's one fused loop, so
# there's one constant. Scheduled against an absolute clock (see
# render_loop's own pacing step at the end of its while loop), not a flat
# post-work sleep - a flat sleep makes the real tick period drift by however
# long that tick's own work took, which reads as uneven micro-stutter even
# though led_effects.py's dt-based scaling keeps the average animation
# speed correct regardless.
RENDER_TICK_SECONDS = 0.05


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


def _resolve_one_source(latest: dict, spec):
    """Walk a dot-path (e.g. "heart_rate.engaged") into server.latest and
    return a 0..1 value. `spec` is either the dot-path string directly, or
    a {"path", "min", "max"} dict - the latter linearly rescales a reading
    that isn't already 0..1 (e.g. temperature in °C) before clamping.
    Missing path -> 0.0 (fail soft, e.g. a zone configured before its
    source sensor's first tick has landed); bool -> 1.0/0.0. Doesn't
    validate that an unscaled path "makes sense" as 0..1 (e.g. bpm isn't) -
    the zone config is trusted the same way config.py's _deep_merge trusts
    config.yaml.

    {"path", "raw": true} skips all of the above and returns the value at
    that path completely unchanged (missing path -> None instead of 0.0,
    since None unambiguously means "nothing there yet" for a shape no
    numeric fallback could stand in for) - for a source that isn't a single
    0..1 reading at all, e.g. weather's `history` (a whole list of
    past readings for TempHumidityBarEffect's replay, not one value to
    rescale). Every other zone's sources stay plain floats; this is an
    escape hatch for the one effect that needs structured data, not a
    general mechanism most zones should reach for."""
    if isinstance(spec, dict):
        if spec.get("raw"):
            value = latest
            for part in spec["path"].split("."):
                if not isinstance(value, dict) or part not in value:
                    return None
                value = value[part]
            return value
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
    see _resolve_one_source (a `raw: true` source is the one exception,
    passed through unrescaled - still keyed the same way). Keys must match
    the zone's chosen effect's step() parameter names (render_loop calls
    effect.step(**sources))."""
    return {name: _resolve_one_source(latest, spec) for name, spec in source_map.items()}


def _led_zone_pixel_ranges(zones_config: list, strips: dict) -> dict:
    """{zone_name: (strip_name, start, end), ...} for zones whose output.type
    is "led" - a "dmx" zone has no position on any strip, so it's skipped
    here. heart_rate and accelerometer are on two independent APA102 chains
    now (separate data+clock pin pairs, not one continuous strip spliced
    together - see leds.strips in config.py/config.yaml), so ranges are
    computed per strip, not globally: each strip fills its own zones in
    config order, and if that strip's zones' pixel counts don't sum to its
    own num_pixels, the last zone on THAT strip is padded/clamped so the
    strip is always exactly filled - a config typo shouldn't crash the
    installation. A zone whose output.strip isn't a real entry in `strips`
    is dropped (warned once) rather than crashing on a bad lookup - same
    "config typo shouldn't crash" reasoning."""
    led_zones = [zone for zone in zones_config if zone["output"]["type"] == "led"]
    zones_by_strip = {}
    for zone in led_zones:
        strip_name = zone["output"].get("strip")
        if strip_name is None:
            print(f"[render_loop] zone {zone['name']!r} has no output.strip set (old single-strip config?) - dropped, add a strip: <name> matching one of leds.strips to config.yaml")
            continue
        if strip_name not in strips:
            print(f"[render_loop] zone {zone['name']!r} references unknown strip {strip_name!r} - dropped, check leds.strips/leds.zones agree in config.yaml")
            continue
        zones_by_strip.setdefault(strip_name, []).append(zone)

    ranges = {}
    for strip_name, zones in zones_by_strip.items():
        num_pixels = strips[strip_name].num_pixels
        start = 0
        for i, zone in enumerate(zones):
            is_last = i == len(zones) - 1
            end = num_pixels if is_last else min(num_pixels, start + zone["output"]["pixels"])
            ranges[zone["name"]] = (strip_name, start, end)
            start = end
        if start != num_pixels:
            print(f"[render_loop] strip {strip_name!r}: led-zone pixel counts sum to {start}, not num_pixels={num_pixels} — last zone on this strip padded/clamped to fit")
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


def render_loop(sensors, infer, activation_tracker, hr_tracker, motion_tracker, audio_moment_tracker,
                 strips, dmx, zones_config, dmx_executor, brightness: float = 1.0) -> None:
    """The ONE protected loop - plain synchronous code on the main thread,
    never awaits, never calls anything that blocks on I/O it doesn't
    control. Runs forever at RENDER_TICK_SECONDS until KeyboardInterrupt.

    Fuses what used to be two separate asyncio coroutines (sensor_loop +
    output_loop) into one function, in this order, every tick:

      1. Snapshot producers.raw (see src/sensing/producers.py) - every
         sensor's latest reading, written by its own dedicated thread. This
         is the ONLY place render_loop touches sensor data; it never calls
         .read() on a Sensor itself (that's producers.py's job) and so can
         never be blocked by one.
      2. The same (cheap - confirmed by reading rules.py/activation.py/
         audio_moments.py, all pure in-memory arithmetic, no I/O) smoothing
         + state-inference + activation-tracking work sensor_loop used to
         do, now inline here instead of a separate coroutine.
      3. Publish the result to server.latest (unchanged shape/keys).
      4. Step every zone's effect and write the composited frame to real
         hardware - unchanged from the old output_loop, verbatim.

    `sensors` is only used here for reading .healthy/.last_error for the
    dashboard's sensor-health display (a plain unsynchronized attribute on
    each Sensor - safe to read cross-thread from whichever producer thread
    last wrote it, same as today) - never for .read().
    """
    led_ranges = _led_zone_pixel_ranges(zones_config, strips)

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
        if output["type"] == "led":
            n = (led_ranges[zone["name"]][2] - led_ranges[zone["name"]][1]) if zone["name"] in led_ranges else 0
        else:
            n = _dmx_zone_pixel_count(output)
        zone_state[zone["name"]] = (None, None, None, np.zeros((n, 3), dtype=np.uint8), False)

    # Real elapsed time passed into every effect's step() as `dt` (see
    # led_effects.py's ASSUMED_TICK_SECONDS module docstring for why) -
    # measured here, not assumed. Clamped so a single unusually slow tick
    # (or the very first one) can't make an effect suddenly jump forward
    # several beats at once instead of just running a bit fast to catch up.
    DT_MAX_SECONDS = 0.5
    _last_tick_time = time.monotonic()

    # EMA-smoothed sensor readings, carried across ticks - local to this
    # single thread, so (unlike producers.raw) needs no synchronization at
    # all: nothing else ever reads or writes this dict.
    smoothed = {}

    # Absolute-clock pacing target (see RENDER_TICK_SECONDS's own comment
    # on why this replaced a flat post-work sleep).
    next_tick = time.monotonic()

    while True:
        # --- 1: snapshot every sensor's latest reading ---
        raw = dict(producers.raw)

        # --- 2: the processing sensor_loop used to do, now inline ---
        activation_tracker.timeout = server.runtime_settings["activation_timeout_seconds"]
        alpha = server.runtime_settings["smoothing_alpha"]

        # "activated" is derived from raw (not smoothed) presence, does not
        # benefit from smoothing. Presence at any of the 3 PIRs (central,
        # via pir.py, plus the 2 node-mounted ones nested under
        # raw["nodes"][node_id]["presence"]) keeps the installation
        # activated, not just the central one.
        central_presence = raw.get("presence", 0.0) > 0.5
        node_presence = any(
            node_reading.get("presence", 0.0) > 0.5
            for node_reading in raw.get("nodes", {}).values()
        )
        presence = central_presence or node_presence
        wall_now = time.time()
        raw["activated"] = activation_tracker.update(presence, wall_now)

        # Isolated interaction signals - fed from raw, same reasoning as
        # "activated" above: a debounce needs the real, un-smoothed edge to
        # trigger on, not an EMA-lagged one.
        hr_engaged = hr_tracker.update(raw.get("pulse_detected", False), wall_now)
        motion_burst = motion_tracker.update(raw.get("acceleration", 0.0) > MOTION_BURST_THRESHOLD, wall_now)
        audio_ripple = audio_moment_tracker.update(raw.get("audio_scene"), raw.get("audio_scene_score", 0.0), wall_now)

        smoothed = _smooth_readings(smoothed, raw, alpha)

        # How far indoor conditions have drifted from outdoor - magnitude
        # only (not signed), since _resolve_one_source's {path, min, max}
        # rescale always clamps to unsigned 0..1. Only set when both
        # readings are actually present - a zone referencing this path when
        # it's absent just resolves to 0.0 via the same fail-soft path
        # every other missing source already takes.
        if "temperature" in smoothed and "outdoor_temperature" in smoothed:
            smoothed["indoor_outdoor_temp_diff"] = abs(smoothed["temperature"] - smoothed["outdoor_temperature"])

        state = infer(smoothed)
        override = server.runtime_settings["state_override"]
        if override is not None:
            state.mood = override["mood"]
            state.activity_level = override["activity_level"]

        # --- 3: publish to server.latest (unchanged shape/keys) ---
        server.latest["state"] = state.to_dict()
        server.latest["sensor_health"] = {
            name: {"healthy": s.healthy, "last_error": s.last_error} for name, s in sensors.items()
        }
        server.latest["heart_rate"] = {"bpm": smoothed.get("heart_rate"), "engaged": hr_engaged}
        server.latest["interactions"] = {
            "motion_burst": motion_burst, "audio_ripple": audio_ripple,
            "acceleration_raw": raw.get("acceleration", 0.0),
        }
        server.latest["sensors"] = smoothed

        # --- 4: step every zone's effect, write to real hardware ---
        now = time.monotonic()
        dt = min(now - _last_tick_time, DT_MAX_SECONDS)
        _last_tick_time = now

        led_frames = {}  # zone name -> frame, assembled into the full strip below, in led_ranges' order
        dmx_frames = {}  # zone name -> graded frame, published for admin.js's dmx zone swatches (not part of led_frame - dmx zones have no slot there)
        dmx_universe = [0] * dmx.universe_size if dmx is not None else None

        for zone in zones_config:
            name = zone["name"]
            output = zone["output"]
            if output["type"] == "led":
                n = (led_ranges[name][2] - led_ranges[name][1]) if name in led_ranges else 0
            else:
                n = _dmx_zone_pixel_count(output)

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
                    frame = effect.step(dt=dt, **sources)
                except TypeError as exc:
                    print(f"[render_loop] zone {name!r}: effect {effect_name!r} doesn't accept sources {list(sources)} — holding last frame until the pick changes ({exc})")
                    frame = last_frame
                    broken = True

            zone_state[name] = (current_effect_name, current_palette_name, effect, frame, broken)

            if output["type"] == "led":
                led_frames[name] = frame
            elif output["type"] == "dmx":
                graded_frame = apply_gamma(frame)
                if brightness != 1.0:
                    graded_frame = np.clip(graded_frame.astype(np.float32) * brightness, 0, 255).astype(np.uint8)
                if brightness != 1.0:
                    preview_frame = np.clip(frame.astype(np.float32) * brightness, 0, 255).astype(np.uint8)
                else:
                    preview_frame = frame
                dmx_frames[name] = preview_frame
                if dmx_universe is not None:
                    channels_layout = output["channels"]
                    base = output["start_address"] - 1  # DMX addresses are 1-based; universe list is 0-based
                    for seg, rgb in enumerate(graded_frame):
                        values = _fixture_channel_values(channels_layout, rgb)
                        start = base + seg * len(channels_layout)
                        for i, value in enumerate(values):
                            if 0 <= start + i < len(dmx_universe):
                                dmx_universe[start + i] = value

        ordered_led_zone_names = list(led_ranges.keys())  # config order, same order dashboard swatches assume
        ordered_led_frames = [led_frames[name] for name in ordered_led_zone_names]
        full_frame = np.concatenate(ordered_led_frames, axis=0) if ordered_led_frames else np.zeros((0, 3))
        graded = apply_gamma(full_frame)
        if brightness != 1.0:
            graded = np.clip(graded.astype(np.float32) * brightness, 0, 255).astype(np.uint8)
        hardware_frame = graded.tolist()  # numpy -> plain ints - gamma-corrected, for the real strips only

        if brightness != 1.0:
            preview = np.clip(full_frame.astype(np.float32) * brightness, 0, 255).astype(np.uint8)
        else:
            preview = full_frame.astype(np.uint8)
        led_frame = preview.tolist()  # dashboard-facing, one flat array same as before the multi-strip split

        strip_buffers = {name: [[0, 0, 0]] * strip.num_pixels for name, strip in strips.items()}
        offset = 0
        for name in ordered_led_zone_names:
            strip_name, local_start, local_end = led_ranges[name]
            length = local_end - local_start
            strip_buffers[strip_name][local_start:local_end] = hardware_frame[offset:offset + length]
            offset += length

        # DMXInterface.send_channels is a blocking ~1ms break plus the wire
        # time (and often real driver-level latency well beyond that on cheap
        # USB-serial dongles) to write + flush() a full 513-byte frame every
        # call - the dominant blocking cost in this loop, well above either
        # LEDStrip.render_pixels call. Submitted to a persistent single-
        # worker executor (created once in main(), not per-tick) so it
        # overlaps with the strip writes below instead of stalling this
        # whole loop for its ~23ms+; a fresh threading.Thread per tick would
        # work too but costs a real (if small) allocation+OS call 20 times a
        # second for no benefit over reusing one worker.
        #
        # The two strip writes themselves stay plain and sequential, NOT also
        # parallelised against each other - running heart_rate_strip (SPI0)
        # and accelerometer_strip (SPI1) concurrently made accelerometer_strip
        # glitch badly (SPI1 is the Pi's simpler auxiliary SPI peripheral,
        # more sensitive to being driven at the exact same instant as
        # something else) while DMX (on entirely separate hardware) stayed
        # fine. Each strip write is only ~2-5ms anyway, so there was never a
        # real performance reason to parallelise them - the actual win is
        # always overlapping the ~23ms DMX write with the cheap strip writes,
        # which this keeps.
        dmx_future = dmx_executor.submit(dmx.send_channels, dmx_universe) if dmx_universe is not None else None

        for strip_name, strip in strips.items():
            strip.render_pixels(strip_buffers[strip_name])

        if dmx_future is not None:
            dmx_future.result()

        server.latest["led_frame"] = led_frame
        server.latest["dmx_frame"] = {name: frame.tolist() for name, frame in dmx_frames.items()}

        # --- pacing: sleep against an absolute clock, not a flat post-work
        # delay (see RENDER_TICK_SECONDS's own comment) ---
        next_tick += RENDER_TICK_SECONDS
        sleep_for = next_tick - time.monotonic()
        if sleep_for > 0:
            time.sleep(sleep_for)
        else:
            # This tick ran over budget - resync to now instead of trying
            # to "catch up" with a burst of back-to-back ticks (which would
            # just turn one slow tick into several fast ones in a row,
            # reading as its own kind of stutter). Same "a stall costs only
            # itself" philosophy as DT_MAX_SECONDS above.
            next_tick = time.monotonic()


PALETTE_BUILD_POLL_SECONDS = 0.5  # not animation-critical, unlike the render loop above


async def palette_build_loop():
    """Process contribute.html palette-build requests one at a time.

    The actual work (decode photo, extract colours, maybe call Colormind)
    runs via asyncio.to_thread so it can't stall this event loop's other
    job (the websocket server, which shares this loop - see
    _run_background_asyncio) - see palette_jobs.py's own docstring for why
    that matters. PALETTES is mutated in place, not reassigned, since
    render_loop holds the same dict via its own `from ...colour_palette
    import PALETTES` - reassigning here would silently stop render_loop
    from ever seeing new palettes."""
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


async def _background_asyncio_main(host: str, port: int) -> None:
    """The websocket server and the palette-build queue, gathered on ONE
    event loop - deliberately kept together (not split onto separate
    threads) because server.py's build_palette control action checks
    latest["palette_job"]["status"] before deciding to enqueue a new job,
    and that check-then-act is only race-free as long as nothing else can
    interleave between it and palette_build_loop's own writes to the same
    key - true as long as both stay coroutines on the same loop, same as
    today."""
    await asyncio.gather(
        server.start_server(host=host, port=port),
        palette_build_loop(),
    )


def _run_background_asyncio(host: str, port: int) -> None:
    asyncio.run(_background_asyncio_main(host, port))


def main():
    config = load_config()
    sensors = build_sensors(config)
    # heart_rate and accelerometer are two independent APA102 chains (not
    # data-connected to each other) - one LEDStrip per entry in leds.strips,
    # each opening its own SPI bus/device. See _led_zone_pixel_ranges/
    # render_loop for how each led zone's frame is routed to the right one.
    strips = {
        name: LEDStrip(
            num_pixels=cfg["num_pixels"],
            spi_bus=cfg.get("spi_bus", 0),
            spi_device=cfg.get("spi_device", 0),
            **({"spi_speed_hz": cfg["spi_speed_hz"]} if "spi_speed_hz" in cfg else {}),
        )
        for name, cfg in config["leds"]["strips"].items()
    }
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
        # render_loop already does. "dmx" zones are kept in a separate list
        # below (dmx_zones) rather than mixed into this one: admin.js's
        # swatch slicing here walks led_frame by zone.pixels in order, and a
        # dmx zone has no slot in led_frame at all - mixing it in would
        # desync every led zone's swatch after it.
        "zones": [
            {"name": z["name"], "pixels": z["output"]["pixels"], "source": z["source"]}
            for z in config["leds"]["zones"] if z["output"]["type"] == "led"
        ],
        # Same shape, for "dmx" zones - admin.js paints these from
        # server.latest["dmx_frame"][name] instead (see render_loop),
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
    # Pre-seeded here (not left for render_loop's first tick to create, the
    # way they used to be) - render_loop now runs on a different OS thread
    # than the websocket server's send_loop, which does `{**latest, ...}`
    # every tick. Inserting a brand-new key into a dict while another
    # thread is mid-copy of that same dict is a genuine CPython hazard
    # (RuntimeError: dictionary changed size during iteration); pre-seeding
    # means every write render_loop ever does to these three keys is a
    # plain re-assignment to an existing key instead, which is safe under
    # the GIL with no lock needed - same reasoning already relied on for
    # every other server.latest key.
    server.latest["sensors"] = {}
    server.latest["led_frame"] = []
    server.latest["dmx_frame"] = {}

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

    # QR codes linking straight to contribute.html/backdrop.html, so people
    # can scan them on the monitor instead of typing a URL. Best-effort: a
    # machine with no network route at all (no interface up) can't be
    # reached by anyone else anyway, so skipping the images there is fine,
    # not fatal.
    try:
        lan_ip = network.get_lan_ip()

        contribute_url = f"http://{lan_ip}:{config['server']['port']}/contribute.html"
        qrcode.make(contribute_url).save("web/qr.png")
        print(f"Contribute-a-palette QR code points to {contribute_url}")

        backdrop_url = f"http://{lan_ip}:{config['server']['port']}/backdrop.html"
        qrcode.make(backdrop_url).save("web/qr_backdrop.png")
        print(f"Shadow-backdrop QR code points to {backdrop_url}")
    except OSError as exc:
        print(f"[main] couldn't generate QR codes (no network route?): {exc}")

    # Everything above this line only ever touches server.latest/
    # runtime_settings/admin_passcode from this one thread (main(), before
    # anything else starts) - so this is the one point where seeding order
    # actually matters. Both of the following start real background
    # threads; nothing before this point may run again once they're live.
    producers.start_producers(sensors, producers.raw)
    threading.Thread(
        target=_run_background_asyncio,
        args=(config["server"]["host"], config["server"]["port"]),
        daemon=True,
        name="asyncio-services",
    ).start()

    # One persistent worker, reused every tick - see render_loop's own
    # comment on why this replaced a fresh executor submission each time.
    dmx_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="dmx-write")

    try:
        # render_loop runs forever on this thread (the process's main
        # thread) until Ctrl+C raises KeyboardInterrupt here.
        render_loop(sensors, infer, activation_tracker, hr_tracker, motion_tracker, audio_moment_tracker,
                    strips, dmx, config["leds"]["zones"], dmx_executor, config["leds"]["brightness"])
    finally:
        # Ctrl+C interrupts render_loop at whatever it's doing (almost
        # always its own time.sleep) - that raises right here, so this
        # always runs before the process actually exits. Without it a
        # strip just holds whatever colour it last received (APA102 chips
        # have no "power off" tied to the SPI line going quiet) - every
        # chain gets blanked, not just one.
        for strip in strips.values():
            strip.render_pixels([[0, 0, 0]] * strip.num_pixels)
        if dmx is not None:
            dmx.blackout()
        dmx_executor.shutdown(wait=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
