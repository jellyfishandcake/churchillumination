"""Runs the actual work behind a contribute.html palette-build request:
decode the uploaded photo, extract colours (optionally handing them to
Colormind to complete/harmonise), and save the result - reusing the same
logic the command-line tools/build_palette.py and tools/colormind_palette.py
already implement, not a re-implementation.

Deliberately lives here (not in server.py, which stays a dumb transport
layer with no src.sensing/src.output/tools imports) and is meant to be
called via asyncio.to_thread from main.py's palette_build_loop, since both
the PIL decode and (when use_ai is True) Colormind's blocking network call
would otherwise stall the whole single-threaded event loop - sensors,
LEDs, every connected browser - for however long they take.
"""
import base64
import io

from tools.build_palette import extract_palette
from tools.colormind_palette import generate_palette, hex_to_rgb, save_to_palettes
from src.output.effects import colour_palette

RESERVED_NAMES = {"winter", "autumn", "festive"}


def run_palette_build(request: dict) -> dict:
    """request: the dict server.py's _handle_control validated and stashed
    in palette_job_request (name, image_data_url, use_ai, n_colors,
    photo_colors). Returns {"hex_colors": [...], "overwritten": bool}.

    Raises on failure (bad image, Colormind unreachable, reserved name) -
    the caller (main.py's palette_build_loop) turns that into a
    palette_job status of "error" rather than anything being caught here,
    keeping this function a plain, readable, independently-testable unit.
    """
    name = request["name"]
    if name in RESERVED_NAMES:
        raise ValueError(f"'{name}' is a built-in palette name and can't be overwritten")

    overwritten = name in colour_palette.PALETTES

    _header, _, b64_data = request["image_data_url"].partition(",")
    image_bytes = io.BytesIO(base64.b64decode(b64_data))

    if request["use_ai"]:
        photo_colors = extract_palette(image_bytes, n_colors=request["photo_colors"])
        fixed = {i: hex_to_rgb(c) for i, c in enumerate(photo_colors)}
        hex_colors = generate_palette(model="default", fixed_colors=fixed)
    else:
        hex_colors = extract_palette(image_bytes, n_colors=request["n_colors"])

    save_to_palettes(name, hex_colors)
    return {"hex_colors": hex_colors, "overwritten": overwritten}
