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

# Every speed/decay/rate constant in this file was originally hand-tuned by
# eye assuming main.py's output_loop calls step() at a steady 20Hz (one call
# = ASSUMED_TICK_SECONDS real seconds). That assumption turned out to be
# false on real hardware (found 2026-08-18: the loop was running at ~2-3Hz,
# not 20Hz, from blocking sensor/DMX I/O - see main.py's sensor_loop/
# output_loop) - every timed effect ran proportionally slower than intended
# with no error, just animations that "felt" wrong (a 70bpm heart pulse
# stretched to several seconds). Rather than chase "make the loop exactly
# 20Hz" as the only fix, every effect below now takes the *real* elapsed
# time (`dt`, measured by output_loop via time.monotonic() and passed into
# step()) and uses it directly, so animation speed stays correct in real
# seconds regardless of the actual loop rate. ASSUMED_TICK_SECONDS is kept
# as the reference baseline every hand-tuned constant was calibrated
# against - at dt == ASSUMED_TICK_SECONDS, every formula below reduces to
# exactly what it was before this change, so nothing needed re-tuning, just
# correctly generalising.
ASSUMED_TICK_SECONDS = 0.05


def _rate_scale(dt: float) -> float:
    """How much faster/slower than the ASSUMED_TICK_SECONDS baseline this
    tick's real dt is - multiply a per-assumed-tick ADDITIVE increment
    (self.t +=, a beam's pos +=, ...) by this to correctly generalise it to
    real elapsed time."""
    return dt / ASSUMED_TICK_SECONDS


def _decay_scale(per_tick_factor: float, dt: float) -> float:
    """Generalises a per-assumed-tick MULTIPLICATIVE decay/fade constant
    (self.trail *= 0.9, ...) to real elapsed time. Decay compounds, so this
    needs the exponent, not the same linear ratio _rate_scale uses -
    per_tick_factor applied N times in a row is per_tick_factor**N, and a
    dt that's e.g. 2x the assumed tick should apply two "ticks" worth of
    decay in one call."""
    return per_tick_factor ** _rate_scale(dt)


