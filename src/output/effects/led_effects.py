"""
led_effects.py

Upgraded versions of the four effect families, addressing the three things
that make generated LED animation look "cheap" compared to something like
FastLED's Pacifica or TwinkleFox:

1. Sine waves are too regular. A pure sin(x) repeats with a perfectly
   predictable period, which reads as mechanical almost immediately.
   Swap it for coherent noise - smoothly varying, but never exactly
   repeating - and the same code looks organic instead of animated-gif-ish.
   This file implements a small hash-based value-noise function from
   scratch (no extra pip dependency, portable to a Pi with no compiler
   needed) rather than relying on the `noise` package.

2. Everything defaults to running too fast. For reference, FastLED's
   Pacifica layers its four wave passes at roughly 3 to 17 "beats" per
   minute - each cycle takes several seconds to tens of seconds. The
   defaults below are 5-10x slower than the previous version.

3. Linear brightness values don't match how eyes perceive brightness.
   apply_gamma() below is a one-line fix, applied once, right before a
   frame goes to hardware - not during internal blending.
"""

import collections

import numpy as np
from .colour_palette import PALETTES, _palette_lut


def _hash2d(ix, it, seed=0):
    ix = ix.astype(np.int64)
    it = it.astype(np.int64)
    n = ix * 374761393 + it * 668265263 + seed * 2246822519
    n = (n ^ (n >> 13)) * 1274126177
    n = n ^ (n >> 16)
    n = n & 0xFFFFFFFF
    return (n / 0xFFFFFFFF) * 2.0 - 1.0


def value_noise(x, t, seed=0):
    """Smooth, non-repeating noise over (x, t). Both arguments are numpy
    arrays of floats (same shape or broadcastable). Returns values in
    roughly [-1, 1]."""
    x0 = np.floor(x)
    t0 = np.floor(t)
    x1 = x0 + 1
    t1 = t0 + 1
    fx = x - x0
    ft = t - t0
    sx = fx * fx * (3 - 2 * fx)  # smoothstep easing, not linear interpolation
    st = ft * ft * (3 - 2 * ft)
    n00 = _hash2d(x0, t0, seed)
    n10 = _hash2d(x1, t0, seed)
    n01 = _hash2d(x0, t1, seed)
    n11 = _hash2d(x1, t1, seed)
    nx0 = n00 * (1 - sx) + n10 * sx
    nx1 = n01 * (1 - sx) + n11 * sx
    return nx0 * (1 - st) + nx1 * st


def scaled(intensity: float, low: float, high: float) -> float:
    """Linear-interpolate intensity (0..1, clamped) into [low, high] - the
    shared "quiet rooms get a slower/dimmer X, lively rooms get a faster/
    brighter X" pattern every effect below uses for its own speed_scale/
    brightness_scale/spawn_scale, factored out so effects share one formula
    instead of each hand-rolling `low + (high - low) * intensity`. The
    low/high pair still varies per effect/parameter by design (e.g. a
    boolean-driven zone like accelerometer only ever sees intensity=0 or 1,
    so its floor matters less than ambient's continuous activity_level) -
    only the interpolation itself is shared, not the tuned endpoints."""
    intensity = min(max(intensity, 0.0), 1.0)
    return low + (high - low) * intensity


def apply_gamma(rgb, gamma=2.2):
    """Perceptual brightness correction. Apply once, right before a frame
    goes to hardware - not inside individual effects, or you'll double-
    correct when effects get composited together."""
    normalized = np.clip(rgb, 0, 255) / 255.0
    corrected = np.power(normalized, gamma) * 255.0
    return np.clip(corrected, 0, 255).astype(np.uint8)


class OrganicWaveEffect:
    """Two layers of coherent noise instead of sine waves, with a gentle
    brightness boost where the layers happen to agree - a cheap stand-in
    for Pacifica's 'whitecaps' highlight pass."""

    def __init__(self, n_pixels, palette, seed=0, speed=0.03, scale=None, highlight=True):
        self.n = n_pixels
        self.lut = _palette_lut(palette)
        self.seed = seed
        self.speed = speed
        # scale is "noise-units per pixel" - a fixed 0.05 (the old default)
        # was tuned for a several-dozen-pixel strip. At n=8 (a segmented DMX
        # bar) x*scale only spans ~0.4 noise-units total across the whole
        # zone - nowhere near one full noise cell (~1 unit) - so every
        # segment samples almost the same point in the noise field and
        # reads as one solid colour instead of a flowing wave. Default now
        # targets ~2.5 visible wave cycles across however many pixels this
        # zone actually has, so it looks like a wave whether it's an
        # 8-segment DMX bar or a 60-pixel strip - pass scale explicitly to
        # override the cycle count.
        self.scale = scale if scale is not None else 2.5 / max(n_pixels, 1)
        self.highlight = highlight
        self.t = 0.0

    def step(self, intensity: float = 1.0):
        """intensity is the current activity_level (0..1) - quiet rooms get
        a slower, dimmer flow; lively rooms get a faster, brighter one.
        speed_scale's floor raised from 0.3 to 0.8 (2026-07-28) - on a
        single-pixel DMX zone (one fixture, no spatial extent for the wave
        to move across - see this effect's use on n_pixels=1 output.py
        zones) the ORIGINAL calm-ambient-strip pacing read as "a static
        block of colour" rather than alive, since there's no positional
        variation to compensate for a slow time-evolution the way a whole
        strip has. Base `speed` bumped 0.006->0.03 for the same reason -
        still scales with intensity same as before, just faster at every
        point on that scale."""
        speed_scale = scaled(intensity, 0.8, 2.0)
        brightness_scale = scaled(intensity, 0.3, 1.2)  # was 0.4-1.2 (too little contrast), then 0.15-1.2 (too dim)

        x = np.arange(self.n)
        layer1 = value_noise(x * self.scale, np.full(self.n, self.t), seed=self.seed)
        layer2 = value_noise(x * self.scale * 2.3 + 50, np.full(self.n, self.t * 1.7), seed=self.seed + 1)
        combined = 0.65 * layer1 + 0.35 * layer2
        t_norm = np.clip((combined + 1) / 2, 0, 1)
        indices = (t_norm * (len(self.lut) - 1)).astype(int)
        frame = self.lut[indices].copy()
        if self.highlight:
            agreement = np.clip(layer1 * layer2, 0, None)
            frame = np.clip(frame + (agreement * 70)[:, None], 0, 255)
        frame = np.clip(frame * brightness_scale, 0, 255)
        self.t += self.speed * speed_scale
        return frame.astype(np.uint8)


