"""Loads config.yaml (see config.example.yaml) and merges it over sane
defaults, so main.py runs with zero config file present. A missing key at
any level just falls back to the default below it."""
import pathlib
import yaml

DEFAULTS = {
    "server": {"host": "localhost", "port": 8000},
    "leds": {"num_pixels": 60, "layout": "strip"}, # change this number in config for default number of pixels to drive
    "activation": {"timeout_seconds": 300.0},
    "effects": {"default_effect": "organic_wave", "default_palette": "winter"},
    # Shared passcode gating admin-only terminal controls (sensor toggles,
    # activation/smoothing tuning, manual state override). CHANGE THIS in
    # config.yaml before any real use - it's sent in plaintext over the
    # local websocket, a low-security gate suitable only for a private LAN.
    "admin": {"passcode": "changeme"},
    "sensors": {
        "audio": {"enabled": True},
        "motion": {"enabled": True},
        "sense_hat": {"enabled": True},
        "pir": {"enabled": True, "gpio_pin": 4},
        "heart_rate": {"enabled": True},
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
