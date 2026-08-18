"""Loads config.yaml (see config.example.yaml) and merges it over sane
defaults, so main.py runs with zero config file present. A missing key at
any level just falls back to the default below it."""
import pathlib
import yaml

DEFAULTS = {
    # "0.0.0.0" listens on every network interface, not just the Pi itself -
    # needed so phones on the same WiFi/LAN can reach contribute.html (e.g.
    # via the QR code). Same private-LAN-only trust model as the admin
    # passcode below: fine for a closed local network, not a real security
    # boundary if ever exposed further than that.
    "server": {"host": "0.0.0.0", "port": 8000},
    "leds": {
        # Virtual/dashboard-only total across every output.type "led" zone -
        # feeds server.latest["leds"]["num_pixels"] for the browser's pixel
        # map + sketch-canvas sampling (see main.py's main()). Doesn't map to
        # one physical chain anymore - see "strips" below for that. Keep
        # this equal to the sum of every led zone's output.pixels: heart_rate
        # 60 + accelerometer 94 (4 arms, 36+20+16+22 - see
        # TriArmGlideEffect.ARM_LENGTHS). temp_humidity is a DMX bar, not
        # counted here.
        "num_pixels": 154,
        "layout": "strip",
        # Real APA102 hardware chains - heart_rate and accelerometer are two
        # independent chains now, NOT data-connected to each other (separate
        # data+clock pin pairs, not one continuous strip spliced together
        # like an earlier plan assumed - each strip is its own LEDStrip/SPI
        # bus, see main.py's main()). Each led zone below picks one via
        # output.strip. accelerometer_strip's spi_bus: 1 (GPIO 20/21,
        # confirmed 2026-08-10 as the right pins) needs
        # `dtoverlay=spi1-3cs` added to /boot/firmware/config.txt + a reboot
        # before /dev/spidev1.0 exists - not yet confirmed done.
        # spi_speed_hz defaults to LEDStrip's own 8_000_000 if omitted here
        # (see main.py's build_strips) - override per-strip to test a lower
        # clock speed while diagnosing signal-integrity issues (glitching/
        # off-palette pixels) without editing source. Worth trying first if
        # a strip's data/clock lines run straight from the Pi's 3.3V GPIO
        # with no level shifter, since 8MHz is fast enough to lose margin at
        # 3.3V into an ostensibly-5V-logic part - see 2026-08-18 debugging.
        "strips": {
            "heart_rate_strip": {"num_pixels": 60, "spi_bus": 0, "spi_device": 0},
            "accelerometer_strip": {"num_pixels": 94, "spi_bus": 1, "spi_device": 0},
        },
        # Final output multiplier, applied after gamma correction (see
        # led_effects.apply_gamma) right before pixels go to hardware. The
        # APA102 driver's own per-pixel brightness byte is already left at
        # its hardware max (see output/leds.py) - that's a 5-bit field with
        # far less headroom than the 8-bit RGB channels, so there's nothing
        # left to raise there. This is the actual "make it brighter" knob:
        # >1.0 boosts (clipped to 255 per channel, so very bright pixels
        # can clip/flatten rather than keep scaling), 1.0 leaves effects'
        # own output untouched, <1.0 dims everything. Applies to `dmx` zones
        # too (as of the multi-segment output_loop rework), same post-gamma
        # clip-not-rescale behaviour - a fixture's own dimmer channel (if
        # its mode has one, see its `channels` config) stacks on top of
        # this, not instead of it.
        "brightness": 1.4,
        # Named sections of the installation, each running its own
        # effect+palette, driven by one or more named sensor signals (see
        # each zone's `source` dict - values are either a dot-path into
        # server.latest, or {path, min, max} to linearly rescale a
        # not-already-0..1 reading like temperature). Resolved each tick by
        # main.py's _resolve_sources and passed to the zone's effect as
        # **kwargs, so a source dict's keys must match the chosen effect's
        # step() parameter names.
        #
        # Each zone's `output` says which hardware it actually drives:
        #   {type: led, strip: name, pixels: N}   - a slice of one of the
        #     independent APA102 chains in "strips" above (`strip` must be a
        #     key there). `pixels` across all `led` zones sharing one strip
        #     should sum to that strip's own num_pixels; output_loop
        #     pads/clamps the last zone on that strip if they don't, rather
        #     than crashing over a config typo. Zones sharing a strip are
        #     modular/reorderable panels (detachable clips) on one
        #     continuous electrical chain, so reordering them physically
        #     just means reordering those zones in this list - but zones on
        #     *different* strips are never electrically connected at all.
        #   {type: dmx, start_address: N, channels: [...], pixels: N} - a
        #     DMX512 fixture (e.g. an RGB/RGBW wall-washer bar) over a
        #     USB-DMX interface, driven in parallel with the strip - see
        #     output/dmx.py and main.py's output_loop. Needs dmx.enabled
        #     (below) set true, and the fixture's real start_address +
        #     channels checked against its own manual/DIP-switch chart.
        #     `pixels` (default 1) is how many independently-addressable
        #     segments the fixture has in its current mode - each one gets
        #     its own consecutive block of `channels`, same "how many points
        #     of light" meaning as an `led` zone's pixels. Confirm the real
        #     count/spacing with tools/test_dmx.py's --start flag before
        #     setting this above 1 - don't assume from a spec sheet alone.
        "zones": [
            {
                "name": "ambient",
                "effect": "audio_reactive_wave", "palette": "winter",
                "source": {
                    "loudness": "sensors.loudness",
                    "motion": "sensors.motion",
                    "env_brightness": {"path": "sensors.lux", "min": 0, "max": 500},
                    "ripple": "interactions.audio_ripple",
                },
                "output": {"type": "dmx", "start_address": 1, "channels": ["r", "g", "b"], "pixels": 8},
            },
            {
                "name": "temp_humidity",
                # Was temp_humidity_matrix on a spatial LED panel idea - the
                # physical build settled on a DMX bar instead (RGBW) - see
                # led_effects.py's TempHumidityBarEffect docstring. Rebuilt
                # 2026-08-18 into a three-layer ambient/shimmer/touch design.
                # start_address 25 confirmed 2026-08-11 by setting the
                # fixture's own display to match, same as ambient's address
                # once was. pixels: 28 is still the original 2026-08-07
                # segment-count guess, not yet re-confirmed now that the
                # fixture's actually addressable - watch for a dark "front
                # bit" beyond the addressed range (tools/test_dmx.py bar
                # --count) before trusting this number.
                "effect": "temp_humidity_bar", "palette": "winter",
                "source": {
                    "temperature": {"path": "sensors.temperature", "min": 15, "max": 30},
                    "contrast": {"path": "sensors.indoor_outdoor_temp_diff", "min": 0, "max": 15},
                    "condition": {"path": "sensors.outdoor_condition_code", "min": 0, "max": 5},
                },
                "output": {"type": "dmx", "start_address": 25, "channels": ["r", "g", "b", "w"], "pixels": 28},
            },
            {
                "name": "heart_rate",
                "effect": "heart_rate_check_in", "palette": "festive",
                # bpm's {min, max} must match HeartRateEffect.BPM_RANGE in
                # led_effects.py - this is what turns the 0..1 _resolve_one_source
                # gives every source back into the real BPM the effect flashes at.
                "source": {
                    "intensity": "heart_rate.engaged",
                    "bpm": {"path": "heart_rate.bpm", "min": 40, "max": 180},
                },
                "output": {"type": "led", "strip": "heart_rate_strip", "pixels": 60},  # 1m strip, confirmed 2026-08-07
            },
            {
                "name": "accelerometer",
                "effect": "tri_arm_glide", "palette": "autumn",
                # angle rescaled the same way temperature/humidity are -
                # accel_stick's raw [0, 360) swing angle maps to 0..1, see
                # TriArmGlideEffect's docstring. This used to be a single-
                # pixel DMX fixture (DirectionalWaveEffect) - now a
                # continuous-strip `led` zone instead, on its own independent
                # chain (accelerometer_strip, SPI1) - NOT data-connected to
                # heart_rate, since the physical design moved to arms
                # radiating from a shared hub rather than one linear bar and
                # the two zones ended up wired as two separate chains rather
                # than one spliced-together strip.
                "source": {
                    "intensity": "sensors.acceleration",
                    "angle": {"path": "sensors.angle_deg", "min": 0, "max": 360},
                },
                # 94 = 36+20+16+22, confirmed 2026-08-10 post-soldering: 4
                # arms (design changed from the original 3-arm idea),
                # unequal lengths, angles 20/45/90/135 (approximate, arm 3
                # straight up), arms 2+4 wired tip-to-hub - see
                # TriArmGlideEffect.ARM_LENGTHS/ARM_ANGLES_DEG/ARM_REVERSED.
                "output": {"type": "led", "strip": "accelerometer_strip", "pixels": 94},
            },
        ],
    },
    # Interface-level DMX512 settings - which zones actually send to it, and
    # each fixture's start_address/channels, are configured per-zone above
    # (leds.zones[].output). Off by default since this is bring-up/test gear
    # (a borrowed fixture, an unconfirmed interface) - flip `enabled` once
    # `python -m tools.test_dmx` confirms the interface talks to the
    # fixture. See output/dmx.py.
    "dmx": {
        "enabled": False,
        "port": None,  # null = auto-detect an FTDI/CH340 serial port; set e.g. "COM5" if that picks the wrong device
    },
    "activation": {"timeout_seconds": 300.0},
    # Isolated interaction signals (heart-rate contact, handheld-stick
    # shake) - short timeouts since these are direct momentary interactions,
    # not ambient presence. See ActivationTracker/main.py's sensor_loop.
    "interaction": {"hr_contact_timeout_seconds": 5.0, "motion_burst_timeout_seconds": 8.0},
    # Shared passcode gating admin-only terminal controls (sensor toggles,
    # activation/smoothing tuning, manual state override). CHANGE THIS in
    # config.yaml before any real use - it's sent in plaintext over the
    # local websocket, a low-security gate suitable only for a private LAN.
    "admin": {"passcode": "changeme"},
    "sensors": {
        "audio": {"enabled": True},
        "motion": {"enabled": True},
        "multisensor": {"enabled": True},
        "pir": {"enabled": True, "gpio_pin": 4},
        # Waveshare HMMD mmWave presence sensor (S3KM1110, 24GHz FMCW radar)
        # - an alternative to pir.py's PIR, being evaluated 2026-08-14 since
        # PIR only fires on movement (a stationary person can "disappear"
        # from presence). NOT meant to run alongside pir above - both
        # publish the same "presence" key, see mmwave.py's docstring.
        # Defaults disabled since PIR is what's actually wired right now -
        # flip pir.enabled off and this on once the mmWave unit's connected.
        "mmwave": {"enabled": False, "gpio_pin": 27},
        "heart_rate": {"enabled": True},
        "accel_stick": {"enabled": True, "serial_port": "/dev/ttyUSB0", "baud_rate": 115200},
        "nodes": {
            "enabled": True,
            "mqtt_host": "localhost",
            "mqtt_port": 1883,
            "node_ids": ["node1", "node2"],
        },
        # Outdoor weather via Open-Meteo (free, no API key needed) - see
        # src/sensing/weather.py. Defaults to Churchill College, Cambridge;
        # override in config.yaml if the install site ever moves.
        "weather": {
            "enabled": True,
            "latitude": 52.2153,
            "longitude": 0.0983,
        },
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge `override` into `base`, recursing into nested dicts. `base` is
    not mutated; a new merged dict is returned."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: str = "config.yaml") -> dict:
    config_path = pathlib.Path(path)
    if not config_path.is_file():
        return DEFAULTS
    with config_path.open("r") as f:
        user_config = yaml.safe_load(f) or {}
    return _deep_merge(DEFAULTS, user_config)
