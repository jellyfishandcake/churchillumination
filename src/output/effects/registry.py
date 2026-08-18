"""
registry.py

Single source of truth for effect *names* - the strings the per-zone
effect pickers send over the control channel, and the strings each zone in
config.py's leds.zones defaults uses. main.py looks names up here rather
than hardcoding the mapping itself, so there's exactly one place that has
to agree with the web UI's <select> options.

Every zone (a large ambient bar or a small cutout like heart-rate) picks
one effect class from the same registry below - just its own palette and
its own named source(s) (see main.py's led_loop/_resolve_sources). Most
effects take a single `intensity` kwarg (organic_wave/organic_comet/
organic_twinkle/reactive_glow); temp_humidity_bar takes `temperature`
(indoor reading - also drives its touch-react rising-edge detector),
`contrast` (indoor/outdoor divergence - drives its shimmer layer) and
`condition` (weather_code bucket - drives its base colour/character) - see
its own docstring for the three-layer ambient/shimmer/touch design; pulse
takes `intensity`+`bpm` (bpm times its per-beat
flash to the wearer's actual heart rate - see PulseEffect's docstring);
tri_arm_glide takes `intensity`+`angle` (a swing angle rescaled to 0..1 -
see TriArmGlideEffect's own docstring); audio_reactive_wave takes
`loudness`+`motion`+`env_brightness`+`ripple` (see AudioReactiveWaveEffect's
own docstring); heart_rate_check_in takes `intensity`+`bpm`, same shape as
pulse, but drives a distinct start/read/end check-in flow instead of a
continuous display (see HeartRateEffect's own docstring) - a zone's
configured `source` dict keys must match whichever effect it's assigned,
since led_loop calls effect.step(**sources).

reactive_glow is the one to reach for on a single-segment DMX zone whose
source is just `intensity` (output.type: dmx, no `pixels` set, or
pixels: 1) - organic_wave/organic_comet all vary colour or position
*across pixel positions*, which a single DMX fixture doesn't have (see
ReactiveGlowEffect's own docstring for the specific bug this avoids in
organic_comet's case). temp_humidity_bar is DMX too (as of the weather
zone's move off the old LED-matrix idea onto a dedicated RGBW bar fixture)
but isn't a reactive_glow candidate despite that overlap - its source
shape (`temperature`+`contrast`+`condition`, not `intensity`) doesn't
reduce to a plain reactive_glow input regardless of segment count. tri_arm_glide is
specific to the accelerometer zone's three-armed `led` layout (output.type:
led, one continuous chain split into three equal-ish spokes) - not meant to
be picked for an arbitrary zone the way the other effects are.
"""

from .led_effects import (
    OrganicWaveEffect, OrganicCometEffect, OrganicTwinkleEffect,
    PulseEffect, TempHumidityBarEffect, ReactiveGlowEffect,
    TriArmGlideEffect, AudioReactiveWaveEffect, HeartRateEffect,
)

EFFECTS = {
    "organic_wave": OrganicWaveEffect,
    "organic_comet": OrganicCometEffect,
    "organic_twinkle": OrganicTwinkleEffect,
    "pulse": PulseEffect,
    "temp_humidity_bar": TempHumidityBarEffect,
    "reactive_glow": ReactiveGlowEffect,
    "tri_arm_glide": TriArmGlideEffect,
    "audio_reactive_wave": AudioReactiveWaveEffect,
    "heart_rate_check_in": HeartRateEffect,
}

# Defensive fallback if a zone's configured effect/palette name doesn't
# exist in EFFECTS/PALETTES (e.g. a config typo) - not a "system default"
# in the old single-global-picker sense anymore, since each zone carries
# its own default effect+palette in config.py's leds.zones.
DEFAULT_EFFECT = "organic_wave"
DEFAULT_PALETTE = "winter"