class AudioReactiveWaveEffect:
    """The ambient zone's effect: two independent, legible drivers instead
    of the older organic_wave's single blended `intensity` (which came from
    state.activity_level, a 0.6*loudness+0.4*motion mix - see the
    conversation that led to this and rules.py's own docstring on why that
    blend still exists for the dashboard mood label and other zones, just
    isn't the right vehicle for this zone's visual response).

    `loudness` picks a position in the palette gradient (same idea as
    TriArmGlideEffect's per-arm palette_index), `motion`
    controls how fast the wave flows, `env_brightness` (from the
    multisensor stick's lux reading) keeps the strip visible against the
    room's own light rather than reacting to loudness/motion at all, and
    `ripple` (see intelligence/audio_moments.py) washes a brief flash of
    white over the wave on laughter/cheering/applause/music.

    Kept as its own class rather than a modified OrganicWaveEffect, since
    "organic_wave" is still independently selectable for any zone from the
    dashboard's Zones tab - a zone whose source only supplies `intensity`
    would break against a changed signature (see output_loop's
    TypeError->hold-last-frame fallback). Reuses the same value_noise
    machinery (see that function's own docstring for why noise beats a
    sine wave here).

    loudness's raw 0..1 value (see audio.py's own docstring on how
    untested that mapping still is) isn't used directly as the palette
    position - instead it's rescaled against a self-calibrating ceiling
    (see _AdaptiveCeiling) so the palette gets explored across its full
    range regardless of how the mic's sensitivity constant happens to be
    tuned, rather than the whole effect spending its life squeezed into
    one end of the gradient."""

    TICK_SECONDS = 0.05
    # How much motion speeds up the underlying noise field's drift - widened
    # 2026-08-17 (was 0.8-2.0, a bare 2.5x range on top of an already-slow
    # base drift - too subtle to actually notice, per user complaint "you
    # can't see wave speed"). Still not the main way motion reads now - see
    # CREST_AMOUNT_RANGE below for the more visible part of that fix.
    SPEED_SCALE_RANGE = (0.5, 4.0)
    # A real travelling crest layered on top of the noise field - sine, not
    # noise, deliberately (an exception to this file's own "avoid
    # mechanical sine waves" philosophy, see its header docstring) because
    # the whole point is a band that visibly *sweeps* across the strip, not
    # another organic drift indistinguishable from the base layer. Its
    # amount is gated by motion (near-zero at rest, so it doesn't sit there
    # looping and reading as mechanical when nobody's around) rather than
    # constant, which is what keeps it from fighting that philosophy in
    # practice - it's a transient accent, not the resting state.
    CREST_WAVENUMBER_SCALE = 2.5   # spatial frequency relative to the base noise's own `scale` - higher = tighter, more distinct bands
    CREST_SPEED_SCALE = 6.0        # how much faster the crest travels than the base noise drift, at the same motion-driven speed_scale
    CREST_AMOUNT_RANGE = (0.0, 0.9)  # invisible at rest -> a prominent bright band at full motion

    def __init__(self, n_pixels, palette, seed=0, speed=0.03, scale=None, highlight=True,
                 ceiling_floor=0.15, ceiling_decay_per_second=0.02, noise_spread=0.3):
        self.n = n_pixels
        self.lut = _palette_lut(palette)
        self.seed = seed
        self.speed = speed
        self.scale = scale if scale is not None else 2.5 / max(n_pixels, 1)
        self.highlight = highlight
        self.noise_spread = noise_spread
        self.t = 0.0
        self._ceiling = ceiling_floor
        self._ceiling_floor = ceiling_floor
        self._ceiling_decay_per_second = ceiling_decay_per_second

    def step(self, loudness: float = 0.0, motion: float = 0.0, env_brightness: float = 0.5, ripple: float = 0.0):
        motion = min(max(motion, 0.0), 1.0)
        speed_scale = scaled(motion, *self.SPEED_SCALE_RANGE)
        brightness_scale = scaled(env_brightness, 0.5, 1.0)  # never fully dark - this is ambient decor, not a screen

        # Self-calibrating ceiling: expands instantly on a new peak (so one
        # loud moment immediately unlocks the top of the palette), decays
        # slowly back down otherwise (so an earlier loud spell doesn't
        # permanently compress everything afterwards into a narrow low
        # band) - never below ceiling_floor, so there's always at least a
        # usable range even in a totally silent room.
        if loudness > self._ceiling:
            self._ceiling = loudness
        else:
            self._ceiling = max(
                self._ceiling_floor,
                self._ceiling - self._ceiling * self._ceiling_decay_per_second * self.TICK_SECONDS,
            )
        loudness_pos = min(1.0, loudness / self._ceiling) if self._ceiling > 0 else 0.0

        x = np.arange(self.n)
        layer1 = value_noise(x * self.scale, np.full(self.n, self.t), seed=self.seed)
        layer2 = value_noise(x * self.scale * 2.3 + 50, np.full(self.n, self.t * 1.7), seed=self.seed + 1)
        combined = 0.65 * layer1 + 0.35 * layer2
        # Anchored on loudness_pos rather than spanning the whole palette on
        # noise alone (the old organic_wave's approach) - the noise now
        # supplies organic wander AROUND that anchor so the wave still
        # flows/breathes instead of snapping to a flat colour, but loudness
        # is what actually decides which region of the palette it's in.
        t_norm = np.clip(loudness_pos + combined * self.noise_spread, 0, 1)
        indices = (t_norm * (len(self.lut) - 1)).astype(int)
        frame = self.lut[indices].copy().astype(float)
        if self.highlight:
            agreement = np.clip(layer1 * layer2, 0, None)
            frame = np.clip(frame + (agreement * 70)[:, None], 0, 255)

        # Travelling crest - see CREST_* constants' comments for why this
        # is sine rather than noise, and why it's motion-gated rather than
        # constant. A moving bright band sweeping along the strip, visible
        # and clearly directional even at a glance, unlike the noise
        # field's speed_scale alone.
        crest_amount = scaled(motion, *self.CREST_AMOUNT_RANGE)
        if crest_amount > 0:
            crest_phase = x * self.scale * self.CREST_WAVENUMBER_SCALE - self.t * self.CREST_SPEED_SCALE
            crest = 0.5 + 0.5 * np.sin(crest_phase)  # 0..1 travelling band
            frame = np.clip(frame + (crest * crest_amount * 100)[:, None], 0, 255)

        frame = np.clip(frame * brightness_scale, 0, 255)

        if ripple > 0:
            frame = frame + (255 - frame) * ripple * 0.8  # wash toward white without fully overriding the wave's own colour

        self.t += self.speed * speed_scale
        return np.clip(frame, 0, 255).astype(np.uint8)


