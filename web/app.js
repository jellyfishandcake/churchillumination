// app.js — connects to the Python server, keeps the public dashboard alive,
// exposes the current sensor/state values as `window.sensors` and
// `window.appState` so the p5 sketch can read them, and drives the
// effect/palette picker + LED-output swatch-strip visualizer.

// These are the globals the sketch will see. Live-updated.
window.sensors = {
  noise: { level: 0.0 },
};
window.appState = {
  mood: "neutral",
  activity: 0.0,
  presenceCount: 0,
};

const status = document.getElementById("status");
const effectSelect = document.getElementById("effect-select");
const paletteSelect = document.getElementById("palette-select");
const ledFrameCanvas = document.getElementById("led-frame-canvas");
const ledFrameCtx = ledFrameCanvas.getContext("2d");
const sampledFrameCanvas = document.getElementById("sampled-frame-canvas");
const sampledFrameCtx = sampledFrameCanvas.getContext("2d");

// Built once we've received the server's LED config and the sketch has
// created its canvas. Left null until both are ready. Also exposed as
// window.pixelMap so sketch.js can draw the sample-point overlay.
let pixelMap = null;
let selectsPopulated = false;

function populateSelects(effects, palettes) {
  for (const name of effects) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name.replace(/_/g, " ");
    effectSelect.appendChild(opt);
  }
  for (const name of palettes) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    paletteSelect.appendChild(opt);
  }
  selectsPopulated = true;
}

function paintSwatchStrip(ctx, canvas, frame) {
  const n = frame.length;
  if (canvas.width !== n) canvas.width = n;
  const imageData = ctx.createImageData(n, 1);
  for (let i = 0; i < n; i++) {
    const [r, g, b] = frame[i];
    imageData.data[i * 4] = r;
    imageData.data[i * 4 + 1] = g;
    imageData.data[i * 4 + 2] = b;
    imageData.data[i * 4 + 3] = 255;
  }
  ctx.putImageData(imageData, 0, 0);
}

const wsHandle = window.connectWS((data) => {
  const { state, leds, effects, palettes, runtime_settings, led_frame, palette_data } = data;

  if (!pixelMap && leds && window.buildPixelMap && window.sketchDimensions) {
    pixelMap = window.buildPixelMap(
      leds.layout,
      leds.num_pixels,
      window.sketchDimensions.width,
      window.sketchDimensions.height
    );
    window.pixelMap = pixelMap; // for sketch.js's sample-point overlay
    window.numPixels = leds.num_pixels; // for sketch.js's own effect instance
  }

  if (!window.paletteData && palette_data) {
    window.paletteData = palette_data; // real colours for sketch.js to use
  }

  if (!selectsPopulated && effects && palettes) {
    populateSelects(effects, palettes);
  }
  if (selectsPopulated && runtime_settings) {
    effectSelect.value = runtime_settings.effect;
    paletteSelect.value = runtime_settings.palette;
  }

  if (led_frame) {
    paintSwatchStrip(ledFrameCtx, ledFrameCanvas, led_frame);
  }

  // Update the globals the sketch reads.
  window.appState.mood = state.mood;
  window.appState.activity = state.activity_level;
  window.appState.presenceCount = state.presence_count;
  if (runtime_settings) {
    window.appState.palette = runtime_settings.palette; // which named palette to draw with
  }

  // We don't yet get raw noise separately from the server — for now we
  // approximate it as activity_level. When we split them apart in
  // rules.py, this will just start receiving a separate field.
  window.sensors.noise.level = state.activity_level;

  // Update the DOM dashboard
  document.getElementById("noise-value").textContent = window.sensors.noise.level.toFixed(2);
  document.getElementById("noise-bar").style.width = `${window.sensors.noise.level * 100}%`;
  document.getElementById("activity-value").textContent = state.activity_level.toFixed(2);
  document.getElementById("activity-bar").style.width = `${state.activity_level * 100}%`;
  document.getElementById("mood-value").textContent = state.mood;
  document.getElementById("presence-value").textContent = state.presence_count;
}, status);

function sendEffectChoice() {
  wsHandle.send({
    control: {
      action: "set_effect",
      effect: effectSelect.value,
      palette: paletteSelect.value,
    },
  });
}
effectSelect.addEventListener("change", sendEffectChoice);
paletteSelect.addEventListener("change", sendEffectChoice);

// Every 50ms, sample the p5 canvas and send colours to Python. Currently
// unconsumed server-side (led_loop runs the selected system effect
// instead) — kept running for its own visual/demo value and as groundwork
// for a future user-sketch-upload feature, not because this is a bug.
// Painted locally into the "sampled from sketch" swatch (pipeline stage 1)
// so that dormant-but-computed step is actually visible, distinct from
// the real LED output (stage 2, painted above from led_frame).
setInterval(() => {
  if (typeof window.sampleCanvas !== "function") return;
  if (!pixelMap) return; // still waiting on the server's LED config

  const pixels = window.sampleCanvas(pixelMap);
  if (pixels) {
    paintSwatchStrip(sampledFrameCtx, sampledFrameCanvas, pixels);
    wsHandle.send({ pixels });
  }
}, 50);
