// zoneUtils.js — shared helpers for anything that shows the per-zone LED
// strip state: index.html's read-only dashboard (app.js) and admin.html's
// Zones tab (admin.js). One implementation instead of two copies, same as
// this project's other small shared files (pixelMap.js, colourPalette.js).
//
// Wrapped in an IIFE, unlike this project's other shared files - app.js
// and admin.js both destructure `window.zoneUtils` into local `const`s of
// the same names (e.g. `const { paintSwatchStrip } = window.zoneUtils`),
// which would collide with these being plain global `function`
// declarations too (all non-module <script> tags share one global scope -
// a `const` of the same name as an existing global is a SyntaxError that
// aborts the whole consuming script). Only `window.zoneUtils` needs to be
// global; everything inside it doesn't.
(function () {

// Matches sketch.js's OFF_STATE_GREY: a plain <canvas> 2D context has no
// notion of "draw this dim colour over a grey backdrop" the way p5's alpha
// compositing does, so the grey blend has to be computed by hand per pixel
// here instead. Display-only - the exact `frame` values are still what's
// sent to the real LEDs via leds.render_pixels() in main.py; this only
// changes how dim/off pixels are drawn on a webpage.
const ZONE_SWATCH_OFF_STATE_GREY = 226;

function paintSwatchStrip(ctx, canvas, frame) {
  const n = frame.length;
  if (canvas.width !== n) canvas.width = n;
  const imageData = ctx.createImageData(n, 1);
  for (let i = 0; i < n; i++) {
    const [r, g, b] = frame[i];
    // sqrt, not a straight linear ratio - a plain `brightness/255` alpha
    // crushes dim-but-saturated pixels toward the grey backdrop almost
    // completely (e.g. a clearly-red [60,10,10] blends to a near-grey
    // (187,175,175) - barely readable as "red" at all) even though that
    // pixel is genuinely saturated in the real data sent to the physical
    // LEDs. sqrt boosts low alphas disproportionately so dim colours still
    // read as colours here, while true off/near-zero pixels still fade to
    // grey as alpha approaches 0.
    const alpha = Math.sqrt(Math.max(r, g, b) / 255);
    imageData.data[i * 4] = r * alpha + ZONE_SWATCH_OFF_STATE_GREY * (1 - alpha);
    imageData.data[i * 4 + 1] = g * alpha + ZONE_SWATCH_OFF_STATE_GREY * (1 - alpha);
    imageData.data[i * 4 + 2] = b * alpha + ZONE_SWATCH_OFF_STATE_GREY * (1 - alpha);
    imageData.data[i * 4 + 3] = 255;
  }
  ctx.putImageData(imageData, 0, 0);
}

// Mirrors main.py's _resolve_one_source - same dot-path walk (and the same
// optional {path, min, max} range-mapping) into the same payload the
// server already sends every tick, so the dashboard/admin panel can show
// "why" a zone looks the way it does without the server needing to publish
// a separately-computed value.
function resolveOneSource(data, spec) {
  const path = typeof spec === "string" ? spec : spec.path;
  const lo = typeof spec === "string" ? undefined : spec.min;
  const hi = typeof spec === "string" ? undefined : spec.max;

  let value = data;
  for (const part of path.split(".")) {
    if (typeof value !== "object" || value === null || !(part in value)) return 0.0;
    value = value[part];
  }
  if (typeof value === "boolean") return value ? 1.0 : 0.0;
  if (typeof value !== "number") return 0.0;

  if (lo !== undefined && hi !== undefined && hi !== lo) {
    value = (value - lo) / (hi - lo);
  }
  return Math.min(Math.max(value, 0.0), 1.0);
}

// { name: 0..1 value, ... } for every named source a zone declares.
function resolveSources(data, sourceMap) {
  const resolved = {};
  for (const name in sourceMap) {
    resolved[name] = resolveOneSource(data, sourceMap[name]);
  }
  return resolved;
}

function formatSources(resolved) {
  return Object.entries(resolved)
    .map(([name, value]) => `${name} = ${value.toFixed(2)}`)
    .join("  ");
}

function buildSelectOptions(select, names) {
  for (const name of names) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name.replace(/_/g, " ");
    select.appendChild(opt);
  }
}

window.zoneUtils = { paintSwatchStrip, resolveSources, formatSources, buildSelectOptions };

})();
