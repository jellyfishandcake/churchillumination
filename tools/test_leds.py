"""
test_leds.py

Drive the APA102 strip directly, without the full sensor/effect pipeline -
for bringing up newly soldered LED zones (checking continuity through a
splice, colour-channel order, which physical pixel index is where) before
they're driven by config.yaml's zone effects. Same role for LED bring-up as
tools/test_dmx.py plays for a DMX fixture.

Must run ON the Pi (needs spidev + SPI0 enabled via raspi-config -> Interface
Options -> SPI). On a dev laptop with no spidev installed, src.output.leds's
LEDStrip falls back to printing frame summaries instead of driving real
hardware - harmless, just won't show you anything on the strip.

Usage:
    python -m tools.test_leds solid 255 0 0                        # heart_rate_strip (SPI0), whole strip solid red
    python -m tools.test_leds solid 255 0 0 --num-pixels 94 --spi-bus 1 --spi-device 0
                                                                     # accelerometer_strip (SPI1) instead - see leds.strips in config.yaml for each strip's real spi_bus/spi_device/num_pixels
    python -m tools.test_leds chase                                 # single white pixel walks the whole strip, ~3px/sec, repeating - watch where it stops lighting to find a broken joint or open circuit
    python -m tools.test_leds chase --start 26 --count 12 --color 255 0 0
    python -m tools.test_leds off
    python -m tools.test_leds solid 255 0 0 --num-pixels 38          # --num-pixels must be >= the highest pixel this strip's SPI chain will actually shift data through, even mid-solder - see note below

If you've only soldered part of a zone so far, run `chase` over the whole
strip (not just --start/--count for the finished part) - the walking pixel
will visibly stop advancing right at the break, which tells you exactly
which joint still needs a connection, rather than a `solid` call over a
--count range that assumes continuity you haven't confirmed yet.

If nothing lights up on --spi-bus 1 at all (not even a "couldn't open SPI"
failure into print-mode - check the terminal output for that line), confirm
`dtoverlay=spi1-3cs` is actually in /boot/firmware/config.txt and the Pi's
been rebooted since - without it /dev/spidev1.* doesn't exist at all, and
LEDStrip falls back to printing frames instead of raising, so a missing
overlay looks identical to "nothing's wrong, it's just not driving real
LEDs" unless you're watching that startup print.
"""
import argparse
import time

from src.output.leds import LEDStrip


def _run(fn):
    try:
        fn()
    except KeyboardInterrupt:
        print("\nStopped.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["solid", "chase", "off"])
    parser.add_argument("args_list", nargs="*", help="'solid': r g b (0-255). 'chase' takes none (use --color).")
    parser.add_argument("--start", type=int, default=0, help="0-based pixel index to start at (default: 0)")
    parser.add_argument("--count", type=int, default=None, help="how many pixels to affect from --start (default: rest of the strip)")
    parser.add_argument("--num-pixels", type=int, default=38, help="total strip length - match whichever strip's num_pixels in config.yaml's leds.strips (default: 38)")
    parser.add_argument("--color", type=int, nargs=3, default=[255, 255, 255], metavar=("R", "G", "B"), help="chase pixel colour (default: white)")
    parser.add_argument("--speed", type=float, default=0.12, help="chase seconds per pixel step (default: 0.12, ~3px/sec)")
    parser.add_argument("--spi-bus", type=int, default=0, help="SPI bus - 0 for heart_rate_strip, 1 for accelerometer_strip (default: 0, see config.yaml's leds.strips)")
    parser.add_argument("--spi-device", type=int, default=0, help="SPI device/chip-select on that bus (default: 0)")
    args = parser.parse_args()

    strip = LEDStrip(num_pixels=args.num_pixels, spi_bus=args.spi_bus, spi_device=args.spi_device)
    count = args.count if args.count is not None else (args.num_pixels - args.start)
    if args.start < 0 or args.start + count > args.num_pixels:
        raise SystemExit(f"--start/--count ({args.start}, {count}) falls outside --num-pixels ({args.num_pixels})")

    if args.command == "off":
        strip.render_pixels([[0, 0, 0]] * args.num_pixels)
        print("Sent blackout.")
        return

    if args.command == "solid":
        if len(args.args_list) != 3:
            raise SystemExit("solid needs exactly 3 values: r g b (0-255)")
        r, g, b = (int(v) for v in args.args_list)
        frame = [[0, 0, 0]] * args.num_pixels
        frame[args.start:args.start + count] = [[r, g, b]] * count
        print(f"Sending R={r} G={g} B={b} to pixels [{args.start}:{args.start + count}) of {args.num_pixels} (Ctrl+C to stop)...")

        def send_forever():
            while True:
                strip.render_pixels(frame)
                time.sleep(0.05)

        _run(send_forever)
        return

    if args.command == "chase":
        r, g, b = args.color
        print(f"Chasing pixel [{args.start}:{args.start + count}) of {args.num_pixels}, colour ({r},{g},{b}) - watch where it stops lighting (Ctrl+C to stop)...")

        def chase_forever():
            while True:
                for i in range(args.start, args.start + count):
                    frame = [[0, 0, 0]] * args.num_pixels
                    frame[i] = [r, g, b]
                    strip.render_pixels(frame)
                    time.sleep(args.speed)

        _run(chase_forever)
        return


if __name__ == "__main__":
    main()
