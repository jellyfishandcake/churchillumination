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

// Display-only - the exact `frame` values are still what's sent to the real
// LEDs via leds.render_pixels() in main.py; this only changes how pixels are
// drawn on a webpage. Any pixel with real colour (even dim) paints fully
// opaque (2026-08-16 - was alpha-faded toward whatever's behind
// .swatch-strip by brightness, which meant a dim zone never showed its
// actual colour, just an increasingly faint blend with the page's
// background - read as a washed-out grey rather than "this zone's real
// colour, just dark"). But a pixel that's exactly [0,0,0] - genuinely no
// signal, not just dim - paints fully transparent instead of solid black
// (2026-08-18, reported back as accelerometer/heart_rate showing "black
// background" whenever those zones sit mostly/fully off - see .zone-card's
// own background in style.css, meant to show through here). Only exact
// black gets this treatment, not "dim" generally, specifically so this
// doesn't regress back into the same washed-out-grey problem the opaque
// change above was fixing - a colour that's merely dark still paints as
// that real dark colour, not faded toward transparent.
function paintSwatchStrip(ctx, canvas, frame) {
  const n = frame.length;
  if (canvas.width !== n) canvas.width = n;
  const imageData = ctx.createImageData(n, 1);
  for (let i = 0; i < n; i++) {
    const [r, g, b] = frame[i];
    imageData.data[i * 4] = r;
    imageData.data[i * 4 + 1] = g;
    imageData.data[i * 4 + 2] = b;
    imageData.data[i * 4 + 3] = (r === 0 && g === 0 && b === 0) ? 0 : 255;
  }
  ctx.putImageData(imageData, 0, 0);
}

// Mirrors main.py's _resolve_one_source - same dot-path walk and the same
// optional {path, min, max} range-mapping - so the dashboard/admin panel
// can show "why" a zone looks the way it does without the server needing
// to publish a separately-computed value.
//
// Deliberately displays the raw real-world reading (e.g. real BPM, real
// °C) when a {path, min, max} range is configured, NOT the 0..1 value that
// range-mapping actually produces for the effect - that 0..1 fraction is
// what a zone's effect receives internally, but as a *readout* it was
// unreadable (reported 2026-08-19: heart_rate's bpm showing as "0.48" with
// no way to tell that's ~107 real bpm without doing the {min:40, max:180}
// math by hand). A plain string source (no range) is already a native 0..1
// signal (motion, ripple, ...) with no real-world unit to show instead, so
// those still clamp/display as 0..1 same as before.
function resolveOneSource(data, spec) {
  const path = typeof spec === "string" ? spec : spec.path;
  const lo = typeof spec === "string" ? undefined : spec.min;
  const hi = typeof spec === "string" ? undefined : spec.max;
  const ranged = lo !== undefined && hi !== undefined && hi !== lo;

  let value = data;
  for (const part of path.split(".")) {
    if (typeof value !== "object" || value === null || !(part in value)) return 0.0;
    value = value[part];
  }
  if (typeof value === "boolean") value = value ? 1.0 : 0.0;
  if (typeof value !== "number") return 0.0;

  if (!ranged) return Math.min(Math.max(value, 0.0), 1.0);
  return Math.min(Math.max(value, lo), hi);
}

// { name: value, ... } for every named source a zone declares - real-world
// units where the zone config gives a {min, max} range to convert with,
// otherwise the native 0..1 signal (see resolveOneSource above).
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
