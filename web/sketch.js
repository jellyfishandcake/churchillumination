// sketch.js — the p5 visual, in instance mode.
//
// Instance mode means every p5 function is on a passed-in `p` object
// (p.background, p.noise, p.color) rather than being global. This is
// cleaner than MIT's global-eval approach and means multiple sketches
// could coexist later without stomping on each other.
//
// The sketch reads two globals kept up-to-date by app.js:
//   window.sensors.noise.level  — 0..1, current room noise
//   window.appState.hue         — the mapped hue from the intelligence layer
//
// It also exposes window.sampleCanvas(n) — the function app.js calls to
// grab N colours from the canvas to send back to Python for the LEDs.
 
const sketch = (p) => {
  const W = 1200;
  const H = 100;
  let sketchCanvas;

  // What's actually drawn each frame — eased toward the latest value from
  // Python each frame rather than snapping to it the instant a WebSocket
  // message arrives (~20/sec), since draw() itself runs at ~60fps. Without
  // this, every update looks like a hard cut. EASE only makes sense here
  // because hue's mapped range (state_to_visual: 200 down to 0) never
  // wraps past 360 — a hue formula that did wrap would need circular
  // interpolation instead of a plain lerp.
  let displayHue = 200;
  let displayBrightness = 0.2;
  const EASE = 0.08; // lower = smoother/slower, higher = snappier

  p.setup = () => {
    sketchCanvas = p.createCanvas(W, H);
    sketchCanvas.parent("sketch-container");
    p.colorMode(p.HSB, 360, 100, 100);
    p.noStroke();
    // So app.js can build a pixel map without hardcoding canvas size here too.
    window.sketchDimensions = { width: W, height: H };
  };

  p.draw = () => {
    // Read live sensor + state values (fresh every frame)
    const noise = window.sensors?.noise?.level ?? 0;
    const targetHue = window.appState?.hue ?? 200;
    const targetBrightness = window.appState?.brightness ?? 0.5;

    displayHue = p.lerp(displayHue, targetHue, EASE);
    displayBrightness = p.lerp(displayBrightness, targetBrightness, EASE);

    // Perlin noise flow — the "living wall" idiom from MIT.
    // The KEY move: noise level modulates the animation speed.
    // Quiet room → slow flow. Busy room → agitated.
    const speed = 0.002 + noise * 0.02;
    const scale = 0.008;

    for (let x = 0; x < W; x += 4) {
      const n = p.noise(x * scale, p.frameCount * speed);
      const localHue = (displayHue + n * 60) % 360;
      const sat = 60 + noise * 40;
      const bri = 100 * displayBrightness * (0.5 + 0.5 * n);
      p.fill(localHue, sat, bri);
      p.rect(x, 0, 4, H);
    }

    // Sampling visualizer: mark exactly where each LED's colour would be
    // read from, so the pixel-map sampling process is visible, not just
    // the abstract flow field. window.pixelMap is set by app.js once the
    // server's LED config + this sketch's canvas size are both known.
    if (window.pixelMap) {
      p.push();
      p.noFill();
      p.stroke(0, 0, 100); // white ring, still in this sketch's HSB mode
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
    // Switch to RGB briefly to read pixel colours as 0-255
    p.colorMode(p.RGB, 255);
    p.loadPixels();
    const pixels = [];
    for (const { x, y } of pixelMap) {
      const idx = (y * W + x) * 4;
      pixels.push([p.pixels[idx], p.pixels[idx + 1], p.pixels[idx + 2]]);
    }
    p.colorMode(p.HSB, 360, 100, 100); // switch back
    return pixels;
  };
};
 
// Kick it off
new p5(sketch);