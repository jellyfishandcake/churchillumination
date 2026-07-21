"""
calibrate_sensor.py

Poll ONE sensor in isolation (same isolation trick as test_sensor.py) for a
stretch of time, tracking min/max/mean per numeric reading - so you have
real numbers to plug into config.yaml's zone `source` {min, max} rescale
ranges instead of guessing (e.g. the temp_humidity zone's temperature/
humidity bounds, or the heart_rate zone's bpm range).

Usage:
    python -m tools.calibrate_sensor multisensor --duration 60
    python -m tools.calibrate_sensor heart_rate --duration 30 --interval 0.2
"""
import argparse
import time

from src.config import load_config
from src.main import build_sensors


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sensor", help="sensor name from config.yaml's sensors: block")
    parser.add_argument("--interval", type=float, default=0.5, help="seconds between reads (default: 0.5)")
    parser.add_argument("--duration", type=float, default=None, help="stop automatically after this many seconds (default: run until Ctrl+C)")
    args = parser.parse_args()

    config = load_config()
    if args.sensor not in config["sensors"]:
        raise SystemExit(f"Unknown sensor {args.sensor!r} - expected one of {list(config['sensors'])}")

    # Same isolation trick as test_sensor.py - only the requested sensor
    # actually gets constructed.
    isolated_config = {
        **config,
        "sensors": {
            name: {**cfg, "enabled": name == args.sensor}
            for name, cfg in config["sensors"].items()
        },
    }
    sensors = build_sensors(isolated_config)
    sensor = sensors[args.sensor]

    stats = {}  # key -> {"min", "max", "sum", "count"}
    n_reads = 0
    start = time.monotonic()
    print(f"Calibrating {type(sensor).__name__} (Ctrl+C to stop early)...\n")

    try:
        while args.duration is None or (time.monotonic() - start) < args.duration:
            reading = sensor.read()
            n_reads += 1
            for key, value in reading.items():
                is_numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
                if not is_numeric:
                    continue
                s = stats.setdefault(key, {"min": value, "max": value, "sum": 0.0, "count": 0})
                s["min"] = min(s["min"], value)
                s["max"] = max(s["max"], value)
                s["sum"] += value
                s["count"] += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass

    elapsed = time.monotonic() - start
    print(f"\n{n_reads} reads over {elapsed:.1f}s:\n")
    if not stats:
        print("No numeric readings seen (only booleans/None so far, or nothing detected yet).")
        return

    name_width = max(len(key) for key in stats)
    for key, s in stats.items():
        mean = s["sum"] / s["count"]
        print(f"  {key:<{name_width}}  min={s['min']:.2f}  max={s['max']:.2f}  mean={mean:.2f}  (n={s['count']})")
    print("\nCopy these into a zone's source {path, min, max} in config.yaml to calibrate its 0..1 rescale.")


if __name__ == "__main__":
    main()
