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
    """Heart-rate globe. Rebuilt 2026-08-18 from scratch (a four-phase
    check-in flow - idle/start_chime/reading/end_chime, with an
    independently-clocked continuous hue drift and a separately-tuned
    brightness cap layered on top - had drifted into behaviour reported
    back as "glitchy... random and weird colours"; scrapping it rather than
    patching it further, per that feedback). Two modes only, switching
    directly on contact - no chime states:

      idle    - palette-coloured pulse at the rolling average of the last
                HISTORY_SIZE completed readings (falls back to
                IDLE_FALLBACK_BPM before any reading's ever completed).
                Colour steps to the next of COLOUR_STEPS evenly-spaced
                stops around the palette once per beat (not a continuous
                drift on its own separate clock) - "tick over a bit on the
                colour spectrum each time", cycling back to the start every
                COLOUR_STEPS beats.
      reading - white pulse at the LIVE bpm for READING_SECONDS, sampling
                bpm for this session's average, appended to history once
                the window completes. Starts on a rising edge of
                `intensity` (heart_rate.engaged - already debounced against
                brief contact blips by main.py's hr_tracker) and locks out
                a new session until contact is released and reapplied
                (`_reading_active`), same "lift and reapply for a new
                reading" behaviour as before. If contact drops mid-session,
                it's abandoned - no partial sample recorded.

    Brightness is a single smooth raised-cosine "breath" per beat - low at
    phase 0, peak exactly at phase 0.5 (the middle of the beat, "in time"),
    back to low at phase 1.0 - with no other smoothing/lag state on top
    (an earlier version eased brightness toward this target tick by tick,
    which meant one beat's level was still catching up when the colour
    switched at the next beat boundary - visually two colours blending
    into each other rather than one colour finishing its breath before the
    next began; per Julia's explicit "I don't want overlapping effects...
    single colour go bright and dim smoothly, then next colour" - dropped
    entirely rather than tuned, since the shape itself is already smooth
    and needs no further easing). One plain 0..1 brightness range
    throughout (idle_level floor to 1.0 peak), identical whether the pulse
    is palette-coloured or white, so "normal and standardised" doesn't mean
    something different per mode."""

    HISTORY_SIZE = 20
    READING_SECONDS = 10.0
    TICK_SECONDS = 0.05
    BPM_RANGE = (40.0, 180.0)  # must match config.yaml's heart_rate zone bpm {min, max}
    IDLE_FALLBACK_BPM = 70.0  # shown at rest before any reading's ever completed
    COLOUR_STEPS = 10  # beats to fully cycle the idle pulse's colour once around the palette, per Julia's "10 ticks" request

    def __init__(self, n_pixels, palette, idle_level=0.15):
        self.n = n_pixels
        self.lut = _palette_lut(palette)
        self.idle_level = idle_level
        self.phase = 0.0
        self.beat_count = 0  # increments once per completed beat cycle - drives the idle colour step, see _pulse_frame
        self.history = collections.deque(maxlen=self.HISTORY_SIZE)
        self._was_engaged = False
        self._reading_active = False
        self._reading_elapsed = 0.0
        self._reading_samples = []

    def _pulse_frame(self, real_bpm: float, white: bool):
        beat_period = 60.0 / real_bpm
        prev_phase = self.phase
        self.phase = (self.phase + self.TICK_SECONDS / beat_period) % 1.0
        if self.phase < prev_phase:  # wrapped past 1.0 -> a new beat just started, colour steps here (see below)
            self.beat_count += 1

        # Raised cosine: 0.0 at phase 0/1.0 (continuous across the wrap, no
        # jump), 1.0 at phase 0.5 - a pure function of phase, no per-tick
        # lag/smoothing state to carry anything across the colour change.
        breath = 0.5 - 0.5 * np.cos(2 * np.pi * self.phase)
        level = self.idle_level + (1.0 - self.idle_level) * breath

        if white:
            colour = np.array([255.0, 255.0, 255.0])
        else:
            step = self.beat_count % self.COLOUR_STEPS
            idx = int(step / self.COLOUR_STEPS * (len(self.lut) - 1))
            colour = self.lut[idx].astype(float)

        return np.tile(colour * level, (self.n, 1))

    def step(self, intensity: float = 0.0, bpm: float = 0.5):
        engaged = intensity > 0.5
        rising_edge = engaged and not self._was_engaged
        self._was_engaged = engaged

        if rising_edge and not self._reading_active:
            self._reading_active = True
            self._reading_elapsed = 0.0
            self._reading_samples = []
        elif not engaged and self._reading_active:
            self._reading_active = False  # abandoned - contact lost mid-reading, nothing recorded

        if self._reading_active and engaged:
            real_bpm = self.BPM_RANGE[0] + min(max(bpm, 0.0), 1.0) * (self.BPM_RANGE[1] - self.BPM_RANGE[0])
            self._reading_samples.append(real_bpm)
            frame = self._pulse_frame(real_bpm, white=True)
            self._reading_elapsed += self.TICK_SECONDS
            if self._reading_elapsed >= self.READING_SECONDS:
                self.history.append(sum(self._reading_samples) / len(self._reading_samples))
                self._reading_active = False  # done - lift and reapply for another reading
        else:
            avg_bpm = sum(self.history) / len(self.history) if self.history else self.IDLE_FALLBACK_BPM
            real_bpm = min(max(avg_bpm, self.BPM_RANGE[0]), self.BPM_RANGE[1])
            frame = self._pulse_frame(real_bpm, white=False)

        return np.clip(frame, 0, 255).astype(np.uint8)


