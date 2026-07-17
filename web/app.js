// app.js — connects to the Python server, keeps the public dashboard alive,
// and renders each zone's live LED output plus whichever sketch is
// currently selected. The selected sketch runs inside a sandboxed iframe
// (see sketchSandbox.js/web/sketchRunner.html) rather than sharing this
// page's window - so unlike before, this file no longer exposes
// window.sensors/window.appState itself; it posts the same data into the
// sandbox instead. Read-only: effect/palette controls live in admin.html's
// Zones tab, not here - see server.py's ADMIN_ACTIONS.

const { paintSwatchStrip, resolveSources, formatSources, buildSelectOptions } = window.zoneUtils;

const status = document.getElementById("status");
const zonesGrid = document.getElementById("zones-grid");
const sampledFrameCanvas = document.getElementById("sampled-frame-canvas");
const sampledFrameCtx = sampledFrameCanvas.getContext("2d");
const sketchSelect = document.getElementById("sketch-select");
const sketchError = document.getElementById("sketch-error");
const sketchContainer = document.getElementById("sketch-container");

const sandbox = window.createSketchSandbox(sketchContainer);

// {num_pixels, layout} - set once the server's leds config first arrives,
// read by wireSandboxLeds below whenever the sandboxed sketch reports its
// canvas size (which can happen again after loadSketch() reloads the
// iframe for a newly picked sketch).
let latestLeds = null;
const knownSketchNames = new Set(["built-in"]);

window.wireSandboxLeds(sandbox, () => latestLeds);

sandbox.onError((message) => {
  sketchError.textContent = `Sketch error: ${message}`;
});

async function loadSketchByName(name) {
  const url = name === "built-in" ? "/sketch.js" : `/sketches/${encodeURIComponent(name)}.js`;
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    const code = await res.text();
    sketchError.textContent = "";
    sandbox.loadSketch(code);
  } catch (err) {
    sketchError.textContent = `Couldn't load sketch "${name}": ${err.message}`;
  }
}

sketchSelect.innerHTML = '<option value="built-in">Built-in demo</option>';
sketchSelect.addEventListener("change", () => loadSketchByName(sketchSelect.value));
loadSketchByName("built-in");

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
  const { state, leds, led_frame, sketches } = data;

  if (leds) latestLeds = { num_pixels: leds.num_pixels, layout: leds.layout };

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

  // Diff-append any newly uploaded sketch name - same "appear at any time"
  // pattern the palette pickers elsewhere in this project already use.
  if (sketches) {
    const missing = sketches.filter((name) => !knownSketchNames.has(name));
    for (const name of missing) knownSketchNames.add(name);
    if (missing.length) buildSelectOptions(sketchSelect, missing);
  }

  sandbox.sendHelpers(window.helpersFromBroadcast(data));

  // Update the DOM dashboard. Noise isn't split out from activity_level
  // server-side yet - approximated the same way it always has been.
  document.getElementById("noise-value").textContent = state.activity_level.toFixed(2);
  document.getElementById("noise-bar").style.width = `${state.activity_level * 100}%`;
  document.getElementById("activity-value").textContent = state.activity_level.toFixed(2);
  document.getElementById("activity-bar").style.width = `${state.activity_level * 100}%`;
  document.getElementById("mood-value").textContent = state.mood;
  document.getElementById("presence-value").textContent = state.presence_count;
  document.getElementById("audio-scene-value").textContent = state.audio_scene ?? "—";
}, status);

// Currently unconsumed server-side (led_loop runs each zone's own selected
// effect instead) - kept for its own visual/demo value, same as before the
// sandbox existed. Painted locally into the "sampled from sketch" swatch,
// distinct from each zone's real LED output (painted above from led_frame).
sandbox.onPixels((pixels) => {
  paintSwatchStrip(sampledFrameCtx, sampledFrameCanvas, pixels);
  wsHandle.send({ pixels });
});
