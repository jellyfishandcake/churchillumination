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
    python -m tools.test_dmx raw 112=255                    # ONLY channel 112 set, everything else 0 -
                                                              # for figuring out an unfamiliar fixture's real
                                                              # channel layout one channel at a time, since
                                                              # `solid` can't isolate a single channel (see its
                                                              # fixed r/g/b-at-3-consecutive-channels shape
                                                              # above). Give several channel=value pairs at
                                                              # once (space-separated) to test a guessed
                                                              # layout, e.g. a guessed RGBW mode:
                                                              #   python -m tools.test_dmx raw 112=0 113=0 114=0 115=255
    python -m tools.test_dmx bar 255 0 0 0 --start 25 --count 28 --channels rgbw
                                                              # repeats one colour across N independently-
                                                              # addressable segments (same "pixels" meaning as
                                                              # a zone's output.pixels) - for bringing up a
                                                              # multi-segment bar (e.g. the whole weather zone's
                                                              # fixture) without going through config.yaml or
                                                              # any sensor at all. --channels sets the per-
                                                              # segment layout/order (values in that order),
                                                              # matching a zone's output.channels - default
                                                              # 'rgb' (3 values), pass 'rgbw' for a 4th value.
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
    parser.add_argument("command", choices=["ports", "solid", "cycle", "blackout", "raw", "bar"])
    # Untyped/unparsed here since the commands that use this need different
    # shapes ('solid' wants 3 ints; 'raw' wants channel=value strings; 'bar'
    # wants one int per --channels entry) - each branch below parses its own
    # args out of this shared list rather than this needing several separate
    # nargs="*" positionals (which argparse can't unambiguously split between
    # anyway).
    parser.add_argument("args_list", nargs="*", help="'solid': r g b (0-255). 'raw': one or more channel=value pairs (1-based channel, 0-255 value). 'bar': one value per --channels entry")
    parser.add_argument("--start", type=int, default=1, help="DMX start channel, 1-based (default: 1)")
    parser.add_argument("--port", default=None, help="explicit serial port, e.g. COM5 - overrides auto-detect")
    parser.add_argument("--channels", default="rgb", help="'bar' only: per-segment channel layout/order, e.g. 'rgb' or 'rgbw' - matches a zone's output.channels (default: rgb)")
    parser.add_argument("--count", type=int, default=1, help="'bar' only: number of consecutive segments to repeat the colour across, same meaning as a zone's output.pixels (default: 1)")
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
        if len(args.args_list) != 3:
            raise SystemExit("solid needs exactly 3 values: r g b (0-255)")
        r, g, b = (int(v) for v in args.args_list)
        universe = [0] * UNIVERSE_SIZE
        universe[args.start - 1:args.start + 2] = [r, g, b]
        print(f"Sending R={r} G={g} B={b} at channel {args.start} (Ctrl+C to stop)...")

        def send_forever():
            while True:
                dmx.send_channels(universe)
                time.sleep(0.05)

        _run(send_forever)
        return

    if args.command == "raw":
        if not args.args_list:
            raise SystemExit("raw needs at least one channel=value pair, e.g. 112=255")
        universe = [0] * UNIVERSE_SIZE
        set_channels = {}
        for pair in args.args_list:
            if "=" not in pair:
                raise SystemExit(f"expected channel=value (e.g. 112=255), got {pair!r}")
            chan_str, value_str = pair.split("=", 1)
            channel, value = int(chan_str), int(value_str)
            if not (1 <= channel <= UNIVERSE_SIZE):
                raise SystemExit(f"channel {channel} out of range (1-{UNIVERSE_SIZE})")
            if not (0 <= value <= 255):
                raise SystemExit(f"value {value} out of range (0-255)")
            universe[channel - 1] = value
            set_channels[channel] = value
        print(f"Sending {set_channels} (every other channel at 0, Ctrl+C to stop)...")

        def send_forever():
            while True:
                dmx.send_channels(universe)
                time.sleep(0.05)

        _run(send_forever)
        return

    if args.command == "bar":
        layout = list(args.channels)
        if len(args.args_list) != len(layout):
            raise SystemExit(f"bar needs exactly {len(layout)} value(s) for --channels {args.channels!r}: {' '.join(layout)}")
        values = [int(v) for v in args.args_list]
        universe = [0] * UNIVERSE_SIZE
        for seg in range(args.count):
            base = args.start - 1 + seg * len(layout)
            for i, v in enumerate(values):
                if 0 <= base + i < UNIVERSE_SIZE:
                    universe[base + i] = v
        print(f"Sending {dict(zip(layout, values))} to {args.count} segment(s) ({len(layout)} ch each) starting at channel {args.start} (Ctrl+C to stop)...")

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
