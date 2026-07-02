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
 
  p.setup = () => {
    sketchCanvas = p.createCanvas(W, H);
    sketchCanvas.parent("sketch-container");
    p.colorMode(p.HSB, 360, 100, 100);
    p.noStroke();
  };
 
  p.draw = () => {
    // Read live sensor + state values (fresh every frame)
    const noise = window.sensors?.noise?.level ?? 0;
    const hue = window.appState?.hue ?? 200;
    const brightness = window.appState?.brightness ?? 0.5;
 
    // Perlin noise flow — the "living wall" idiom from MIT.
    // The KEY move: noise level modulates the animation speed.
    // Quiet room → slow flow. Busy room → agitated.
    const speed = 0.002 + noise * 0.02;
    const scale = 0.008;
 
    for (let x = 0; x < W; x += 4) {
      const n = p.noise(x * scale, p.frameCount * speed);
      const localHue = (hue + n * 60) % 360;
      const sat = 60 + noise * 40;
      const bri = 100 * brightness * (0.5 + 0.5 * n);
      p.fill(localHue, sat, bri);
      p.rect(x, 0, 4, H);
    }
  };
 
  // Called by app.js — samples the canvas at N evenly-spaced points along y=H/2
  // and returns an array of [r,g,b] triples. This is the MIT pattern from
  // PlayingNow.vue, adapted for instance-mode + our pixel-per-LED contract.
  window.sampleCanvas = (n) => {
    if (!sketchCanvas) return null;
    // Switch to RGB briefly to read pixel colours as 0-255
    p.colorMode(p.RGB, 255);
    p.loadPixels();
    const pixels = [];
    const y = Math.floor(H / 2);
    for (let i = 0; i < n; i++) {
      const x = Math.floor((i / (n - 1)) * (W - 1));
      const idx = (y * W + x) * 4;
      pixels.push([p.pixels[idx], p.pixels[idx + 1], p.pixels[idx + 2]]);
    }
    p.colorMode(p.HSB, 360, 100, 100); // switch back
    return pixels;
  };
};
 
// Kick it off
new p5(sketch);