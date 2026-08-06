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
        speed_scale = scaled(motion, 0.8, 2.0)
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
    """For the accelerometer zone once it's three LED arms radiating from a
    shared hub at 120 degrees apart, spliced into the same continuous APA102
    chain as the heart_rate zone (not a standalone DMX fixture like the
    single-bar DirectionalWaveEffect this replaces) - see accel_stick.ino's
    atan2-based `angle_deg` and config.py's accelerometer zone.

    `angle` arrives pre-rescaled to 0..1 by main.py's _resolve_one_source
    (the zone's source config supplies {min: 0, max: 360} - see config.py)
    and is converted back to radians here. `intensity` is the shake's
    magnitude, same signal DirectionalWaveEffect used.

    Each arm claims a smooth ~180-degree-wide slice of the circle centred
    on its own spoke, via a clamped cosine falloff: weight = max(0,
    cos(angle - arm_centre)). With arms exactly 120 degrees apart this
    self-limits to at most two arms active at once, both getting equal
    weight exactly on the boundary between them - a swing angled between
    two arms lights both, proportionally, rather than snapping hard at the
    60-degree midpoint.

    "Glide out": while a swing keeps an arm's weight * intensity above
    IDLE_THRESHOLD, that arm's head advances from the hub (distance 0)
    toward the tip at a speed set by intensity - a harder shake reaches
    further, faster. Once the swing moves on (this arm's drive drops back
    below threshold), the head holds its position and fades with the rest
    of the trail rather than snapping back, then resets to the hub so the
    next swing toward this arm starts from the centre again, not wherever
    the last one left off.

    n_pixels doesn't split evenly across ARM_COUNT in general - the last
    arm absorbs the remainder, same "pad the odd one out" idea output_loop
    already uses when a zone's total doesn't divide cleanly.

    ARM_ANGLES_DEG and ARM_REVERSED below are placeholders - the physical
    shape isn't cut/soldered yet (it'll be one continuous strip bent/spliced
    into three arms, not three separate fixtures). Once it is:
    - ARM_ANGLES_DEG: swing the stick toward each arm in turn and watch
      `sensors.angle_deg` on the admin dashboard; set each arm's entry to
      the angle that lit it, rather than assuming 0/120/240 in stick-frame
      degrees actually lines up with the arms as mounted.
    - ARM_REVERSED: assumes every arm is wired hub-to-tip in increasing
      pixel order within its slice of the strip. Flip an entry to True if
      that arm's data line actually runs tip-to-hub once soldered (likely
      for at least one arm, since a single continuous chain snaking hub -
      tip - hub - tip - hub - tip alternates direction every other arm)."""

    ARM_COUNT = 3
    ARM_ANGLES_DEG = (0.0, 120.0, 240.0)   # placeholder - recalibrate on-site, see docstring
    ARM_REVERSED = (False, False, False)   # placeholder - recalibrate on-site, see docstring
    IDLE_THRESHOLD = 0.08                  # below this arm-weight*intensity, that arm isn't "being swung toward"
    GLIDE_SPEED = 0.6                      # pixels/tick at intensity=1.0 - tune once the arm length is real

    def __init__(self, n_pixels, palette, decay=0.9, background_level=0.05):
        self.lut = _palette_lut(palette)
        self.decay = decay
        self.background = self.lut[0] * background_level
        self.trail = np.zeros(n_pixels)

        base = n_pixels // self.ARM_COUNT
        lengths = [base] * self.ARM_COUNT
        lengths[-1] += n_pixels - base * self.ARM_COUNT  # last arm absorbs the remainder
        starts = np.cumsum([0] + lengths[:-1]).tolist()
        self.arm_ranges = list(zip(starts, lengths))  # (start, length) per arm, in the shared trail array
        self.arm_center_rad = [np.radians(deg) for deg in self.ARM_ANGLES_DEG]
        self.head_pos = [0.0] * self.ARM_COUNT  # hub-relative sub-pixel distance per arm

    def step(self, intensity: float = 0.0, angle: float = 0.0):
        intensity = min(max(intensity, 0.0), 1.0)
        angle = min(max(angle, 0.0), 1.0)
        angle_rad = angle * 2 * np.pi

        self.trail *= self.decay

        for k in range(self.ARM_COUNT):
            start, length = self.arm_ranges[k]
            if length == 0:
                continue

            weight = max(0.0, np.cos(angle_rad - self.arm_center_rad[k]))
            drive = weight * intensity

            if drive > self.IDLE_THRESHOLD:
                self.head_pos[k] = min(length - 1, self.head_pos[k] + self.GLIDE_SPEED * intensity)
                dist0 = int(np.floor(self.head_pos[k]))
                dist1 = min(length - 1, dist0 + 1)
                frac = self.head_pos[k] - dist0

                # hub-relative distance -> real index in the shared trail array
                to_index = (lambda d: start + (length - 1 - d)) if self.ARM_REVERSED[k] else (lambda d: start + d)
                head_strength = scaled(drive, 0.2, 1.0)
                self.trail[to_index(dist0)] = max(self.trail[to_index(dist0)], (1 - frac) * head_strength)
                self.trail[to_index(dist1)] = max(self.trail[to_index(dist1)], frac * head_strength)
            else:
                self.head_pos[k] = 0.0  # next swing toward this arm starts from the hub again

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

    def __init__(self, n_pixels, palette, idle_level=0.15, ease=0.15, decay_rate=6.0):
        self.n = n_pixels
        self.lut = _palette_lut(palette)
        self.idle_level = idle_level
        self.ease = ease
        self.decay_rate = decay_rate
        self.level = idle_level
        self.phase = 0.0
        self.history = collections.deque(maxlen=self.HISTORY_SIZE)
        self._was_engaged = False
        self._chime_elapsed = 0.0
        self._reading_elapsed = 0.0
        self._reading_samples = []
        self._state = "idle"

    def _color_at_level(self, level: float, white: bool = False):
        if white:
            return np.array([255.0, 255.0, 255.0])
        index = int(min(max(level, 0.0), 1.0) * (len(self.lut) - 1))
        return self.lut[index].astype(float)

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
        # cue, not just another heartbeat.
        quarter = self.CHIME_SECONDS / 4
        flash_on = (self._chime_elapsed % (2 * quarter)) < quarter
        level = 1.0 if flash_on else 0.0
        return np.tile(np.array([255.0, 255.0, 255.0]) * level, (self.n, 1))

    def step(self, intensity: float = 0.0, bpm: float = 0.5):
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


