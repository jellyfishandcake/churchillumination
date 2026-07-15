// app.js — connects to the Python server, keeps the public dashboard alive,
// exposes the current sensor/state values as `window.sensors` and
// `window.appState` so the p5 sketch can read them, and drives the
// per-zone effect/palette pickers + LED-output swatch strips.

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
const zonesGrid = document.getElementById("zones-grid");
const sampledFrameCanvas = document.getElementById("sampled-frame-canvas");
const sampledFrameCtx = sampledFrameCanvas.getContext("2d");

// Built once we've received the server's LED config and the sketch has
// created its canvas. Left null until both are ready. Also exposed as
// window.pixelMap so sketch.js can draw the sample-point overlay.
let pixelMap = null;

// Populated once from the server's global effects/palettes lists, reused
// to build every zone card's <select> options - same list of choices for
// every zone, just applied per zone instead of once globally.
let effectsList = null;
let palettesList = null;

// One entry per zone, keyed by zone name: { zone, card, effectSelect,
// paletteSelect, canvas, ctx, sourceReadout }. Built once when leds.zones
// first arrives (zone count/pixel layout is fixed for the process's
// lifetime - only which effect/palette each zone is running changes).
const zoneCards = {};

function buildSelectOptions(select, names) {
  for (const name of names) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name.replace(/_/g, " ");
    select.appendChild(opt);
  }
}

// Matches sketch.js's OFF_STATE_GREY: a plain <canvas> 2D context has no
// notion of "draw this dim colour over a grey backdrop" the way p5's alpha
// compositing does, so the grey blend has to be computed by hand per pixel
// here instead. Display-only - the exact `frame` values are still what's
// sent to the real LEDs via leds.render_pixels() in main.py; this only
// changes how dim/off pixels are drawn on this webpage.
const SWATCH_OFF_STATE_GREY = 226;

function paintSwatchStrip(ctx, canvas, frame) {
  const n = frame.length;
  if (canvas.width !== n) canvas.width = n;
  const imageData = ctx.createImageData(n, 1);
  for (let i = 0; i < n; i++) {
    const [r, g, b] = frame[i];
    const alpha = Math.max(r, g, b) / 255; // brightness, doubles as blend weight
    imageData.data[i * 4] = r * alpha + SWATCH_OFF_STATE_GREY * (1 - alpha);
    imageData.data[i * 4 + 1] = g * alpha + SWATCH_OFF_STATE_GREY * (1 - alpha);
    imageData.data[i * 4 + 2] = b * alpha + SWATCH_OFF_STATE_GREY * (1 - alpha);
    imageData.data[i * 4 + 3] = 255;
  }
  ctx.putImageData(imageData, 0, 0);
}

// Mirrors main.py's _resolve_source - same dot-path walk into the same
// payload the server already sends every tick, so the dashboard can show
// "why" a zone looks the way it does without the server needing to publish
// a separately-computed value.
function resolveSource(data, path) {
  let value = data;
  for (const part of path.split(".")) {
    if (typeof value !== "object" || value === null || !(part in value)) return 0.0;
    value = value[part];
  }
  if (typeof value === "boolean") return value ? 1.0 : 0.0;
  if (typeof value === "number") return Math.min(Math.max(value, 0.0), 1.0);
  return 0.0;
}

