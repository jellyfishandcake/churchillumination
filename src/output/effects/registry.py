"""
registry.py

Single source of truth for effect *names* - the strings the per-zone
effect pickers send over the control channel, and the strings each zone in
config.py's leds.zones defaults uses. main.py looks names up here rather
than hardcoding the mapping itself, so there's exactly one place that has
to agree with the web UI's <select> options.

Every zone (a large ambient panel or a small cutout like heart-rate) picks
one effect class from the same registry below - just its own palette and
its own named source(s) (see main.py's led_loop/_resolve_sources). Most
effects take a single `intensity` kwarg (organic_wave/organic_comet/
organic_twinkle/reactive_glow); temp_humidity_matrix takes
`temperature`+`humidity`, plus an optional `activity` for its shimmer (see
its own docstring); pulse takes `intensity`+`bpm` (bpm times its per-beat
flash to the wearer's actual heart rate - see PulseEffect's docstring) - a
zone's configured `source` dict keys must match whichever effect it's
assigned, since led_loop calls effect.step(**sources).

reactive_glow is the one to reach for on a single-pixel DMX zone
(output.type: dmx) - organic_wave/organic_comet vary colour *across pixel
positions*, which a single DMX fixture doesn't have (see ReactiveGlowEffect's
own docstring for the specific bug this avoids in organic_comet's case).
"""

from .led_effects import (
    OrganicWaveEffect, OrganicCometEffect, OrganicTwinkleEffect,
    PulseEffect, TempHumidityMatrixEffect, ReactiveGlowEffect,
)

EFFECTS = {
    "organic_wave": OrganicWaveEffect,
    "organic_comet": OrganicCometEffect,
    "organic_twinkle": OrganicTwinkleEffect,
    "pulse": PulseEffect,
    "temp_humidity_matrix": TempHumidityMatrixEffect,
    "reactive_glow": ReactiveGlowEffect,
}

# Defensive fallback if a zone's configured effect/palette name doesn't
# exist in EFFECTS/PALETTES (e.g. a config typo) - not a "system default"
# in the old single-global-picker sense anymore, since each zone carries
# its own default effect+palette in config.py's leds.zones.
DEFAULT_EFFECT = "organic_wave"
DEFAULT_PALETTE = "winter"
