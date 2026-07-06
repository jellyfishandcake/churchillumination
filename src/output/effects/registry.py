"""
registry.py

Single source of truth for effect *names* - the strings the terminal's
effect picker sends over the control channel, and the strings config.py's
defaults use. main.py looks names up here rather than hardcoding the
mapping itself, so there's exactly one place that has to agree with the
web UI's <select> options.
"""

from .led_effects import OrganicWaveEffect, OrganicCometEffect, OrganicTwinkleEffect

EFFECTS = {
    "organic_wave": OrganicWaveEffect,
    "organic_comet": OrganicCometEffect,
    "organic_twinkle": OrganicTwinkleEffect,
}

DEFAULT_EFFECT = "organic_wave"
DEFAULT_PALETTE = "winter"
