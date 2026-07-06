"""
colour_palette.py

Base layer for the LED effect system: named colour palettes and the
primitives that turn a short list of "anchor" colours into something an
effect can index by a continuous 0..1 position.

Colours interpolate in plain RGB, in the order you list them - so a
palette isn't just "any colours", it's a *path* through colour space.
Two things affect how it looks:

  1. Order. _palette_lut and color_at walk your anchors in the order
     given - not brightness-sorted, not hue-sorted. Put them in the
     sequence you actually want the gradient to move through.
  2. Hue distance between adjacent anchors. Plain RGB interpolation
     doesn't know about hue - it just draws a straight line through the
     colour cube. Red -> green passes through (128,128,0), a dull olive,
     because that's the midpoint of a straight line between them, not
     because anything's wrong. Anchors close in hue or sharing a channel
     blend cleanly; near-complementary neighbours will always have a
     muddier middle. If that's not wanted, insert a bridge colour or
     reorder so complementary colours aren't adjacent.

PALETTES starts from a few built-in defaults, then merges in anything
found in palettes.json (same file build_palette.py writes to) at import
time - so hand-picked palettes defined below and crowd-sourced ones
extracted from a photo end up in the same dict, usable interchangeably by
any effect.
"""

import json
import pathlib

import numpy as np

_PALETTES_FILE = pathlib.Path("palettes.json")


def _load_custom_palettes() -> dict:
    """Palettes contributed via build_palette.py. Missing file (nobody's
    run it yet) just means no custom palettes - not an error."""
    if not _PALETTES_FILE.is_file():
        return {}
    with _PALETTES_FILE.open("r") as f:
        return json.load(f)


PALETTES = {
    "winter": ["#0B132B", "#1C2541", "#3A506B", "#5BC0BE", "#FFFFFF"],
    "autumn": ["#3E1F0E", "#7A3B12", "#C1652F", "#E8A33D", "#F2D492"],
    "festive": ["#0B3D2E", "#1B5E3F", "#C21E33", "#F2C230", "#FFFFFF"],
}
PALETTES.update(_load_custom_palettes())


def hex_to_rgb(hex_code: str) -> tuple:
    """"#RRGGBB" -> (r, g, b), each 0-255."""
    h = hex_code.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def color_at(palette: list, t: float) -> tuple:
    """The palette's colour at position t in [0, 1], linearly interpolated
    (in RGB) between whichever two anchors t falls between. Anchors are
    visited in the order given - see module docstring."""
    anchors = [hex_to_rgb(c) for c in palette]
    if len(anchors) == 1:
        return anchors[0]

    t = min(max(t, 0.0), 1.0)
    scaled = t * (len(anchors) - 1)
    i = min(int(scaled), len(anchors) - 2)
    frac = scaled - i
    a, b = anchors[i], anchors[i + 1]
    return tuple(a[c] + (b[c] - a[c]) * frac for c in range(3))


def _palette_lut(palette: list, size: int = 256) -> np.ndarray:
    """A (size, 3) uint8 array - the palette sampled at `size` evenly
    spaced points across [0, 1]. Effects index into this by position each
    frame, which is far cheaper than calling color_at per-pixel every
    frame since it's built once and reused."""
    lut = np.zeros((size, 3), dtype=np.uint8)
    for i in range(size):
        lut[i] = color_at(palette, i / (size - 1))
    return lut
