"""
test_dmx.py

Drive one DMX fixture directly, without the full sensor/effect pipeline -
for bringing up a borrowed or newly-bought fixture over a USB-DMX512
interface before it's wired into config.yaml's dmx.fixtures. Same role for
DMX bring-up as tools/test_sensor.py plays for a single sensor.

Usage:
    python -m tools.test_dmx ports                        # list serial ports (helps identify the interface + confirm auto-detect picked the right one)
    python -m tools.test_dmx solid 255 0 0                 # solid red, channels 1-3, held until Ctrl+C
    python -m tools.test_dmx solid 255 0 0 --start 4        # same, starting at DMX channel 4
    python -m tools.test_dmx cycle                          # slow R -> G -> B cycle, channels 1-3
    python -m tools.test_dmx blackout
    python -m tools.test_dmx solid 255 0 0 --port COM5      # explicit port if auto-detect picks the wrong device
"""
import argparse
import time

from src.output.dmx import DMXInterface, UNIVERSE_SIZE


def _list_ports():
    from serial.tools import list_ports
    ports = list(list_ports.comports())
    if not ports:
        print("No serial ports found.")
        return
    for p in ports:
        vid = f"{p.vid:04X}" if p.vid else "----"
        print(f"{p.device}  (VID:{vid}  {p.description})")


def _run(fn):
    try:
        fn()
    except KeyboardInterrupt:
        print("\nStopped.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["ports", "solid", "cycle", "blackout"])
    parser.add_argument("rgb", nargs="*", type=int, help="r g b (0-255), required for 'solid'")
    parser.add_argument("--start", type=int, default=1, help="DMX start channel, 1-based (default: 1)")
    parser.add_argument("--port", default=None, help="explicit serial port, e.g. COM5 - overrides auto-detect")
    args = parser.parse_args()

    if args.command == "ports":
        _list_ports()
        return

    dmx = DMXInterface(port=args.port)

    if args.command == "blackout":
        dmx.blackout()
        print("Sent blackout.")
        return

    if args.command == "solid":
        if len(args.rgb) != 3:
            raise SystemExit("solid needs exactly 3 values: r g b (0-255)")
        r, g, b = args.rgb
        universe = [0] * UNIVERSE_SIZE
        universe[args.start - 1:args.start + 2] = [r, g, b]
        print(f"Sending R={r} G={g} B={b} at channel {args.start} (Ctrl+C to stop)...")

        def send_forever():
            while True:
                dmx.send_channels(universe)
                time.sleep(0.05)

        _run(send_forever)
        return

    if args.command == "cycle":
        print(f"Cycling R -> G -> B at channel {args.start} (Ctrl+C to stop)...")
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]

        def cycle_forever():
            while True:
                for r, g, b in colors:
                    universe = [0] * UNIVERSE_SIZE
                    universe[args.start - 1:args.start + 2] = [r, g, b]
                    for _ in range(40):  # ~2s per colour at 20Hz
                        dmx.send_channels(universe)
                        time.sleep(0.05)

        _run(cycle_forever)
        return


if __name__ == "__main__":
    main()