class OrganicCometEffect:
    """Sub-pixel position (so slow speeds don't look stepped), a two-pixel
    soft head instead of a single hard-set pixel, and a slow noise-driven
    speed wobble so it doesn't feel metronomic."""

    def __init__(self, n_pixels, palette, speed=0.12, decay=0.93, background_level=0.05, seed=0):
        self.n = n_pixels
        self.lut = _palette_lut(palette)
        self.speed = speed
        self.decay = decay
        self.background = self.lut[0] * background_level
        self.trail = np.zeros(n_pixels)
        self.pos = 0.0
        self.seed = seed
        self.t = 0.0

    def step(self, intensity: float = 1.0):
        """intensity is the current activity_level (0..1) - quiet rooms get
        a slower, dimmer comet; lively rooms get a faster, brighter one."""
        speed_scale = scaled(intensity, 0.3, 1.2)
        brightness_scale = scaled(intensity, 0.4, 1.2)

        wobble = 0.35 * value_noise(np.array([self.t]), np.array([0.0]), seed=self.seed)[0]
        self.pos = (self.pos + self.speed * speed_scale * (1 + wobble)) % self.n
        self.trail *= self.decay
        idx0 = int(np.floor(self.pos)) % self.n
        idx1 = (idx0 + 1) % self.n
        frac = self.pos - np.floor(self.pos)
        self.trail[idx0] = max(self.trail[idx0], 1 - frac)
        self.trail[idx1] = max(self.trail[idx1], frac)
        palette_index = int((self.pos / self.n) * (len(self.lut) - 1))
        comet_color = self.lut[palette_index]
        frame = self.background[None, :] + (comet_color - self.background)[None, :] * self.trail[:, None]
        frame = frame * brightness_scale
        self.t += 0.4
        return np.clip(frame, 0, 255).astype(np.uint8)


class OrganicTwinkleEffect:
    """Each star gets its own randomised fade rate and peak brightness at
    the moment it ignites, instead of every star behaving identically."""

    def __init__(self, n_pixels, palette, spawn_prob=0.01, decay_range=(0.94, 0.985),
                 peak_range=(0.55, 1.0), background_level=0.03):
        self.n = n_pixels
        self.lut = _palette_lut(palette)
        self.spawn_prob = spawn_prob
        self.decay_range = decay_range
        self.peak_range = peak_range
        self.background = self.lut[0] * background_level
        self.brightness = np.zeros(n_pixels)
        self.hue_index = np.zeros(n_pixels, dtype=int)
        self.decay = np.full(n_pixels, 0.96)
        self.peak = np.ones(n_pixels)

    def step(self, intensity: float = 1.0):
        """intensity is the current activity_level (0..1) - quiet rooms get
        fewer, dimmer stars; lively rooms get more, brighter ones."""
        spawn_scale = scaled(intensity, 0.3, 1.7)
        brightness_scale = scaled(intensity, 0.4, 1.2)

        self.brightness *= self.decay
        spawn = np.random.random(self.n) < self.spawn_prob * spawn_scale
        n_spawn = int(spawn.sum())
        if n_spawn:
            self.brightness[spawn] = 1.0
            self.hue_index[spawn] = np.random.randint(0, len(self.lut), size=n_spawn)
            self.decay[spawn] = np.random.uniform(*self.decay_range, size=n_spawn)
            self.peak[spawn] = np.random.uniform(*self.peak_range, size=n_spawn)
        star_colors = self.lut[self.hue_index]
        effective_brightness = self.brightness * self.peak
        frame = self.background[None, :] + (star_colors - self.background) * effective_brightness[:, None]
        frame = frame * brightness_scale
        return np.clip(frame, 0, 255).astype(np.uint8)


class ReactiveGlowEffect:
    """A single evolving colour that reacts directly to intensity - no
    positional/spatial computation at all, unlike OrganicWaveEffect/
    OrganicCometEffect (which vary colour *across pixel positions*, and so
    have nothing meaningful to do on a single-pixel DMX zone - or in
    organic_comet's case, a genuine bug there: its "head" and "next" pixel
    indices collide onto the same single cell when n_pixels=1, injecting
    an artificially high trail value every tick that fights the decay and
    reads as "always creeping bright" rather than a clean reactive flash).
    Built for a single DMX fixture - one point of light, not a strip -
    where a positional wave/comet effect literally has no "position" to
    vary across.

    Idle sits near the palette's cool/low end at a dim background level.
    Intensity pushes toward the palette's hot/bright end and brighter.
    Attack and decay are asymmetric on purpose: fast attack so a real
    shake/sound spike reads as an immediate response, slower decay so it
    eases back rather than snapping off - a sense of continuity between
    old and new state, not a jarring cut."""

    def __init__(self, n_pixels, palette, attack=0.5, decay=0.08, idle_level=0.1):
        self.n = n_pixels
        self.lut = _palette_lut(palette)
        self.attack = attack
        self.decay = decay
        self.idle_level = idle_level
        self.level = idle_level

    def step(self, intensity: float = 0.0):
        intensity = min(max(intensity, 0.0), 1.0)
        target = self.idle_level + (1.0 - self.idle_level) * intensity
        rate = self.attack if target > self.level else self.decay
        self.level += (target - self.level) * rate

        index = int(self.level * (len(self.lut) - 1))
        color = self.lut[index].astype(float)
        brightness = 0.25 + 0.75 * self.level
        frame = np.tile(color * brightness, (self.n, 1))
        return np.clip(frame, 0, 255).astype(np.uint8)


