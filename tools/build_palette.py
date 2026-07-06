"""
build_palette.py

Turn a photo into a curated LED palette. Point this at a picture of
Churchill in autumn, under exam-term grey skies, decked out for a May Ball,
whatever - it pulls out the N most dominant colours and writes them as a
named entry into palettes.json, in exactly the shape led_effects.py expects.

This is doing the same job as picking colours by eye in Coolors or Adobe
Color, just automated: PIL's quantize() runs a colour-clustering algorithm
(median-cut) under the hood, which is the same family of technique as
k-means for this purpose - group similar pixel colours together, then
report the centre of each group.

Run this from the repo root so palettes.json lands next to it - that's
where src/output/effects/led_effects.py looks for custom palettes.

Usage:
    python3 tools/build_palette.py photo.jpg --name autumn --colors 4
    python3 tools/build_palette.py photo.jpg --name may_ball --colors 5 --preview

Writes/updates palettes.json and, with --preview, saves a small swatch
strip image next to your source photo so you can eyeball the result
before committing to it.
"""

import argparse
import json
import os

from PIL import Image


def extract_palette(image_path, n_colors=4):
    img = Image.open(image_path).convert("RGB")
    img.thumbnail((300, 300))  # quantize is slow on full-resolution photos
    quantized = img.quantize(colors=n_colors, method=Image.MEDIANCUT)
    palette = quantized.getpalette()[: n_colors * 3]
    rgb_colors = [tuple(palette[i:i + 3]) for i in range(0, len(palette), 3)]

    counts = sorted(quantized.getcolors(), reverse=True)
    order = [color_index for _, color_index in counts if color_index < n_colors]
    ordered_colors = [rgb_colors[i] for i in order] or rgb_colors

    ordered_colors.sort(key=lambda c: 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2])
    return ["#{:02X}{:02X}{:02X}".format(*c) for c in ordered_colors]


def save_preview(hex_colors, out_path):
    swatch_w = 120
    strip = Image.new("RGB", (swatch_w * len(hex_colors), 120))
    for i, hex_code in enumerate(hex_colors):
        rgb = tuple(int(hex_code.lstrip("#")[j:j + 2], 16) for j in (0, 2, 4))
        block = Image.new("RGB", (swatch_w, 120), rgb)
        strip.paste(block, (i * swatch_w, 0))
    strip.save(out_path)


def main():
    parser = argparse.ArgumentParser(description="Extract an LED palette from a photo")
    parser.add_argument("image", help="Path to a source photo")
    parser.add_argument("--name", required=True, help="Palette name, e.g. autumn")
    parser.add_argument("--colors", type=int, default=4, help="Number of anchor colours")
    parser.add_argument("--out", default="palettes.json", help="Where to save the palette dictionary")
    parser.add_argument("--preview", action="store_true", help="Also save a swatch-strip preview image")
    args = parser.parse_args()

    hex_colors = extract_palette(args.image, args.colors)

    palettes = {}
    if os.path.exists(args.out):
        with open(args.out) as f:
            palettes = json.load(f)
    palettes[args.name] = hex_colors
    with open(args.out, "w") as f:
        json.dump(palettes, f, indent=2)

    print(f"{args.name}: {hex_colors}")
    print(f"Saved to {args.out}")

    if args.preview:
        preview_path = os.path.splitext(args.image)[0] + f"_{args.name}_swatch.png"
        save_preview(hex_colors, preview_path)
        print(f"Swatch preview saved to {preview_path}")


if __name__ == "__main__":
    main()
