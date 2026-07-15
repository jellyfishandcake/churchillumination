"""
registry.py

Single source of truth for effect *names* - the strings the per-zone
effect pickers send over the control channel, and the strings each zone in
config.py's leds.zones defaults uses. main.py looks names up here rather
than hardcoding the mapping itself, so there's exactly one place that has
to agree with the web UI's <select> options.

Every zone (main strip or a smaller section like the heart-rate segment)
uses the same effect classes below - just its own palette and its own
intensity source (see main.py's led_loop/_resolve_source). There's no
separate "bulb effect" shape anymore: a zone that used to be a flat glow
is just a zone running organic_twinkle with a warm palette, same as any
other section.
"""

from .led_effects import OrganicWaveEffect, OrganicCometEffect, OrganicTwinkleEffect

EFFECTS = {
    "organic_wave": OrganicWaveEffect,
    "organic_comet": OrganicCometEffect,
    "organic_twinkle": OrganicTwinkleEffect,
}

# Defensive fallback if a zone's configured effect/palette name doesn't
# exist in EFFECTS/PALETTES (e.g. a config typo) - not a "system default"
# in the old single-global-picker sense anymore, since each zone carries
# its own default effect+palette in config.py's leds.zones.
DEFAULT_EFFECT = "organic_wave"
DEFAULT_PALETTE = "winter"