class TriArmGlideEffect:
    """For the accelerometer zone's LED arms radiating from a shared hub,
    spliced into the same continuous APA102 chain as the heart_rate zone
    (not a standalone DMX fixture like the single-bar DirectionalWaveEffect
    this replaces) - see accel_stick.ino's atan2-based `angle_deg` and
    config.py's accelerometer zone. Name/class kept as "tri" from the
    original 3-arm-at-120-degrees design even though the physical build
    (confirmed 2026-08-07) settled on 4 arms at ergonomics/visual-design-
    driven angles, not an even split - ARM_COUNT below is what actually
    governs arm count, not the class name.

    `angle` arrives pre-rescaled to 0..1 by main.py's _resolve_one_source
    (the zone's source config supplies {min: 0, max: 360} - see config.py)
    and is converted back to radians here. `intensity` is the shake's
    magnitude, same signal DirectionalWaveEffect used.

    REDESIGNED 2026-08-17 from a continuous per-tick reactive glide (every
    tick's raw intensity/angle immediately nudged a head further along
    whichever arm(s) it aligned with, no notion of "a swing" as a discrete
    thing) to a discrete swing-detect-then-fire-a-beam model instead: real
    handheld shakes are noisy tick to tick, so reacting to every instant
    made the response feel twitchy/glitchy rather than deliberate.

    SWING DETECTION: `intensity` is watched with hysteresis (two
    thresholds, not one - same "a single fixed cutoff flickers on
    borderline readings" reasoning as accel_stick.ino's SWING_INVITE
    thresholds) - crossing above SWING_START_THRESHOLD marks a swing as
    "in progress" and starts buffering every (intensity, angle) sample;
    crossing back below the lower SWING_END_THRESHOLD marks it finished.
    At that point, the buffer's TOP_K_SAMPLES highest-intensity readings -
    the actual peak of the swing, not its noisy ramp-up/ramp-down - get
    collapsed into one clean (direction, speed) pair: speed is their plain
    mean; direction is a *circular* mean (averaging each sample's unit
    vector, intensity-weighted, then atan2 back), since a plain average of
    angles breaks near the 0/360 wraparound (e.g. averaging 350 degrees and
    10 degrees directly gives 180 - exactly backwards - instead of the
    correct 0). That one summarised reading, not the raw stream, is what
    drives the light.

    BEAM RENDERING: the finalised (direction, speed) spawns one travelling
    beam per arm whose cosine-weighted alignment to that direction clears
    MIN_ARM_WEIGHT - same clamped-cosine falloff idea as before (see the
    dead-zone/overlap caveat below, still just as true here), just used to
    decide which arm(s) get a beam at all rather than a continuous
    per-tick weight. Each arm's beam travels hub -> tip at a speed set by
    (swing speed * that arm's own alignment weight), with brightness and
    pixel-width scaled the same way - a harder, more on-target swing
    produces a beam that's faster, brighter, AND wider (more pixels lit at
    once), not just brighter. Reaching the tip ends that arm's beam. A new
    swing detected on an arm already mid-beam just replaces it outright,
    no blending between the two.

    Each arm claims a smooth ~180-degree-wide slice of the circle centred
    on its own spoke, via a clamped cosine falloff: weight = max(0,
    cos(angle - arm_centre)). This self-limiting-to-two-arms property (each
    weight window is exactly 180 degrees wide, so two windows only overlap
    if their centres are within 180 degrees of each other - never all-zero,
    never more than two nonzero at typical spacings) only strictly holds
    when arms are evenly spaced *and* no closer together than 120 degrees;
    with ARM_ANGLES_DEG now arbitrary/ergonomic rather than an even split,
    arms placed closer together than that can overlap into 3+ simultaneously
    weighted arms, or (if two arms end up more than 180 degrees apart from
    every other arm) leave a dead gap where no arm has positive weight for a
    stretch of angles. With the confirmed angles (20/45/90/135, all bunched
    into a 115-degree span) this isn't hypothetical - checked numerically:
    3+ arms carry positive weight simultaneously across most of the 0-135
    degree arc (unsurprising, given how close together 20/45/90/135 are),
    and there's a genuine ~65-degree dead zone from about 226 to 290 degrees
    (roughly opposite the arm cluster) where every arm's weight is exactly
    0 - swinging the stick into that range currently lights nothing. Test on
    real hardware once flashed; if the dead zone or overlap reads badly, a
    narrower falloff than a plain cosine (e.g. raising the cos term to a
    power before clamping) is the fix, not a change to the angles
    themselves - the dead zone in particular may be fine as-is if that
    swing direction isn't ergonomically reachable anyway.

    ARM_LENGTHS gives each arm's pixel count explicitly (confirmed
    2026-08-10 post-soldering: 36/20/16/22, physically unequal, so the old
    "split n_pixels evenly across ARM_COUNT" approach doesn't apply) - arm
    order here is the physical chain order (arm 0 is whichever arm's pixels
    the data line reaches first out of accelerometer_strip, not an arbitrary
    label), and ARM_ANGLES_DEG/ARM_REVERSED below are given in that same
    order. If the actual zone's n_pixels doesn't match sum(ARM_LENGTHS) (e.g.
    a config typo), the last arm absorbs the difference so the strip is
    still exactly filled, same "pad the odd one out" idea output_loop uses
    for a mismatched zone.

    ARM_ANGLES_DEG (confirmed 2026-08-10, approximate - a closer on-site
    remeasurement is still expected, these are eyeballed not precision-
    measured) and ARM_REVERSED (confirmed 2026-08-10) - angles are
    ergonomic/visual-design choices, not an even split (20/45/90/135
    degrees - arm 3 at 90 is straight up), and wiring direction is driven by
    physical routing/cable-run constraints, not a simple alternating
    pattern (arms 2 and 4 are wired tip-to-hub, 1 and 3 hub-to-tip, per
    which end of each arm the physical cable run made easiest to reach).
    To refine the angles further: swing the stick toward each arm in turn
    and watch `sensors.angle_deg` on the admin dashboard, and update the
    entry below to whatever it actually reads."""

    ARM_COUNT = 4
    ARM_LENGTHS = (36, 20, 16, 22)                  # confirmed 2026-08-10 post-soldering - see docstring, chain order
    ARM_ANGLES_DEG = (20.0, 45.0, 90.0, 135.0)      # confirmed 2026-08-10, approximate - see docstring
    ARM_REVERSED = (False, True, False, True)       # confirmed 2026-08-10 - arms 2 and 4 wired tip-to-hub, see docstring
    TICK_SECONDS = 0.05                    # matches main.py's output_loop's fixed 20Hz - see PulseEffect's own note on this same assumption

    # Swing detection - all untuned placeholders (feel, not physics, same
    # caveat as accel_stick.ino's SENSITIVITY) - tune against a real swing
    # once flashed and wired.
    SWING_START_THRESHOLD = 0.15   # intensity must climb above this to count as a swing beginning
    SWING_END_THRESHOLD = 0.08     # then drop back below this to finalise it - see step()'s docstring on why two thresholds, not one
    PEAK_WINDOW_SECONDS = 1.5      # how far back the sample buffer reaches - must comfortably cover one real swing's whole duration
    TOP_K_SAMPLES = 4              # how many of the buffer's highest-intensity samples define the finished swing's direction/speed
    MIN_ARM_WEIGHT = 0.15          # below this cosine-alignment to the swing direction, an arm doesn't get a beam at all

    # Beam rendering - also untuned placeholders.
    BEAM_SPEED_SCALE = 1.6         # pixels/tick at (swing speed * arm weight) = 1.0
    BEAM_WIDTH_MIN_PX = 2          # pixels lit around the beam's leading edge at driven strength -> 0
    BEAM_WIDTH_MAX_PX = 10         # ... at driven strength -> 1 - "more pixels pack" for a harder/more-aligned swing
    BEAM_DECAY = 0.88              # per-tick trail fade behind a travelling beam

    def __init__(self, n_pixels, palette, background_level=0.05):
        self.lut = _palette_lut(palette)
        self.background = self.lut[0] * background_level
        self.trail = np.zeros(n_pixels)

        lengths = list(self.ARM_LENGTHS)
        lengths[-1] += n_pixels - sum(lengths)  # last arm absorbs any mismatch against the zone's real pixel count
        starts = np.cumsum([0] + lengths[:-1]).tolist()
        self.arm_ranges = list(zip(starts, lengths))  # (start, length) per arm, in the shared trail array
        self.arm_center_rad = [np.radians(deg) for deg in self.ARM_ANGLES_DEG]

        buffer_len = max(1, int(self.PEAK_WINDOW_SECONDS / self.TICK_SECONDS))
        self._buffer = collections.deque(maxlen=buffer_len)  # (intensity, angle_rad) samples, rolling
        self._in_swing = False
        self._beams = {}  # arm_index -> {"pos", "speed", "width", "brightness"} for each currently-travelling beam

    def _to_index(self, arm_idx: int, dist: int) -> int:
        """Hub-relative distance along one arm -> real index in the shared trail array."""
        start, length = self.arm_ranges[arm_idx]
        return start + (length - 1 - dist) if self.ARM_REVERSED[arm_idx] else start + dist

    def _finalise_swing(self):
        """Collapse the buffered samples from the swing that just ended into
        one (direction, speed) pair and spawn a beam on every arm that's
        actually aligned with it - see the class docstring's SWING
        DETECTION / BEAM RENDERING sections for the reasoning."""
        if not self._buffer:
            return
        top = sorted(self._buffer, key=lambda sample: sample[0], reverse=True)[:self.TOP_K_SAMPLES]
        speed = sum(sample[0] for sample in top) / len(top)
        sin_sum = sum(sample[0] * np.sin(sample[1]) for sample in top)
        cos_sum = sum(sample[0] * np.cos(sample[1]) for sample in top)
        mean_angle_rad = np.arctan2(sin_sum, cos_sum)

        for k in range(self.ARM_COUNT):
            _, length = self.arm_ranges[k]
            if length == 0:
                continue
            weight = max(0.0, np.cos(mean_angle_rad - self.arm_center_rad[k]))
            if weight < self.MIN_ARM_WEIGHT:
                continue
            driven = speed * weight
            self._beams[k] = {
                "pos": 0.0,
                "speed": driven,
                "width": self.BEAM_WIDTH_MIN_PX + (self.BEAM_WIDTH_MAX_PX - self.BEAM_WIDTH_MIN_PX) * driven,
                "brightness": scaled(driven, 0.3, 1.0),
            }

    def step(self, intensity: float = 0.0, angle: float = 0.0):
        intensity = min(max(intensity, 0.0), 1.0)
        angle = min(max(angle, 0.0), 1.0)
        angle_rad = angle * 2 * np.pi
        self._buffer.append((intensity, angle_rad))

        if not self._in_swing and intensity > self.SWING_START_THRESHOLD:
            self._in_swing = True
        elif self._in_swing and intensity < self.SWING_END_THRESHOLD:
            self._in_swing = False
            self._finalise_swing()
            self._buffer.clear()  # next swing's peak window starts clean, not mixed with this one's tail

        self.trail *= self.BEAM_DECAY
        for k in list(self._beams.keys()):
            _, length = self.arm_ranges[k]
            beam = self._beams[k]
            beam["pos"] += beam["speed"] * self.BEAM_SPEED_SCALE
            if beam["pos"] >= length - 1:
                del self._beams[k]  # reached the tip - done
                continue
            lo = max(0, int(beam["pos"] - beam["width"] / 2))
            hi = min(length - 1, int(beam["pos"] + beam["width"] / 2))
            for dist in range(lo, hi + 1):
                idx = self._to_index(k, dist)
                self.trail[idx] = max(self.trail[idx], beam["brightness"])

        frame = np.tile(self.background, (len(self.trail), 1))
        for k in range(self.ARM_COUNT):
            start, length = self.arm_ranges[k]
            if length == 0:
                continue
            # Each arm gets a distinct spot in the palette gradient, so which
            # arm is lit reads visually too, not just spatially - same idea
            # DirectionalWaveEffect used direction for, just per-arm instead.
            palette_index = int((k / max(1, self.ARM_COUNT - 1)) * (len(self.lut) - 1))
            color = self.lut[palette_index].astype(float)
            seg_trail = self.trail[start:start + length][:, None]
            frame[start:start + length] = self.background[None, :] + (color - self.background)[None, :] * seg_trail

        return np.clip(frame, 0, 255).astype(np.uint8)