class TempHumidityMatrixEffect:
    """`temperature` picks a position along the palette gradient (cool end
    <-> warm end), `humidity` scales overall brightness - still one
    combined base colour across the panel, not a spatial pattern driven by
    the matrix's actual rows/cols (that's future work once there's a
    physical panel to tune against). On top of that base colour:

    - `activity` (state.activity_level - same signal the ambient zone
      reacts to, so this zone's liveliness stays in sync with it rather
      than reading as a separate, disconnected panel) drives a subtle
      per-pixel shimmer: a calm room leaves the panel essentially flat and
      static like before, a lively one gets a slow, visible noise-driven
      variation across the pixels.
    - `contrast` (how far indoor temperature has drifted from outdoor -
      see main.py's indoor_outdoor_temp_diff) pushes the displayed colour
      further toward whichever end of the palette it's already closest to
      - "the room vs the world" reads as a more saturated/extreme colour
      the more indoor conditions diverge from outside, rather than
      needing a separate signed "which direction" input (the resolve
      pipeline only ever gives unsigned 0..1 - see main.py's
      _resolve_one_source). At contrast=0 (or no weather data at all) this
      is a no-op - identical to the colour before this existed."""

    def __init__(self, n_pixels, palette):
        self.n = n_pixels
        self.lut = _palette_lut(palette)
        self.t = 0.0

    def step(self, temperature: float = 0.5, humidity: float = 0.5, activity: float = 0.0, contrast: float = 0.0):
        activity = min(max(activity, 0.0), 1.0)
        contrast = min(max(contrast, 0.0), 1.0)

        t = min(max(temperature, 0.0), 1.0)
        exaggerated_t = min(max(t + (t - 0.5) * contrast, 0.0), 1.0)
        index = int(exaggerated_t * (len(self.lut) - 1))
        color = self.lut[index].astype(float)
        brightness = 0.3 + 0.7 * min(max(humidity, 0.0), 1.0)

        x = np.arange(self.n)
        shimmer = value_noise(x * 0.15, np.full(self.n, self.t))  # roughly -1..1 per pixel
        shimmer_amount = 0.25 * activity  # calm: ~0, panel reads flat; lively: visible variation
        pixel_brightness = brightness * (1.0 + shimmer * shimmer_amount)

        frame = color[None, :] * pixel_brightness[:, None]
        self.t += 0.01 + 0.04 * activity  # calm: near-static; lively: visibly drifting
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