class TempHumidityBarEffect:
    """Weather zone's DMX bar. Rebuilt 2026-08-18 from scratch (previous
    version stacked four independent signals - condition texture, activity
    shimmer, a contrast-driven marker, plus a whole second replay mode - into
    one effect and read as muddled even though each piece alone was simple;
    replay is dropped entirely for now, see CLAUDE.md's "keep it simple for
    first installation" note). Three layers, each with exactly one driver,
    always composited in this order:

    1. AMBIENT BASE (always on) - a slow, organic mood light, not a data
       readout. `condition` (weather.py's _CONDITION_ORDER index, rescaled
       0..1 - see CONDITION_ORDER below, which must stay in the same order)
       picks both a fixed colour (CONDITION_COLOURS - "what's the weather"
       reads at a glance) and a *character* (CONDITION_CHARACTER - how fast/
       turbulent the noise driving its breathing brightness is): clear/
       cloudy/fog sit calm and slow, rain/storm breathe faster and harder,
       snow sits soft and slow but sparklier than fog. This is one noise
       field, not colour-plus-a-separate-texture-layer.
    2. CONTRAST GLIMMER (continuous) - `contrast` (how far indoor
       temperature has drifted from outdoor, main.py's
       indoor_outdoor_temp_diff) fades in a second, sparser, faster
       twinkle noise field on top as a brightness-only boost (never
       darkens). Indoor ~= outdoor: bar stays calm. Diverged: visibly
       glittery. This does NOT try to also be the touch reaction (see
       layer 3) - on a hot day outdoor temperature can already sit near
       body temperature, so a warm hand wouldn't reliably move contrast
       much even though a touch has genuinely happened.
    3. TOUCH REACT (event-triggered) - a dedicated rising-edge detector on
       `temperature` itself: a hand resting on the sensor pushes its
       reading up over a few seconds regardless of what contrast happens
       to be that day, so this triggers on the *rate of rise*
       (TOUCH_TRIGGER_DELTA over TOUCH_WINDOW_SECONDS), not on an absolute
       level. Firing boosts the *same* sparkle mechanic layer 2 uses (see
       _sparkle) but faster and much stronger (TOUCH_BURST_MAX_BOOST vs
       SHIMMER_MAX_BOOST) - a burst of energetic glimmer decaying back over
       PULSE_DECAY_SECONDS, not a flat colour wash (an earlier version
       blended the whole bar toward a solid warm colour, but combined with
       this fixture's white channel - see main.py's _fixture_channel_values,
       a crude `min(r,g,b)` heuristic - anything close to desaturated white
       read as just "glows white", not a distinct touch reaction; per
       Julia's own framing this should read as glimmer, not a wash, so it's
       the same kind of effect as the contrast shimmer, just an unmistakably
       bigger/faster burst of it). Re-armed only once the reading drops back
       down (COOLDOWN_RESET_FRACTION) so a hand left resting there doesn't
       refire every tick.

    TICK_SECONDS assumes the same fixed 20Hz cadence every effect in this
    file is driven at (main.py's output_loop) - effects don't receive an
    actual dt, so the touch-window buffer and pulse decay both advance by
    this fixed constant per step() call rather than by measured elapsed
    time."""

    TICK_SECONDS = 0.05
    CONDITION_ORDER = ("clear", "cloudy", "fog", "rain", "snow", "storm")  # must match weather.py's _CONDITION_ORDER
    # Fixed, distinct colour per condition, same order as CONDITION_ORDER -
    # "what's the weather" reads as an immediate colour identity at a
    # glance. Untuned placeholders, same as every other colour choice in
    # this file - adjust if any pair reads as too similar in person.
    CONDITION_COLOURS = {
        "clear": (255, 200, 80),
        "cloudy": (140, 150, 160),
        "fog": (200, 200, 200),
        "rain": (70, 130, 220),
        "snow": (215, 235, 245),
        "storm": (95, 60, 140),
    }
    # Per-condition (noise_speed, noise_amplitude, base_brightness) driving
    # the ambient base's breathing - this is what gives each condition a
    # distinct *feel*, not just a distinct colour. Untuned, retune on real
    # hardware.
    CONDITION_CHARACTER = {
        "clear": (0.15, 0.15, 0.9),
        "cloudy": (0.10, 0.10, 0.55),
        "fog": (0.08, 0.08, 0.45),
        "rain": (0.6, 0.35, 0.7),
        "snow": (0.25, 0.30, 0.85),
        "storm": (1.0, 0.5, 0.75),
    }
    SHIMMER_MAX_BOOST = 0.6      # brightness boost added at contrast=1.0 (0 = no glimmer at all)
    TOUCH_WINDOW_SECONDS = 6.0   # how far back the touch-delta looks
    TOUCH_TRIGGER_DELTA = 0.12   # rise (in the 0..1 rescaled temperature units) over that window that counts as "someone touched the sensor"
    COOLDOWN_RESET_FRACTION = 0.5  # re-arms once the rise drops back below TOUCH_TRIGGER_DELTA * this - stops one long touch refiring every tick
    PULSE_DECAY_SECONDS = 4.0    # roughly how long the touch burst takes to fade back into the ambient base
    TOUCH_BURST_MAX_BOOST = 2.2  # brightness boost added at pulse_level=1.0 - well above SHIMMER_MAX_BOOST so a touch unmistakably outshines everyday contrast glimmer

    def __init__(self, n_pixels, palette, background_level=0.05):
        self.n = n_pixels
        self.t = 0.0
        self._touch_window = collections.deque(maxlen=max(1, int(self.TOUCH_WINDOW_SECONDS / self.TICK_SECONDS)))
        self._touch_armed = True   # False right after firing, until the reading drops back down
        self._pulse_level = 0.0

    def _condition_index(self, condition: float) -> int:
        idx = int(round(min(max(condition, 0.0), 1.0) * (len(self.CONDITION_ORDER) - 1)))
        return min(max(idx, 0), len(self.CONDITION_ORDER) - 1)

    def _ambient_base(self, condition_idx: int) -> np.ndarray:
        x = np.arange(self.n)
        name = self.CONDITION_ORDER[condition_idx]
        speed, amplitude, base_brightness = self.CONDITION_CHARACTER[name]
        noise = value_noise(x * 0.2, np.full(self.n, self.t * speed), seed=2)  # -1..1
        brightness = np.clip(base_brightness + amplitude * noise, 0.0, 1.2)
        colour = np.array(self.CONDITION_COLOURS[name], dtype=float)
        return colour[None, :] * brightness[:, None]

    def _sparkle(self, seed: int, x_scale: float, t_scale: float, power: float = 2.0) -> np.ndarray:
        """Shared sparkle-noise primitive behind both the contrast shimmer
        and the touch burst below - >= 0 always, and raising `power` makes
        it sparser/glitterier (only near-peak spots of the underlying noise
        stay visible) rather than a smooth wave. Distinct seed/x_scale/
        t_scale per caller so the two read as different textures, not just
        different amounts of the same one."""
        x = np.arange(self.n)
        noise = value_noise(x * x_scale, np.full(self.n, self.t * t_scale), seed=seed)
        return np.clip(noise, 0.0, None) ** power

    def _shimmer_boost(self, contrast: float) -> np.ndarray:
        """Brightness-only multiplier, >= 1.0 always - glimmer only ever
        adds sparkle on top of the ambient base, never darkens it."""
        sparkle = self._sparkle(seed=11, x_scale=1.4, t_scale=2.2)
        return 1.0 + sparkle * contrast * self.SHIMMER_MAX_BOOST

    def _touch_burst_boost(self, pulse_level: float) -> np.ndarray:
        """Same shape as _shimmer_boost but a faster, sparser, much stronger
        sparkle - see class docstring on why a touch is a bigger/quicker
        burst of the same texture rather than a solid colour wash."""
        sparkle = self._sparkle(seed=13, x_scale=2.2, t_scale=6.0, power=3.0)
        return 1.0 + sparkle * pulse_level * self.TOUCH_BURST_MAX_BOOST

    def _update_touch_pulse(self, temperature: float) -> float:
        """Rising-edge detector on raw temperature - see class docstring on
        why this is a separate signal from `contrast` rather than reusing
        it. Returns the current pulse brightness (0..1, 1.0 right after a
        fresh trigger, decaying back to 0)."""
        self._touch_window.append(temperature)
        delta = temperature - self._touch_window[0] if len(self._touch_window) == self._touch_window.maxlen else 0.0

        if self._touch_armed and delta >= self.TOUCH_TRIGGER_DELTA:
            self._pulse_level = 1.0
            self._touch_armed = False
        elif not self._touch_armed and delta <= self.TOUCH_TRIGGER_DELTA * self.COOLDOWN_RESET_FRACTION:
            self._touch_armed = True  # reading's dropped back down - a later rise counts as a new touch

        decay_per_tick = self.TICK_SECONDS / self.PULSE_DECAY_SECONDS
        self._pulse_level = max(0.0, self._pulse_level - decay_per_tick)
        return self._pulse_level

    def step(self, temperature: float = 0.5, contrast: float = 0.0, condition: float = 0.0):
        contrast = min(max(contrast, 0.0), 1.0)
        self.t += 0.02  # slow, steady drift - condition's own `speed` (CONDITION_CHARACTER) scales how turbulent it looks on top of this

        frame = self._ambient_base(self._condition_index(condition))
        frame = frame * self._shimmer_boost(contrast)[:, None]

        pulse_level = self._update_touch_pulse(temperature)
        if pulse_level > 0.0:
            frame = frame * self._touch_burst_boost(pulse_level)[:, None]

        return np.clip(frame, 0, 255).astype(np.uint8)


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