class PulseEffect:
    """Colour AND brightness both sweep along the palette by how "deep" into
    the current beat's flash we are - idle sits near the palette's first
    (coolest/dimmest) anchor, each heartbeat's attack sweeps toward the
    last (hottest/brightest) anchor and eases back. Uses the whole palette
    (previously only ever rendered palette[-1] as a single held colour
    scaled by brightness, wasting every other anchor - a 5-colour palette
    like festive looked identical to a 1-colour one).

    `bpm` arrives pre-rescaled to 0..1 by main.py's _resolve_one_source
    (the zone's source config supplies the {min, max} real-BPM range - see
    config.py's heart_rate zone) - BPM_RANGE here must match that config
    so the two ends agree on what 0.0/1.0 mean.

    TICK_SECONDS assumes the fixed 20Hz cadence every effect in this file
    is driven at (main.py's led_loop) - effects don't receive an actual dt,
    so this mirrors the same "fixed tick" assumption self.t increments
    elsewhere in this file already make."""

    TICK_SECONDS = 0.05
    BPM_RANGE = (40.0, 180.0)

    def __init__(self, n_pixels, palette, idle_level=0.15, ease=0.15, decay_rate=6.0):
        self.n = n_pixels
        self.lut = _palette_lut(palette)
        self.idle_level = idle_level
        self.ease = ease
        self.decay_rate = decay_rate
        self.level = idle_level
        self.phase = 0.0

    def _color_at_level(self, level: float):
        index = int(min(max(level, 0.0), 1.0) * (len(self.lut) - 1))
        return self.lut[index].astype(float)

    def step(self, intensity: float = 0.0, bpm: float = 0.5):
        engaged = intensity > 0.5
        if not engaged:
            self.phase = 0.0  # next contact starts on a fresh beat, not mid-cycle
            self.level += (self.idle_level - self.level) * self.ease
            frame = np.tile(self._color_at_level(self.level), (self.n, 1))
            return np.clip(frame, 0, 255).astype(np.uint8)

        real_bpm = self.BPM_RANGE[0] + min(max(bpm, 0.0), 1.0) * (self.BPM_RANGE[1] - self.BPM_RANGE[0])
        beat_period = 60.0 / real_bpm
        self.phase = (self.phase + self.TICK_SECONDS / beat_period) % 1.0
        pulse = np.exp(-self.phase * self.decay_rate)  # bright flash at phase 0, fading before the next beat
        target = self.idle_level + (1.0 - self.idle_level) * pulse
        self.level += (target - self.level) * self.ease  # ease avoids a hard jump/flicker frame-to-frame
        frame = np.tile(self._color_at_level(self.level), (self.n, 1))
        return np.clip(frame, 0, 255).astype(np.uint8)


