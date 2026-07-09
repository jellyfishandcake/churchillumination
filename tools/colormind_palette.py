"""
colormind_palette.py

Calls the Colormind API (colormind.io) - a small, free, non-commercial
deep-learning palette generator - and folds the result into palettes.json,
in the same shape build_palette.py and led_effects.py expect.

The useful trick: you can lock some anchor colours (say, a Churchill
institutional colour, or a hue you designed by hand) and let the AI fill
in the rest so they harmonise with it, rather than getting a fully random
palette every time. --from-photo does this automatically using colours
pulled from a real photo (via build_palette.py's extract_palette) as the
locked anchors - so instead of an unconstrained AI palette, you get one
that's still rooted in an actual photo, just completed/harmonised by the
model rather than left as whatever raw colour-clustering produced.

This is a personal, free hobby API with no uptime guarantee (per
Colormind's own documentation), so treat it as a design-time tool for
building your palette dictionary once, offline-friendly after that - not
something the live installation calls over the internet during the
exhibition.

Usage:
    python3 tools/colormind_palette.py --name evening --model default
    python3 tools/colormind_palette.py --name churchill_locked --lock 0 "#4A1B0C"
    python3 tools/colormind_palette.py --name autumn_ai --from-photo autumn.jpg --photo-colors 2

Note: this hits a real external endpoint, so it can only be tested with
network access - it hasn't been run against the live API here, only the
surrounding logic (argument parsing, hex conversion, saving) has been
verified against a simulated response.
"""

import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))  # so `import build_palette` finds its sibling script
from build_palette import extract_palette

API_URL = "http://colormind.io/api/"


def generate_palette(model="default", fixed_colors=None):
    """
    fixed_colors: optional dict of {index: (r, g, b)} for anchors you want
    to keep fixed. Any index 0-4 not given is left for the AI to fill,
    marked as "N" per Colormind's input format.
    """
    fixed_colors = fixed_colors or {}
    payload_input = []
    for i in range(5):
        if i in fixed_colors:
            payload_input.append(list(fixed_colors[i]))
        else:
            payload_input.append("N")

    body = json.dumps({"model": model, "input": payload_input}).encode("utf-8")
    # Colormind's server 403s requests carrying urllib's default User-Agent
    # (a basic bot-block) - any real User-Agent string satisfies it.
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "churchillumination-palette-tool/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        data = json.loads(response.read().decode("utf-8"))
    return ["#{:02X}{:02X}{:02X}".format(*rgb) for rgb in data["result"]]


def hex_to_rgb(hex_code):
    hex_code = hex_code.lstrip("#")
    return tuple(int(hex_code[i:i + 2], 16) for i in (0, 2, 4))


def save_to_palettes(name, hex_colors, out_path="palettes.json"):
    palettes = {}
    if os.path.exists(out_path):
        with open(out_path) as f:
            palettes = json.load(f)
    palettes[name] = hex_colors
    with open(out_path, "w") as f:
        json.dump(palettes, f, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Generate a palette via Colormind and save it")
    parser.add_argument("--name", required=True)
    parser.add_argument("--model", default="default", help="e.g. default, ui, or a themed daily model")
    parser.add_argument("--lock", nargs=2, action="append", metavar=("INDEX", "HEX"),
                         help="Lock anchor at INDEX (0-4) to HEX, e.g. --lock 0 #4A1B0C. Repeatable.")
    parser.add_argument("--from-photo", metavar="IMAGE",
                         help="Extract anchor colours from a photo (via build_palette.py) and lock "
                              "them at indices 0..N-1, letting Colormind harmonise the rest.")
    parser.add_argument("--photo-colors", type=int, default=2,
                         help="How many colours to pull from --from-photo (default 2, leaving 3 "
                              "slots for the AI to fill). Only used with --from-photo.")
    parser.add_argument("--out", default="palettes.json")
    args = parser.parse_args()

    fixed = {}
    if args.from_photo:
        photo_colors = extract_palette(args.from_photo, n_colors=args.photo_colors)
        for i, hex_code in enumerate(photo_colors):
            fixed[i] = hex_to_rgb(hex_code)
        print(f"locked from photo: {photo_colors}")
    if args.lock:
        # Explicit --lock always wins over --from-photo for the same index,
        # since it's a more deliberate, specific choice.
        for index_str, hex_code in args.lock:
            fixed[int(index_str)] = hex_to_rgb(hex_code)

    hex_colors = generate_palette(model=args.model, fixed_colors=fixed)
    save_to_palettes(args.name, hex_colors, args.out)
    print(f"{args.name}: {hex_colors}")
    print(f"Saved to {args.out}")


if __name__ == "__main__":
    main()