def _ema_rate(per_tick_rate: float, dt: float) -> float:
    """Generalises a per-assumed-tick exponential-moving-average rate (used
    as `level += (target - level) * rate`) to real elapsed time - same
    compounding reasoning as _decay_scale (one minus the rate is what
    actually decays each step)."""
    return 1.0 - _decay_scale(1.0 - per_tick_rate, dt)


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

    def step(self, intensity: float = 1.0, dt: float = ASSUMED_TICK_SECONDS):
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
        point on that scale. `dt` is the real elapsed seconds since the
        last step() call (see ASSUMED_TICK_SECONDS's module docstring) -
        self.t's advance is scaled by it so drift speed stays correct in
        real time regardless of the actual loop rate."""
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
        self.t += self.speed * speed_scale * _rate_scale(dt)
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

    def step(self, loudness: float = 0.0, motion: float = 0.0, env_brightness: float = 0.5, ripple: float = 0.0,
              dt: float = ASSUMED_TICK_SECONDS):
        motion = min(max(motion, 0.0), 1.0)
        speed_scale = scaled(motion, *self.SPEED_SCALE_RANGE)
        brightness_scale = scaled(env_brightness, 0.5, 1.0)  # never fully dark - this is ambient decor, not a screen

        # Self-calibrating ceiling: expands instantly on a new peak (so one
        # loud moment immediately unlocks the top of the palette), decays
        # slowly back down otherwise (so an earlier loud spell doesn't
        # permanently compress everything afterwards into a narrow low
        # band) - never below ceiling_floor, so there's always at least a
        # usable range even in a totally silent room. ceiling_decay_per_second
        # is already a real per-second rate, so it just needs the real `dt`
        # in place of the old fixed-TICK_SECONDS assumption - no ratio/
        # exponent scaling needed here, unlike self.t's advance below.
        if loudness > self._ceiling:
            self._ceiling = loudness
        else:
            self._ceiling = max(
                self._ceiling_floor,
                self._ceiling - self._ceiling * self._ceiling_decay_per_second * dt,
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

        self.t += self.speed * speed_scale * _rate_scale(dt)
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

    def step(self, intensity: float = 1.0, dt: float = ASSUMED_TICK_SECONDS):
        """intensity is the current activity_level (0..1) - quiet rooms get
        a slower, dimmer comet; lively rooms get a faster, brighter one."""
        speed_scale = scaled(intensity, 0.3, 1.2)
        brightness_scale = scaled(intensity, 0.4, 1.2)

        wobble = 0.35 * value_noise(np.array([self.t]), np.array([0.0]), seed=self.seed)[0]
        self.pos = (self.pos + self.speed * speed_scale * (1 + wobble) * _rate_scale(dt)) % self.n
        self.trail *= _decay_scale(self.decay, dt)
        idx0 = int(np.floor(self.pos)) % self.n
        idx1 = (idx0 + 1) % self.n
        frac = self.pos - np.floor(self.pos)
        self.trail[idx0] = max(self.trail[idx0], 1 - frac)
        self.trail[idx1] = max(self.trail[idx1], frac)
        palette_index = int((self.pos / self.n) * (len(self.lut) - 1))
        comet_color = self.lut[palette_index]
        frame = self.background[None, :] + (comet_color - self.background)[None, :] * self.trail[:, None]
        frame = frame * brightness_scale
        self.t += 0.4 * _rate_scale(dt)
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

    def step(self, intensity: float = 1.0, dt: float = ASSUMED_TICK_SECONDS):
        """intensity is the current activity_level (0..1) - quiet rooms get
        fewer, dimmer stars; lively rooms get more, brighter ones."""
        spawn_scale = scaled(intensity, 0.3, 1.7)
        brightness_scale = scaled(intensity, 0.4, 1.2)

        self.brightness *= _decay_scale(self.decay, dt)
        # spawn_prob is a per-assumed-tick probability - scaling it linearly
        # by _rate_scale keeps the real-time spawn RATE correct (a fair
        # approximation for a low per-interval probability like this one,
        # same reasoning a Poisson-process rate scales linearly with the
        # window length).
        spawn = np.random.random(self.n) < self.spawn_prob * spawn_scale * _rate_scale(dt)
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

    def step(self, intensity: float = 0.0, dt: float = ASSUMED_TICK_SECONDS):
        intensity = min(max(intensity, 0.0), 1.0)
        target = self.idle_level + (1.0 - self.idle_level) * intensity
        rate = self.attack if target > self.level else self.decay
        self.level += (target - self.level) * _ema_rate(rate, dt)

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
    magnitude, same signal DirectionalWaveEffect used. See
    accel_stick.ino's readAcceleration() for what angle_deg actually is:
    atan2(ay, ax) of the board's own local X/Y axes ONLY - Z (roughly
    gravity-aligned when the stick is held upright) is deliberately
    excluded, on the assumption the stick is held pointing up/down and
    swung side-to-side/forward-back, not held flat. A horizontal swing
    while the board itself isn't held upright doesn't show up correctly in
    X/Y - if a swing's landing on the wrong arm consistently (not just
    noisy/random), that's the first thing to check, not this effect's own
    angle-to-arm mapping.

    REDESIGNED 2026-08-17 from a continuous per-tick reactive glide into a
    swing-detect-then-fire-a-beam model with hysteresis + a buffered,
    averaged direction - real handheld shakes are noisy tick to tick, so
    reacting to every instant made the response feel twitchy/glitchy.
    REDESIGNED AGAIN 2026-08-18, same day it was first confirmed on real
    hardware: that version only fired once a swing fully *ended* (intensity
    dropping back below a second, lower threshold) - reported back as "it
    slowly started having lights after I stopped", i.e. the reaction was
    delayed until the motion completely settled, and the beam speed itself
    read as "a snail climbing the arm" rather than the "quick little thing"
    that gives an instantaneous-feeling interaction. Per that feedback, the
    whole buffer-and-wait-for-settle mechanism is gone:

    INSTANT TRIGGER: a single threshold (SWING_TRIGGER_THRESHOLD) - the
    moment `intensity` crosses it, a beam fires immediately using THAT
    tick's own (intensity, angle), no waiting, no averaging a window of
    samples. SWING_COOLDOWN_SECONDS is the only thing standing between one
    trigger and the next - short enough (0.12s) that genuinely separate
    quick swings each register on their own, but long enough that one
    continuous held-above-threshold reading doesn't refire and restart the
    same beam every single 0.05s tick, which would just read as the arm
    solidly lit rather than a distinct travelling beam. This is also what
    gives "shake violently in many directions" its firework quality for
    free, with no separate "violent" detection needed: each quick
    direction-change that clears the cooldown is its own independent
    trigger, on whichever arm(s) that instant's angle aligns with - a
    single controlled swing reads as one shooting star, a wild multi-
    directional shake reads as several, potentially on different arms,
    firing in quick succession.

    BEAM RENDERING: the triggering (intensity, angle) spawns one travelling
    beam per arm whose cosine-weighted alignment to that angle clears
    MIN_ARM_WEIGHT (see the dead-zone/overlap caveat below, still just as
    true here). Each arm's beam travels hub -> tip at a speed set by
    (intensity * that arm's own alignment weight) * BEAM_SPEED_SCALE - the
    scale itself raised sharply (1.6 -> 7.0) so even a moderate swing
    crosses the longest arm (36px) in well under half a second, not
    several seconds; BEAM_DECAY also raised to match (a fast head with a
    slow-fading trail would just look like the whole arm lighting up solid
    rather than a distinct streak). Brightness and pixel-width scale the
    same way a harder, more on-target swing has always produced a beam
    that's faster, brighter, AND wider, not just brighter. Reaching the tip
    ends that arm's beam. A new trigger on an arm already mid-beam just
    replaces it outright, no blending between the two - matches "quick,
    immediate feedback" better than queuing would.

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

    # Swing detection - untuned placeholders (feel, not physics, same
    # caveat as accel_stick.ino's SENSITIVITY), but SWING_TRIGGER_THRESHOLD
    # deliberately matches accel_stick.ino's own SWING_INVITE_HIDE_ABOVE
    # (0.15) - the firmware's own on-screen UI already treats that as "a
    # real swing, not just jitter", so there's no reason for this effect to
    # disagree about what counts as one.
    SWING_TRIGGER_THRESHOLD = 0.15  # intensity crossing this fires a beam immediately - see class docstring's INSTANT TRIGGER section
    SWING_COOLDOWN_SECONDS = 0.12   # minimum gap between two triggers - short enough that quick successive swings each register
    MIN_ARM_WEIGHT = 0.15           # below this cosine-alignment to the swing direction, an arm doesn't get a beam at all

    # Beam rendering - also untuned placeholders, but deliberately much
    # faster than the first real-hardware version (BEAM_SPEED_SCALE 1.6,
    # BEAM_DECAY 0.88) per explicit "too slow, like a snail" feedback -
    # 7.0/0.7 crosses even the longest arm (36px) in well under half a
    # second at full strength and leaves a short, fast-fading streak
    # instead of a slow, long-lingering one.
    BEAM_SPEED_SCALE = 7.0         # pixels/tick at (intensity * arm weight) = 1.0
    BEAM_WIDTH_MIN_PX = 2          # pixels lit around the beam's leading edge at driven strength -> 0
    BEAM_WIDTH_MAX_PX = 8          # ... at driven strength -> 1 - "more pixels pack" for a harder/more-aligned swing
    BEAM_DECAY = 0.7               # per-tick trail fade behind a travelling beam - raised (lowered?) to match the faster beam, see above

    def __init__(self, n_pixels, palette, background_level=0.05):
        self.lut = _palette_lut(palette)
        self.background = self.lut[0] * background_level
        self.trail = np.zeros(n_pixels)

        lengths = list(self.ARM_LENGTHS)
        lengths[-1] += n_pixels - sum(lengths)  # last arm absorbs any mismatch against the zone's real pixel count
        starts = np.cumsum([0] + lengths[:-1]).tolist()
        self.arm_ranges = list(zip(starts, lengths))  # (start, length) per arm, in the shared trail array
        self.arm_center_rad = [np.radians(deg) for deg in self.ARM_ANGLES_DEG]

        self._cooldown_remaining = 0.0
        self._beams = {}  # arm_index -> {"pos", "speed", "width", "brightness"} for each currently-travelling beam

    def _to_index(self, arm_idx: int, dist: int) -> int:
        """Hub-relative distance along one arm -> real index in the shared trail array."""
        start, length = self.arm_ranges[arm_idx]
        return start + (length - 1 - dist) if self.ARM_REVERSED[arm_idx] else start + dist

    def _fire(self, intensity: float, angle_rad: float):
        """Spawn a beam on every arm whose cosine-weighted alignment to
        this instant's angle clears MIN_ARM_WEIGHT - see class docstring's
        INSTANT TRIGGER / BEAM RENDERING sections."""
        for k in range(self.ARM_COUNT):
            _, length = self.arm_ranges[k]
            if length == 0:
                continue
            weight = max(0.0, np.cos(angle_rad - self.arm_center_rad[k]))
            if weight < self.MIN_ARM_WEIGHT:
                continue
            driven = intensity * weight
            self._beams[k] = {
                "pos": 0.0,
                "speed": driven,
                "width": self.BEAM_WIDTH_MIN_PX + (self.BEAM_WIDTH_MAX_PX - self.BEAM_WIDTH_MIN_PX) * driven,
                "brightness": scaled(driven, 0.3, 1.0),
            }

    def step(self, intensity: float = 0.0, angle: float = 0.0, dt: float = ASSUMED_TICK_SECONDS):
        intensity = min(max(intensity, 0.0), 1.0)
        angle = min(max(angle, 0.0), 1.0)
        angle_rad = angle * 2 * np.pi

        # Cooldown is a real countdown in seconds - dt substitutes directly
        # for the old fixed-TICK_SECONDS assumption, no ratio/exponent
        # needed (unlike BEAM_SPEED_SCALE/BEAM_DECAY below, which were
        # tuned as per-assumed-tick rates).
        self._cooldown_remaining = max(0.0, self._cooldown_remaining - dt)
        if intensity > self.SWING_TRIGGER_THRESHOLD and self._cooldown_remaining <= 0.0:
            self._fire(intensity, angle_rad)
            self._cooldown_remaining = self.SWING_COOLDOWN_SECONDS

        self.trail *= _decay_scale(self.BEAM_DECAY, dt)
        for k in list(self._beams.keys()):
            _, length = self.arm_ranges[k]
            beam = self._beams[k]
            beam["pos"] += beam["speed"] * self.BEAM_SPEED_SCALE * _rate_scale(dt)
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

    `dt` is the real elapsed seconds since the last step() call (see
    ASSUMED_TICK_SECONDS's module docstring) - phase advances by
    dt/beat_period directly, a genuine "how many seconds passed / how many
    seconds per beat" calculation, so bpm stays correct in real time
    regardless of the actual loop rate. (Had grown a `* 0.1` fudge factor
    on beat_period at one point, compensating for the loop running ~10x
    slower than assumed instead of fixing that directly - removed now that
    dt makes the underlying assumption correct instead of worked around.)"""

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

    def step(self, intensity: float = 0.0, bpm: float = 0.5, dt: float = ASSUMED_TICK_SECONDS):
        engaged = intensity > 0.5
        if not engaged:
            self.phase = 0.0  # next contact starts on a fresh beat, not mid-cycle
            self.level += (self.idle_level - self.level) * _ema_rate(self.ease, dt)
            frame = np.tile(self._color_at_level(self.level), (self.n, 1))
            return np.clip(frame, 0, 255).astype(np.uint8)

        real_bpm = self.BPM_RANGE[0] + min(max(bpm, 0.0), 1.0) * (self.BPM_RANGE[1] - self.BPM_RANGE[0])
        beat_period = 60.0 / real_bpm
        self.phase = (self.phase + dt / beat_period) % 1.0
        pulse = np.exp(-self.phase * self.decay_rate)  # bright flash at phase 0, fading before the next beat
        target = self.idle_level + (1.0 - self.idle_level) * pulse
        self.level += (target - self.level) * _ema_rate(self.ease, dt)  # ease avoids a hard jump/flicker frame-to-frame
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
    BPM_RANGE = (40.0, 180.0)  # must match config.yaml's heart_rate zone bpm {min, max}
    IDLE_FALLBACK_BPM = 70.0  # shown at rest before any reading's ever completed
    COLOUR_STEPS = 20  # beats to fully cycle the idle pulse's colour once around the palette, per Julia's "10 ticks" request

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

    def _pulse_frame(self, real_bpm: float, white: bool, dt: float):
        # dt/beat_period is a genuine "real seconds elapsed / real seconds
        # per beat" calculation, so bpm stays correct regardless of the
        # actual loop rate - see ASSUMED_TICK_SECONDS's module docstring.
        # (This briefly grew a `* 0.1` fudge on beat_period, compensating
        # for the loop running ~10x slower than assumed instead of fixing
        # that directly - removed now that dt makes the underlying
        # assumption correct instead of worked around.)
        beat_period = 60.0 / real_bpm
        prev_phase = self.phase
        self.phase = (self.phase + dt / beat_period) % 1.0
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

    def step(self, intensity: float = 0.0, bpm: float = 0.5, dt: float = ASSUMED_TICK_SECONDS):
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
            frame = self._pulse_frame(real_bpm, white=True, dt=dt)
            self._reading_elapsed += dt
            if self._reading_elapsed >= self.READING_SECONDS:
                self.history.append(sum(self._reading_samples) / len(self._reading_samples))
                self._reading_active = False  # done - lift and reapply for another reading
        else:
            avg_bpm = sum(self.history) / len(self.history) if self.history else self.IDLE_FALLBACK_BPM
            real_bpm = min(max(avg_bpm, self.BPM_RANGE[0]), self.BPM_RANGE[1])
            frame = self._pulse_frame(real_bpm, white=False, dt=dt)

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

    `dt` (real elapsed seconds since the last step() call - see
    ASSUMED_TICK_SECONDS's module docstring) drives both the touch-window
    buffer and the pulse decay in genuine real time now, rather than
    assuming a fixed 20Hz cadence - see _update_touch_pulse for how the
    window itself changed from a fixed-length sample buffer (N samples,
    which only means "TOUCH_WINDOW_SECONDS ago" if the loop is genuinely
    running at the assumed rate) to tracking real elapsed time directly."""

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
        # (elapsed_at_sample, temperature) pairs, oldest first - real-time
        # replacement for the old fixed-length deque (see class docstring).
        # Unbounded but self-pruning (see _update_touch_pulse), so it never
        # actually grows past roughly TOUCH_WINDOW_SECONDS worth of samples
        # regardless of how fast/slow step() is actually being called.
        self._touch_window = collections.deque()
        self._elapsed_total = 0.0
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

    def _update_touch_pulse(self, temperature: float, dt: float) -> float:
        """Rising-edge detector on raw temperature - see class docstring on
        why this is a separate signal from `contrast` rather than reusing
        it. Returns the current pulse brightness (0..1, 1.0 right after a
        fresh trigger, decaying back to 0). Window is real elapsed time now
        (see __init__), not a fixed sample count - compare against the
        oldest sample still within TOUCH_WINDOW_SECONDS of now, pruning
        anything older every call."""
        self._elapsed_total += dt
        self._touch_window.append((self._elapsed_total, temperature))
        while self._touch_window and self._elapsed_total - self._touch_window[0][0] > self.TOUCH_WINDOW_SECONDS:
            self._touch_window.popleft()

        # Only trust the delta once we've actually accumulated close to a
        # full window - otherwise the "oldest sample" early on is really
        # just "a moment ago", not genuinely TOUCH_WINDOW_SECONDS back, and
        # would read as a huge/spurious rise.
        if self._elapsed_total >= self.TOUCH_WINDOW_SECONDS and self._touch_window:
            delta = temperature - self._touch_window[0][1]
        else:
            delta = 0.0

        if self._touch_armed and delta >= self.TOUCH_TRIGGER_DELTA:
            self._pulse_level = 1.0
            self._touch_armed = False
        elif not self._touch_armed and delta <= self.TOUCH_TRIGGER_DELTA * self.COOLDOWN_RESET_FRACTION:
            self._touch_armed = True  # reading's dropped back down - a later rise counts as a new touch

        self._pulse_level = max(0.0, self._pulse_level - dt / self.PULSE_DECAY_SECONDS)
        return self._pulse_level

    def step(self, temperature: float = 0.5, contrast: float = 0.0, condition: float = 0.0,
              dt: float = ASSUMED_TICK_SECONDS):
        contrast = min(max(contrast, 0.0), 1.0)
        self.t += 0.02 * _rate_scale(dt)  # slow, steady drift - condition's own `speed` (CONDITION_CHARACTER) scales how turbulent it looks on top of this

        frame = self._ambient_base(self._condition_index(condition))
        frame = frame * self._shimmer_boost(contrast)[:, None]

        pulse_level = self._update_touch_pulse(temperature, dt)
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
