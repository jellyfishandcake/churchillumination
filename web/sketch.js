// sketch.js — the p5 visual, in instance mode.
//
// Instance mode means every p5 function is on a passed-in `p` object
// (p.background, p.noise, p.color) rather than being global. This is
// cleaner than MIT's global-eval approach and means multiple sketches
// could coexist later without stomping on each other.
//
// This sketch is a working example of colourPalette.js + effects.js: it
// builds a real OrganicWaveEffect (same technique as the Python system
// effect of the same name) using a real named palette, driven by
// window.appState.activity (kept up-to-date by app.js). Any future
// student sketch can use the exact same two libraries.
//
// It also exposes window.sampleCanvas(n) — the function app.js calls to
// grab N colours from the canvas to send back to Python (currently
// unconsumed server-side - see main.py's led_loop).

const sketch = (p) => {
  const W = 1200;
  const H = 100;
  // What an unlit physical strip actually looks like against its housing —
  // light grey, not black. Drawn as the canvas's base every frame; each
  // LED's colour is then composited on top at an alpha proportional to its
  // own brightness, so dim/off pixels show this grey instead of a fully
  // opaque dark rect.
  const OFF_STATE_GREY = 226;
  let sketchCanvas;
  let effect = null;
  let currentPaletteName = null;

  p.setup = () => {
    sketchCanvas = p.createCanvas(W, H);
    sketchCanvas.parent("sketch-container");
    p.colorMode(p.RGB, 255); // effects.js/colourPalette.js both work in RGB
    p.noStroke();
    // So app.js can build a pixel map without hardcoding canvas size here too.
    window.sketchDimensions = { width: W, height: H };
  };

  p.draw = () => {
    if (!window.paletteData || !window.numPixels) return; // still waiting on the server

    const paletteName = window.appState?.palette ?? Object.keys(window.paletteData)[0];
    if (!effect || paletteName !== currentPaletteName) {
      effect = new window.OrganicWaveEffect(p, window.numPixels, window.paletteData[paletteName]);
      currentPaletteName = paletteName;
    }

    const intensity = window.appState?.activity ?? 0;
    const frame = window.applyGamma(effect.step(intensity));

    p.background(OFF_STATE_GREY);

    const bandWidth = W / frame.length;
    for (let i = 0; i < frame.length; i++) {
      const [r, g, b] = frame[i];
      // sqrt-boosted, not a straight linear ratio - see zoneUtils.js's
      // paintSwatchStrip for the full reasoning (same alpha-over-grey
      // compositing here, same "dim colours crush to near-grey" bug
      // otherwise).
      const brightness = Math.sqrt(Math.max(r, g, b) / 255) * 255; // 0-255, doubles as this pixel's alpha
      p.fill(r, g, b, brightness);
      p.rect(i * bandWidth, 0, bandWidth, H);
    }

    // Sampling visualizer: mark exactly where each LED's colour would be
    // read from, so the pixel-map sampling process is visible, not just
    // the effect itself. window.pixelMap is set by app.js once the
    // server's LED config + this sketch's canvas size are both known.
    if (window.pixelMap) {
      p.push();
      p.noFill();
      p.stroke(255);
      p.strokeWeight(1);
      for (const { x, y } of window.pixelMap) {
        p.circle(x, y, 6);
      }
      p.pop();
    }
  };

  // Called by app.js with a pixel map (array of {x, y} points, one per LED,
  // built by pixelMap.js) and returns an array of [r,g,b] triples sampled at
  // those exact canvas coordinates.
  window.sampleCanvas = (pixelMap) => {
    if (!sketchCanvas || !pixelMap) return null;
    p.loadPixels();
    const pixels = [];
    for (const { x, y } of pixelMap) {
      const idx = (y * W + x) * 4;
      pixels.push([p.pixels[idx], p.pixels[idx + 1], p.pixels[idx + 2]]);
    }
    return pixels;
  };
};

// Kick it off
new p5(sketch);