class HeartRateEffect:
    """A "check-in" flow rather than PulseEffect's continuous contact-
    follows-BPM display (kept as its own class/registry name for the same
    reason AudioReactiveWaveEffect isn't a repurposed organic_wave - `pulse`
    stays selectable elsewhere in its original shape). Four phases, cycling
    on a *rising edge* of `intensity` (heart_rate.engaged - already
    debounced against brief finger-contact blips by main.py's hr_tracker,
    see its own ActivationTracker) so holding a finger down after a
    completed reading doesn't immediately restart another one - lift and
    reapply to take a new reading:

      idle        - palette-coloured pulse at the rolling average of the
                    last HISTORY_SIZE completed readings (falls back to a
                    resting 70 bpm before any reading's ever completed) -
                    the "ambient" display, not tied to live contact at all.
      start_chime - two quick white flashes, contact just detected.
      reading     - white pulse at the LIVE bpm for READING_SECONDS,
                    sampling bpm for this session's average. Distinct
                    colour from idle is the point: white unambiguously
                    means "reading you right now, hold still".
      end_chime   - two quick white flashes; the session's mean bpm is
                    appended to history before returning to idle.

    If contact drops during `reading` before READING_SECONDS elapses, the
    session's abandoned (no history update, no end chime) rather than
    recording a partial sample as if it were a real reading."""

    HISTORY_SIZE = 20
    READING_SECONDS = 10.0
    CHIME_SECONDS = 0.5
    TICK_SECONDS = 0.05
    BPM_RANGE = (40.0, 180.0)  # must match config.yaml's heart_rate zone bpm {min, max}
    IDLE_FALLBACK_BPM = 70.0  # shown at rest before any reading's ever completed

    # How long one full drift across the *entire* palette takes, in the
    # idle phase - deliberately a totally separate clock from the
    # heartbeat (see HUE_RANGE's replacement here, 2026-08-17 v2): the
    # original bug was indexing the whole palette with the beat's own
    # brightness envelope, so hue swept end to end on every single beat -
    # looked glitchy. Narrowing to one fixed hue band (2026-08-17 v1) fixed
    # the glitch but killed the "explore the palette" feeling entirely -
    # per user follow-up: "I still want a palette inspiration colour and
    # maybe it pulses and gradually changes throughout the palette
    # spectrum". This is that: hue now drifts across the full gradient on
    # its own slow independent timer (`self.hue_phase`, advanced once per
    # tick regardless of pulse state), while `level` (the fast, beat-
    # synced envelope) only ever controls brightness. Slow enough that
    # motion is never abrupt, fast enough to actually notice over a
    # minute or so of watching - retune per palette/taste.
    HUE_CYCLE_SECONDS = 45.0
    # Ceiling on the pulse's peak brightness - not 1.0 on purpose (per user
    # request 2026-08-17: the un-capped version read as "very
    # bright"/distracting up close). Applies to the white `reading` phase
    # too, dimmed but still clearly distinct from the idle phase's colour.
    MAX_BRIGHTNESS = 0.6

    def __init__(self, n_pixels, palette, idle_level=0.15, ease=0.7, decay_rate=3.5):
        self.n = n_pixels
        self.lut = _palette_lut(palette)
        self.idle_level = idle_level
        # Raised from 0.15 (2026-08-17 v3) - `level` is what actually
        # reaches the display, but at 0.15 it only tracks 15% of the way
        # toward `target` each tick, and target's own oscillation (1.0 down
        # to idle_level every single beat) happens on roughly the same
        # timescale as that catch-up - so most of the swing got smoothed
        # away before it ever showed up, leaving a barely-there ~9-17
        # brightness ripple (out of a possible ~150) that read as no
        # perceptible rhythm at all. 0.7 lets level track target closely
        # enough that the beat is clearly recognisable while decay_rate
        # (below) still keeps each individual pulse's shape gentle rather
        # than a sharp snap.
        self.ease = ease
        # Lower than a sharp cardiac-monitor-style blip (was 6.0 until
        # 2026-08-17) - a smaller decay_rate stretches the bright phase of
        # each beat out over more of its cycle instead of flashing and
        # snapping straight back down, reading as a gentle breathing glow
        # rather than a sharp, distracting flash. See step()'s docstring
        # for the exp(-phase * decay_rate) shape this controls.
        self.decay_rate = decay_rate
        self.level = idle_level
        self.phase = 0.0
        self.hue_phase = 0.0  # 0..1, own slow independent clock - see HUE_CYCLE_SECONDS
        self.history = collections.deque(maxlen=self.HISTORY_SIZE)
        self._was_engaged = False
        self._chime_elapsed = 0.0
        self._reading_elapsed = 0.0
        self._reading_samples = []
        self._state = "idle"

    def _color_at_level(self, level: float, white: bool = False):
        """`level` (0..1, the pulse's current fast brightness envelope)
        only ever scales brightness now. Hue - for the non-white idle
        phase - comes from `self.hue_phase` instead, a separate slow clock
        advanced once per tick in step() (see HUE_CYCLE_SECONDS), so
        colour drifts gradually across the whole palette independently of
        the heartbeat's own rhythm. white=True (the `reading` phase)
        genuinely brightness-pulses too, rather than statically ignoring
        `level` and always returning solid white regardless of pulse
        phase."""
        level = min(max(level, 0.0), 1.0)
        if white:
            base = np.array([255.0, 255.0, 255.0])
        else:
            idx = int(self.hue_phase * (len(self.lut) - 1))
            base = self.lut[idx].astype(float)
        return base * level * self.MAX_BRIGHTNESS

    def _pulse_frame(self, real_bpm: float, white: bool):
        beat_period = 60.0 / real_bpm
        self.phase = (self.phase + self.TICK_SECONDS / beat_period) % 1.0
        pulse = np.exp(-self.phase * self.decay_rate)
        target = self.idle_level + (1.0 - self.idle_level) * pulse
        self.level += (target - self.level) * self.ease
        color = self._color_at_level(self.level, white=white)
        return np.tile(color, (self.n, 1))

    def _chime_frame(self):
        # Two flashes across CHIME_SECONDS - a deliberately different
        # rhythm from the smooth BPM sine so it reads as a distinct "chime"
        # cue, not just another heartbeat. Now scaled by MAX_BRIGHTNESS
        # (2026-08-18 - was full uncapped 255,255,255, an oversight from
        # when MAX_BRIGHTNESS was added elsewhere in this class but not
        # here) - the chime was consistently, reproducibly brighter than
        # the rest of the effect every single time it fired, which is also
        # where a real hardware white-balance skew (many cheap addressable
        # RGB LEDs render full-strength white with a visible blue tint,
        # since the blue die is typically more efficient than red/green at
        # matched signal levels) would be most visible - full brightness is
        # exactly where that skew shows up strongest.
        quarter = self.CHIME_SECONDS / 4
        flash_on = (self._chime_elapsed % (2 * quarter)) < quarter
        level = 1.0 if flash_on else 0.0
        return np.tile(np.array([255.0, 255.0, 255.0]) * level * self.MAX_BRIGHTNESS, (self.n, 1))

    def step(self, intensity: float = 0.0, bpm: float = 0.5):
        # Advanced unconditionally, every tick, regardless of phase - a
        # clock fully independent of the heartbeat/state machine below, see
        # HUE_CYCLE_SECONDS's comment.
        self.hue_phase = (self.hue_phase + self.TICK_SECONDS / self.HUE_CYCLE_SECONDS) % 1.0

        engaged = intensity > 0.5
        rising_edge = engaged and not self._was_engaged
        self._was_engaged = engaged

        if self._state == "idle" and rising_edge:
            self._state = "start_chime"
            self._chime_elapsed = 0.0

        if self._state == "idle":
            avg_bpm = sum(self.history) / len(self.history) if self.history else self.IDLE_FALLBACK_BPM
            real_bpm = min(max(avg_bpm, self.BPM_RANGE[0]), self.BPM_RANGE[1])
            frame = self._pulse_frame(real_bpm, white=False)

        elif self._state == "start_chime":
            frame = self._chime_frame()
            self._chime_elapsed += self.TICK_SECONDS
            if self._chime_elapsed >= self.CHIME_SECONDS:
                self._state = "reading" if engaged else "idle"
                self._reading_elapsed = 0.0
                self._reading_samples = []

        elif self._state == "reading":
            if not engaged:
                self._state = "idle"  # abandoned - contact lost mid-reading, nothing recorded
                frame = self._pulse_frame(self.IDLE_FALLBACK_BPM, white=False)
            else:
                real_bpm = self.BPM_RANGE[0] + min(max(bpm, 0.0), 1.0) * (self.BPM_RANGE[1] - self.BPM_RANGE[0])
                self._reading_samples.append(real_bpm)
                frame = self._pulse_frame(real_bpm, white=True)
                self._reading_elapsed += self.TICK_SECONDS
                if self._reading_elapsed >= self.READING_SECONDS:
                    self.history.append(sum(self._reading_samples) / len(self._reading_samples))
                    self._state = "end_chime"
                    self._chime_elapsed = 0.0

        else:  # end_chime
            frame = self._chime_frame()
            self._chime_elapsed += self.TICK_SECONDS
            if self._chime_elapsed >= self.CHIME_SECONDS:
                self._state = "idle"

        return np.clip(frame, 0, 255).astype(np.uint8)


