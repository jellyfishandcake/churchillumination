// app.js — connects to the Python server, keeps the public dashboard alive,
// exposes the current sensor/state values as `window.sensors` and
// `window.appState` so the p5 sketch can read them, and renders each
// zone's live LED output. Read-only: effect/palette controls live in
// admin.html's Zones tab, not here - see server.py's ADMIN_ACTIONS.

// These are the globals the sketch will see. Live-updated.
window.sensors = {
  noise: { level: 0.0 },
};
window.appState = {
  mood: "neutral",
  activity: 0.0,
  presenceCount: 0,
};

const { paintSwatchStrip, resolveSources, formatSources } = window.zoneUtils;

const status = document.getElementById("status");
const zonesGrid = document.getElementById("zones-grid");
const sampledFrameCanvas = document.getElementById("sampled-frame-canvas");
const sampledFrameCtx = sampledFrameCanvas.getContext("2d");

// Built once we've received the server's LED config and the sketch has
// created its canvas. Left null until both are ready. Also exposed as
// window.pixelMap so sketch.js can draw the sample-point overlay.
let pixelMap = null;

// One entry per zone, keyed by zone name: { zone, canvas, ctx, sourceReadout }.
// Built once when leds.zones first arrives - zone count/pixel layout is
// fixed for the process's lifetime.
const zoneCards = {};

function buildZoneCards(zones) {
  zonesGrid.innerHTML = "";
  for (const zone of zones) {
    const card = document.createElement("div");
    card.className = "zone-card";

    const heading = document.createElement("h3");
    heading.textContent = `${zone.name} (${zone.pixels}px)`;
    card.appendChild(heading);

    const canvas = document.createElement("canvas");
    canvas.className = "swatch-strip";
    canvas.width = zone.pixels;
    canvas.height = 1;
    card.appendChild(canvas);

    const sourceReadout = document.createElement("div");
    sourceReadout.className = "source-readout";
    card.appendChild(sourceReadout);

    zonesGrid.appendChild(card);
    zoneCards[zone.name] = { zone, canvas, ctx: canvas.getContext("2d"), sourceReadout };
  }
}

const wsHandle = window.connectWS((data) => {
  const { state, leds, led_frame, palette_data } = data;

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

  if (Object.keys(zoneCards).length === 0 && leds?.zones?.length) {
    buildZoneCards(leds.zones);
  }

  if (led_frame) {
    let offset = 0;
    for (const name in zoneCards) {
      const { zone, ctx, canvas, sourceReadout } = zoneCards[name];
      const slice = led_frame.slice(offset, offset + zone.pixels);
      paintSwatchStrip(ctx, canvas, slice);
      sourceReadout.textContent = formatSources(resolveSources(data, zone.source));
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
