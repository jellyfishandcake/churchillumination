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
        "num_pixels": 60,  # change this number in config for default number of pixels to drive
        "layout": "strip",
        # Physical sections of the strip - each runs its own effect+palette,
        # driven by its own sensor signal (a dot-path into server.latest,
        # resolved each tick by main.py's _resolve_source). `pixels` across
        # all zones should sum to num_pixels; led_loop pads/clamps the last
        # zone if they don't, rather than crashing over a config typo.
        "zones": [
            {"name": "ambient", "pixels": 48, "effect": "organic_wave", "palette": "winter", "source": "state.activity_level"},
            {"name": "heart_rate", "pixels": 6, "effect": "organic_twinkle", "palette": "festive", "source": "heart_rate.engaged"},
            {"name": "movement", "pixels": 6, "effect": "organic_comet", "palette": "autumn", "source": "interactions.motion_burst"},
        ],
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
        "heart_rate": {"enabled": True},
        "accel_stick": {"enabled": True, "serial_port": "/dev/ttyUSB0", "baud_rate": 115200},
        "nodes": {
            "enabled": True,
            "mqtt_host": "localhost",
            "mqtt_port": 1883,
            "node_ids": ["node1", "node2"],
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