class TempHumidityBarEffect:
    """Two display modes for the weather zone's DMX bar, switched between by
    an internal timer - live current-weather most of the time, with a
    periodic animated reveal of the past 24h while someone's present.

    LIVE MODE (default, and whenever `history` isn't available yet):
    `temperature` picks a position along the palette gradient (cool end <->
    warm end), `humidity` scales overall brightness - same base colour
    across every segment (was originally imagined as a spatial LED matrix
    panel - hence the class's old name - but the physical build settled on
    a DMX bar fixture instead; this effect never did real 2D addressing, so
    that rename was a pure naming fix). On top of that base colour:

    - `condition` (weather.py's _CONDITION_ORDER index, rescaled to 0..1 by
      main.py's {path, min: 0, max: 5} source - see CONDITION_ORDER below,
      which must stay in the same order) adds a per-condition texture on
      top of the base colour via _condition_texture: clear is a flat calm
      glow (a no-op multiplier, identical to this effect before condition
      existed), cloudy/fog dim the whole bar down, rain/storm add a fast
      noise-driven flicker (storm faster/stronger than rain), snow a
      slower, softer one. Texture only ever brightens above the base
      (clipped noise, `1.0 + ...`) so it reads as sparkle/flicker layered
      on the weather colour, never darkens it unpredictably.
    - `activity` (state.activity_level - same signal the ambient zone
      reacts to, so this zone's liveliness stays in sync with it rather
      than reading as a separate, disconnected zone) drives its own subtle
      per-segment shimmer, independent of condition - a calm room stays
      essentially flat regardless of weather, a lively one gets visible
      noise-driven variation across segments.
    - `contrast` (how far indoor temperature has drifted from outdoor - see
      main.py's indoor_outdoor_temp_diff) pushes the displayed colour
      further toward whichever end of the palette it's already closest to
      - "the room vs the world" reads as a more saturated/extreme colour
      the more indoor conditions diverge from outside. At contrast=0 (or no
      weather data at all) this is a no-op - identical to the colour before
      this existed.

    REPLAY MODE: every REPLAY_INTERVAL_SECONDS of live mode while `activated`
    (state.activated via main.py's ActivationTracker - someone's actually
    present, not just ambient idle) is true, the bar switches to a
    REPLAY_DURATION_SECONDS animated reveal of `history` (weather.py's past-
    24h hourly series, passed through unrescaled via a {path, raw: true}
    source - see main.py's _resolve_one_source). This is a genuinely spatial
    use of the bar's segments, unlike live mode's one blended colour: history
    is resampled across all n segments (oldest at index 0, most recent at
    n-1) and revealed left-to-right as a growing count of lit segments, each
    holding its own historical temperature/humidity/condition rather than
    one shared colour - reads as a small temperature graph animating in,
    like a plotter drawing the last 24 hours. The just-revealed segment gets
    a brief brightness boost so the sweep still reads as progressing even
    though segment-by-segment reveal steps are coarse (n=28 over 60s is
    ~2s/segment - visibly stepped, not smooth, and that's fine here; this
    is meant to read as discrete hourly samples, not a continuous wave).
    Ends by resetting the live-mode timer, so the next replay is
    REPLAY_INTERVAL_SECONDS after this one *finishes*, not from when it
    started. If `history` is empty/None (weather sensor disabled, or no
    fetch has landed yet) replay never triggers - the fail-soft path is
    just "stay in live mode forever", not a crash or a blank bar.

    TICK_SECONDS assumes the same fixed 20Hz cadence every effect in this
    file is driven at (main.py's output_loop) - effects don't receive an
    actual dt, so both timers above increment by this fixed constant per
    step() call rather than by a measured elapsed time."""

    TICK_SECONDS = 0.05
    TEMPERATURE_RANGE = (15.0, 30.0)  # must match config's temp_humidity zone temperature {min, max} - history entries arrive in raw °C, live `temperature` arrives already rescaled by main.py, so history needs the same rescale done here
    HUMIDITY_RANGE = (20.0, 70.0)     # must match config's temp_humidity zone humidity {min, max}
    CONDITION_ORDER = ("clear", "cloudy", "fog", "rain", "snow", "storm")  # must match weather.py's _CONDITION_ORDER - index is what `condition` and each history entry's condition_code encode
    CONDITION_FLAT_BRIGHTNESS = (1.0, 0.75, 0.65, 0.85, 0.95, 0.8)  # per-CONDITION_ORDER-index flat multiplier used by replay's revealed segments (no animated texture there - see docstring)
    # Fixed, distinct colour per condition, same order as CONDITION_ORDER -
    # added 2026-08-17 so "what's the weather" reads as an immediate colour
    # identity at a glance, rather than being folded into the same hue
    # temperature was already using (the old design picked colour from
    # *temperature*, condition only added a subtle brightness texture - two
    # different things both landing in "colour-ish" territory read as
    # muddled/hard to parse, per user feedback: "we can't see or understand
    # what's happening"). Untuned placeholders, same as every other colour
    # choice in this file - adjust if any pair reads as too similar in
    # person.
    CONDITION_COLOURS = {
        "clear": (255, 200, 80),
        "cloudy": (140, 150, 160),
        "fog": (200, 200, 200),
        "rain": (70, 130, 220),
        "snow": (215, 235, 245),
        "storm": (95, 60, 140),
    }
    REPLAY_INTERVAL_SECONDS = 180.0  # time spent in live mode between replays while activated - 3 minutes
    REPLAY_DURATION_SECONDS = 60.0   # how long one full past-24h reveal takes

    def __init__(self, n_pixels, palette, background_level=0.05):
        self.n = n_pixels
        self.lut = _palette_lut(palette)
        self.t = 0.0
        self.background = self.lut[0] * background_level
        self._since_replay = 0.0  # seconds of live mode since the last replay ended (or since startup)
        self._replaying = False
        self._replay_elapsed = 0.0

    def _condition_index(self, condition: float) -> int:
        idx = int(round(min(max(condition, 0.0), 1.0) * (len(self.CONDITION_ORDER) - 1)))
        return min(max(idx, 0), len(self.CONDITION_ORDER) - 1)

    def _condition_texture(self, condition_idx: int) -> np.ndarray:
        """Brightness multiplier per segment, >= 1.0 always (see docstring
        on why texture only ever brightens, never darkens unpredictably)."""
        x = np.arange(self.n)
        name = self.CONDITION_ORDER[condition_idx]
        if name in ("rain", "storm"):
            speed, amp = (8.0, 0.5) if name == "storm" else (4.0, 0.3)
            noise = value_noise(x * 0.6, np.full(self.n, self.t * speed), seed=3)
            return 1.0 + np.clip(noise, 0, None) * amp
        if name == "snow":
            noise = value_noise(x * 0.3, np.full(self.n, self.t * 0.5), seed=4)
            return 1.0 + np.clip(noise, 0, None) * 0.25
        if name in ("cloudy", "fog"):
            return np.full(self.n, 0.7)
        return np.ones(self.n)  # clear

    # Width (in segments) and minimum visibility of the indoor-temperature
    # marker in _live_frame - see that method's docstring. Untuned, same
    # "feel not physics" caveat as everything else here.
    MARKER_WIDTH_SEGMENTS = 2.5
    MARKER_PROMINENCE_RANGE = (0.3, 1.0)  # always at least somewhat visible even at contrast=0, brighter as indoor/outdoor diverge more

    def _live_frame(self, temperature: float, humidity: float, activity: float, contrast: float, condition_idx: int):
        """Redesigned 2026-08-17 into three separated, individually legible
        channels (was one hue picked from *temperature*, with condition only
        adding a subtle brightness texture - two different things both
        landing in "which colour is this" territory read as muddled, per
        user feedback: "we can't see or understand what's happening"):

        1. COLOUR = a fixed, distinct colour per weather condition (see
           CONDITION_COLOURS) - "what's the weather" reads at a glance,
           consistent moment to moment rather than drifting with indoor
           temperature too.
        2. SHIMMER = the same per-condition animated texture as before
           (_condition_texture - rain/storm flicker, snow sparkles,
           cloudy/fog sit flat) layered on top as a brightness multiplier,
           plus `activity`'s own subtle noise-driven variation.
        3. MOVEMENT = a soft white marker sliding along the bar toward
           whichever end represents the current indoor temperature - "the
           room vs the world" is now a literal moving position instead of
           a colour shift, and `contrast` (how far indoor's diverged from
           outdoor) controls how prominent that marker is, rather than
           pushing the base hue toward an extreme."""
        x = np.arange(self.n)
        condition_name = self.CONDITION_ORDER[condition_idx]
        base_color = np.array(self.CONDITION_COLOURS[condition_name], dtype=float)

        brightness = 0.3 + 0.7 * min(max(humidity, 0.0), 1.0)
        shimmer = value_noise(x * 0.15, np.full(self.n, self.t))  # roughly -1..1 per segment
        shimmer_amount = 0.25 * activity  # calm: ~0, bar reads flat; lively: visible variation
        texture = self._condition_texture(condition_idx)

        pixel_brightness = brightness * texture * (1.0 + shimmer * shimmer_amount)
        frame = base_color[None, :] * pixel_brightness[:, None]

        t = min(max(temperature, 0.0), 1.0)
        marker_pos = t * (self.n - 1)
        marker_shape = np.clip(1.0 - np.abs(x - marker_pos) / self.MARKER_WIDTH_SEGMENTS, 0.0, 1.0)
        marker_prominence = scaled(min(max(contrast, 0.0), 1.0), *self.MARKER_PROMINENCE_RANGE)
        marker_color = np.array([255.0, 255.0, 255.0])
        frame = frame + (marker_color[None, :] - frame) * (marker_shape * marker_prominence)[:, None]

        return np.clip(frame, 0, 255).astype(np.uint8)

    def _replay_frame(self, history: list):
        n_hist = len(history)
        progress = min(1.0, self._replay_elapsed / self.REPLAY_DURATION_SECONDS)
        reveal_count = int(progress * self.n)

        frame = np.tile(self.background, (self.n, 1)).astype(float)
        t_lo, t_hi = self.TEMPERATURE_RANGE
        h_lo, h_hi = self.HUMIDITY_RANGE
        for i in range(reveal_count):
            entry = history[min(n_hist - 1, int(i / self.n * n_hist))]
            t_norm = min(max((entry.get("temperature", t_lo) - t_lo) / (t_hi - t_lo), 0.0), 1.0)
            h_norm = min(max((entry.get("humidity", h_lo) - h_lo) / (h_hi - h_lo), 0.0), 1.0)
            color = self.lut[int(t_norm * (len(self.lut) - 1))].astype(float)
            brightness = (0.3 + 0.7 * h_norm) * self.CONDITION_FLAT_BRIGHTNESS[entry.get("condition_code", 0)]
            if i == reveal_count - 1:
                brightness = min(1.4, brightness * 1.4)  # brief boost on the leading edge so the sweep reads as actively progressing
            frame[i] = color * brightness

        return np.clip(frame, 0, 255).astype(np.uint8)

    def step(self, temperature: float = 0.5, humidity: float = 0.5, activity: float = 0.0, contrast: float = 0.0,
              condition: float = 0.0, activated: float = 0.0, history: list = None):
        activity = min(max(activity, 0.0), 1.0)
        contrast = min(max(contrast, 0.0), 1.0)
        self.t += 0.01 + 0.04 * activity  # calm: near-static; lively: visibly drifting - also paces replay's condition-independent noise calls

        if self._replaying:
            self._replay_elapsed += self.TICK_SECONDS
            if self._replay_elapsed >= self.REPLAY_DURATION_SECONDS:
                self._replaying = False
                self._replay_elapsed = 0.0
                self._since_replay = 0.0  # next replay is REPLAY_INTERVAL_SECONDS after this one finishes, not from when it started
        else:
            self._since_replay += self.TICK_SECONDS
            if activated > 0.5 and history and self._since_replay >= self.REPLAY_INTERVAL_SECONDS:
                self._replaying = True
                self._replay_elapsed = 0.0

        if self._replaying:
            return self._replay_frame(history)
        return self._live_frame(temperature, humidity, activity, contrast, self._condition_index(condition))


if __name__ == "__main__":
    n_pixels = 60
    effects = {
        "organic wave": OrganicWaveEffect(n_pixels, PALETTES["winter"]),
        "organic comet": OrganicCometEffect(n_pixels, PALETTES["autumn"]),
        "organic twinkle": OrganicTwinkleEffect(n_pixels, PALETTES["festive"]),
    }
    for name, effect in effects.items():
        raw = effect.step()
        graded = apply_gamma(raw)
        print(f"{name}: raw {tuple(raw[10])} -> gamma-corrected {tuple(graded[10])}")
