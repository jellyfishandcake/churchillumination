// effects.js — JS port of src/output/effects/led_effects.py, so a sketch
// can use (or extend) the same generative techniques as the Python system
// effects: coherent-noise flow, a wandering comet with a trail, and
// randomly-igniting twinkling stars.
//
// Uses p5's own p.noise() rather than porting Python's custom hash-based
// value_noise - both give smooth, non-repeating variation, and p.noise()
// is already a well-tested part of any p5 sketch's toolkit, rather than a
// second bespoke noise implementation to maintain in two languages. The
// two won't look bit-identical to their Python counterparts, which is
// fine - nothing here needs numeric parity across languages, just the
// same creative technique.
//
// Each class takes (p, nPixels, palette, options) and has step(intensity)
// returning an array of nPixels [r,g,b] triples - same shape as the
// Python effects, so a sketch could draw them, blend them, or feed them
// straight into whatever it wants.

function applyGamma(frame, gamma = 2.2) {
  return frame.map((rgb) =>
    rgb.map((v) => {
      const normalized = Math.min(Math.max(v, 0), 255) / 255;
      return Math.min(255, Math.max(0, Math.pow(normalized, gamma) * 255));
    })
  );
}

class OrganicWaveEffect {
  constructor(p, nPixels, palette, { seed = 0, speed = 0.006, scale = 0.05, highlight = true } = {}) {
    this.p = p;
    this.n = nPixels;
    this.lut = window.paletteLut(palette);
    this.seed = seed;
    this.speed = speed;
    this.scale = scale;
    this.highlight = highlight;
    this.t = 0;
  }

  step(intensity = 1.0) {
    intensity = Math.min(Math.max(intensity, 0), 1);
    const speedScale = 0.3 + 0.9 * intensity;
    const brightnessScale = 0.4 + 0.8 * intensity;
    const seedOffset = this.seed * 137;

    const frame = [];
    for (let i = 0; i < this.n; i++) {
      const layer1 = this.p.noise(i * this.scale + seedOffset, this.t) * 2 - 1;
      const layer2 = this.p.noise(i * this.scale * 2.3 + 50 + seedOffset, this.t * 1.7) * 2 - 1;
      const combined = 0.65 * layer1 + 0.35 * layer2;
      const tNorm = Math.min(Math.max((combined + 1) / 2, 0), 1);
      const idx = Math.floor(tNorm * (this.lut.length - 1));
      let [r, g, b] = this.lut[idx];
      if (this.highlight) {
        const agreement = Math.max(layer1 * layer2, 0) * 70;
        r += agreement;
        g += agreement;
        b += agreement;
      }
      frame.push([r, g, b].map((v) => Math.min(255, Math.max(0, v * brightnessScale))));
    }
    this.t += this.speed * speedScale;
    return frame;
  }
}

class OrganicCometEffect {
  constructor(p, nPixels, palette, { speed = 0.12, decay = 0.93, backgroundLevel = 0.05, seed = 0 } = {}) {
    this.p = p;
    this.n = nPixels;
    this.lut = window.paletteLut(palette);
    this.speed = speed;
    this.decay = decay;
    this.background = this.lut[0].map((v) => v * backgroundLevel);
    this.trail = new Array(nPixels).fill(0);
    this.pos = 0;
    this.seed = seed;
    this.t = 0;
  }

  step(intensity = 1.0) {
    intensity = Math.min(Math.max(intensity, 0), 1);
    const speedScale = 0.3 + 0.9 * intensity;
    const brightnessScale = 0.4 + 0.8 * intensity;

    const wobble = 0.35 * (this.p.noise(this.t + this.seed * 137) * 2 - 1);
    this.pos = (this.pos + this.speed * speedScale * (1 + wobble) + this.n) % this.n;
    this.trail = this.trail.map((v) => v * this.decay);

    const idx0 = Math.floor(this.pos) % this.n;
    const idx1 = (idx0 + 1) % this.n;
    const frac = this.pos - Math.floor(this.pos);
    this.trail[idx0] = Math.max(this.trail[idx0], 1 - frac);
    this.trail[idx1] = Math.max(this.trail[idx1], frac);

    const paletteIndex = Math.floor((this.pos / this.n) * (this.lut.length - 1));
    const cometColor = this.lut[paletteIndex];

    const frame = [];
    for (let i = 0; i < this.n; i++) {
      const t = this.trail[i];
      frame.push(
        this.background.map((bg, c) => Math.min(255, Math.max(0, (bg + (cometColor[c] - bg) * t) * brightnessScale)))
      );
    }
    this.t += 0.4;
    return frame;
  }
}

class OrganicTwinkleEffect {
  constructor(p, nPixels, palette, { spawnProb = 0.01, decayRange = [0.94, 0.985], peakRange = [0.55, 1.0], backgroundLevel = 0.03 } = {}) {
    this.p = p;
    this.n = nPixels;
    this.lut = window.paletteLut(palette);
    this.spawnProb = spawnProb;
    this.decayRange = decayRange;
    this.peakRange = peakRange;
    this.background = this.lut[0].map((v) => v * backgroundLevel);
    this.brightness = new Array(nPixels).fill(0);
    this.hueIndex = new Array(nPixels).fill(0);
    this.decay = new Array(nPixels).fill(0.96);
    this.peak = new Array(nPixels).fill(1);
  }

  step(intensity = 1.0) {
    intensity = Math.min(Math.max(intensity, 0), 1);
    const spawnScale = 0.3 + 1.4 * intensity;
    const brightnessScale = 0.4 + 0.8 * intensity;

    const frame = [];
    for (let i = 0; i < this.n; i++) {
      this.brightness[i] *= this.decay[i];
      if (Math.random() < this.spawnProb * spawnScale) {
        this.brightness[i] = 1;
        this.hueIndex[i] = Math.floor(Math.random() * this.lut.length);
        this.decay[i] = this.decayRange[0] + Math.random() * (this.decayRange[1] - this.decayRange[0]);
        this.peak[i] = this.peakRange[0] + Math.random() * (this.peakRange[1] - this.peakRange[0]);
      }
      const starColor = this.lut[this.hueIndex[i]];
      const effectiveBrightness = this.brightness[i] * this.peak[i];
      frame.push(
        this.background.map((bg, c) =>
          Math.min(255, Math.max(0, (bg + (starColor[c] - bg) * effectiveBrightness) * brightnessScale))
        )
      );
    }
    return frame;
  }
}

window.applyGamma = applyGamma;
window.OrganicWaveEffect = OrganicWaveEffect;
window.OrganicCometEffect = OrganicCometEffect;
window.OrganicTwinkleEffect = OrganicTwinkleEffect;