function buildZoneCards(zones) {
  zonesGrid.innerHTML = "";
  for (const zone of zones) {
    const card = document.createElement("div");
    card.className = "zone-card";

    const heading = document.createElement("h3");
    heading.textContent = `${zone.name} (${zone.pixels}px)`;
    card.appendChild(heading);

    const effectField = document.createElement("div");
    effectField.className = "field";
    const effectLabel = document.createElement("span");
    effectLabel.className = "field-label";
    effectLabel.textContent = "Pattern";
    const effectSelect = document.createElement("select");
    buildSelectOptions(effectSelect, effectsList);
    effectField.append(effectLabel, effectSelect);
    card.appendChild(effectField);

    const paletteField = document.createElement("div");
    paletteField.className = "field";
    const paletteLabel = document.createElement("span");
    paletteLabel.className = "field-label";
    paletteLabel.textContent = "Palette";
    const paletteSelect = document.createElement("select");
    buildSelectOptions(paletteSelect, palettesList);
    paletteField.append(paletteLabel, paletteSelect);
    card.appendChild(paletteField);

    const canvas = document.createElement("canvas");
    canvas.className = "swatch-strip";
    canvas.width = zone.pixels;
    canvas.height = 1;
    card.appendChild(canvas);

    const sourceReadout = document.createElement("div");
    sourceReadout.className = "source-readout";
    card.appendChild(sourceReadout);

    zonesGrid.appendChild(card);

    const sendChoice = () => {
      wsHandle.send({
        control: {
          action: "set_zone_effect",
          zone: zone.name,
          effect: effectSelect.value,
          palette: paletteSelect.value,
        },
      });
    };
    effectSelect.addEventListener("change", sendChoice);
    paletteSelect.addEventListener("change", sendChoice);

    zoneCards[zone.name] = {
      zone, card, effectSelect, paletteSelect, canvas,
      ctx: canvas.getContext("2d"),
      sourceReadout,
    };
  }
}

const wsHandle = window.connectWS((data) => {
  const { state, leds, effects, palettes, runtime_settings, led_frame, palette_data } = data;

  // Always refresh (not just once) - a palette can be added at any time via
  // contribute.html or the CLI tools, and sketch.js needs the real colours
  // for it the moment it exists, not just at page load.
  if (palette_data) {
    window.paletteData = palette_data;
  }

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

  if (!effectsList && effects) effectsList = effects;
  if (!palettesList && palettes) palettesList = palettes;

  if (Object.keys(zoneCards).length === 0 && leds?.zones?.length && effectsList && palettesList) {
    buildZoneCards(leds.zones);
  }

  // Palettes, unlike effects, can appear at any time - contribute.html (or
  // the CLI tools) can add one while this page is already open. Diff-append
  // any name not already an <option> to every zone's palette picker, every
  // message, instead of populating once - this is what actually makes a
  // freshly-built palette show up here without a manual refresh.
  if (palettes) {
    for (const name in zoneCards) {
      const { paletteSelect } = zoneCards[name];
      const existing = new Set(Array.from(paletteSelect.options).map((o) => o.value));
      const missing = palettes.filter((p) => !existing.has(p));
      if (missing.length) buildSelectOptions(paletteSelect, missing);
    }
  }

  if (runtime_settings) {
    for (const name in zoneCards) {
      const { effectSelect, paletteSelect } = zoneCards[name];
      const zoneSettings = runtime_settings.zones?.[name];
      if (!zoneSettings) continue;
      if (document.activeElement !== effectSelect) effectSelect.value = zoneSettings.effect;
      if (document.activeElement !== paletteSelect) paletteSelect.value = zoneSettings.palette;
    }
  }

  if (led_frame) {
    let offset = 0;
    for (const name in zoneCards) {
      const { zone, ctx, canvas, sourceReadout } = zoneCards[name];
      const slice = led_frame.slice(offset, offset + zone.pixels);
      paintSwatchStrip(ctx, canvas, slice);
      sourceReadout.textContent = `${zone.source} = ${resolveSource(data, zone.source).toFixed(2)}`;
      offset += zone.pixels;
    }
  }

  // Update the globals the sketch reads.
  window.appState.mood = state.mood;
  window.appState.activity = state.activity_level;
  window.appState.presenceCount = state.presence_count;

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
  document.getElementById("audio-scene-value").textContent = state.audio_scene ?? "—";
}, status);

// Every 50ms, sample the p5 canvas and send colours to Python. Currently
// unconsumed server-side (led_loop runs each zone's own selected effect
// instead) — kept running for its own visual/demo value and as groundwork
// for a future user-sketch-upload feature, not because this is a bug.
// Painted locally into the "sampled from sketch" swatch so that
// dormant-but-computed step is actually visible, distinct from each zone's
// real LED output (painted above from led_frame).
setInterval(() => {
  if (typeof window.sampleCanvas !== "function") return;
  if (!pixelMap) return; // still waiting on the server's LED config

  const pixels = window.sampleCanvas(pixelMap);
  if (pixels) {
    paintSwatchStrip(sampledFrameCtx, sampledFrameCanvas, pixels);
    wsHandle.send({ pixels });
  }
}, 50);
