// cast.js — full-screen, no-chrome sketch display for projecting the p5
// visual as an actual light source (e.g. a projector, standing in for or
// alongside the physical LED strips), rather than index.html's boxed-in
// preview panel. Same live sensor/state wiring as app.js's sketch panel
// (see sketchSandbox.js/wsClient.js), just without any of app.js's other
// dashboard elements (zone cards, metrics, QR code) that don't belong on
// something meant to be projected as-is.
//
// Which sketch to run: ?sketch=<name> in the URL (matching web/sketches/
// <name>.js, same names app.js's picker lists), defaults to the built-in
// demo if omitted - e.g. /cast.html?sketch=my_sketch

const sketchContainer = document.getElementById("sketch-container");
const sandbox = window.createSketchSandbox(sketchContainer);

let latestLeds = null;
window.wireSandboxLeds(sandbox, () => latestLeds);

sandbox.onError((message) => {
  // No on-screen error UI on purpose - this page is meant to be projected
  // clean. Errors still surface in the console for whoever's driving the
  // device connected to the projector.
  console.error("Sketch error:", message);
});

async function loadSketchByName(name) {
  const url = name === "built-in" ? "/sketch.js" : `/sketches/${encodeURIComponent(name)}.js`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  const code = await res.text();
  sandbox.loadSketch(code);
}

const sketchName = new URLSearchParams(location.search).get("sketch") || "built-in";
loadSketchByName(sketchName).catch((err) => console.error(`Couldn't load sketch "${sketchName}":`, err));

window.connectWS((data) => {
  const { leds } = data;
  if (leds) latestLeds = { num_pixels: leds.num_pixels, layout: leds.layout };
  sandbox.sendHelpers(window.helpersFromBroadcast(data));
}, null); // no status element on this page - it's meant to be projected clean
