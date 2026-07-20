"""
test_sensor.py

Construct and poll ONE sensor in isolation, printing its .read() output on
a loop - for wiring up and tuning one piece of hardware at a time without
running the full pipeline (LEDs, websocket server, every other sensor).

Reuses main.py's build_sensors() rather than duplicating its per-sensor
construction logic (real hardware vs mock, config-driven kwargs like
gpio_pin) here - the only difference is every sensor except the one being
tested is temporarily marked disabled, so build_sensors() only actually
constructs the one you asked for.

Usage:
    python -m tools.test_sensor pir
    python -m tools.test_sensor multisensor --interval 1.0
    python -m tools.test_sensor audio
"""
import argparse
import time

from src.config import load_config
from src.main import build_sensors


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sensor", help="sensor name from config.yaml's sensors: block, e.g. pir, audio, multisensor, heart_rate, accel_stick, nodes, motion")
    parser.add_argument("--interval", type=float, default=0.5, help="seconds between reads (default: 0.5)")
    args = parser.parse_args()

    config = load_config()
    if args.sensor not in config["sensors"]:
        raise SystemExit(f"Unknown sensor {args.sensor!r} - expected one of {list(config['sensors'])}")

    # Only construct the one sensor being tested - every other one gets
    # temporarily marked disabled so build_sensors() skips constructing it
    # (and doesn't e.g. open the mic or an I2C bus you're not testing).
    isolated_config = {
        **config,
        "sensors": {
            name: {**cfg, "enabled": name == args.sensor}
            for name, cfg in config["sensors"].items()
        },
    }

    sensors = build_sensors(isolated_config)
    sensor = sensors[args.sensor]
    print(f"Testing {type(sensor).__name__} (Ctrl+C to stop)...\n")

    try:
        while True:
            print(sensor.read())
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
