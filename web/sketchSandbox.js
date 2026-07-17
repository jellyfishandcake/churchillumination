// sketchSandbox.js — creates and owns a sandboxed `<iframe
// sandbox="allow-scripts">` running web/sketchRunner.html, and speaks the
// postMessage protocol documented there. Shared by index.html's dashboard
// (viewing whichever sketch is selected) and create.html (live preview
// before submitting) - one implementation, same reasoning as this
// project's other shared web/*.js files.
//
// Without `allow-same-origin`, the iframe gets an opaque origin: this
// controller can hold a reference to `iframe.contentWindow` but can't read
// anything off it directly, and the iframe can't reach back into this
// page's window either. postMessage is the only channel - see the message
// listener below and sketchRunner.html's matching half.

function createSketchSandbox(container) {
  let iframe = null;
  let pendingCode = null;
  let lastHelpers = null;
  let lastLeds = null;
  let onPixelsCb = null;
  let onErrorCb = null;
  let onCanvasReadyCb = null;

  function freshIframe() {
    // A full reload (not just re-posting new code into the same document)
    // so a previous sketch's p5 instance/draw loop/global state can't leak
    // into the next one - matches the mental model of "picking a different
    // sketch" being a clean start, not a hot-swap.
    const next = document.createElement("iframe");
    next.setAttribute("sandbox", "allow-scripts");
    next.className = "sketch-frame";
    next.src = "/sketchRunner.html";
    container.innerHTML = "";
    container.appendChild(next);
    iframe = next;
  }

  window.addEventListener("message", (event) => {
    if (!iframe || event.source !== iframe.contentWindow) return;
    const data = event.data || {};
    if (data.type === "ready") {
      // Re-send whatever this sandbox already knew, since a fresh iframe
      // (freshIframe() above) starts with none of it.
      if (pendingCode) iframe.contentWindow.postMessage({ type: "sketch", code: pendingCode }, "*");
      if (lastLeds) iframe.contentWindow.postMessage({ type: "leds", ...lastLeds }, "*");
      if (lastHelpers) iframe.contentWindow.postMessage({ type: "helpers", helpers: lastHelpers }, "*");
    } else if (data.type === "pixels") {
      onPixelsCb?.(data.pixels);
    } else if (data.type === "canvasReady") {
      onCanvasReadyCb?.(data.width, data.height);
    } else if (data.type === "error") {
      onErrorCb?.(data.message);
    }
  });

  freshIframe();

  return {
    loadSketch(code) {
      pendingCode = code;
      freshIframe(); // "ready" handshake above resends it once loaded
    },
    sendHelpers(helpers) {
      lastHelpers = helpers;
      iframe?.contentWindow.postMessage({ type: "helpers", helpers }, "*");
    },
    sendLeds(numPixels, pixelMap) {
      lastLeds = { numPixels, pixelMap };
      iframe?.contentWindow.postMessage({ type: "leds", numPixels, pixelMap }, "*");
    },
    onPixels(cb) { onPixelsCb = cb; },
    onError(cb) { onErrorCb = cb; },
    onCanvasReady(cb) { onCanvasReadyCb = cb; },
  };
}

// Shared by app.js/create.js: turns one broadcast tick's payload into the
// {sensors, appState, paletteData} shape sketchRunner.html applies to its
// window globals. Kept in one place so the "noise isn't split out from
// activity_level yet" approximation (see app.js's original comment) only
// needs updating once when rules.py eventually does split them apart.
function helpersFromBroadcast(data) {
  const state = data.state || {};
  return {
    appState: {
      mood: state.mood,
      activity: state.activity_level,
      presenceCount: state.presence_count,
    },
    sensors: { noise: { level: state.activity_level ?? 0.0 } },
    paletteData: data.palette_data,
  };
}

// Shared by app.js/create.js: once the sandboxed sketch reports its canvas
// size, build the pixel map (same window.buildPixelMap pixelMap.js already
// provides) and hand it to the sandbox. `getLeds()` returns the current
// `{num_pixels, layout}` (or null if that hasn't arrived over the
// websocket yet).
function wireSandboxLeds(sandbox, getLeds) {
  sandbox.onCanvasReady((width, height) => {
    const leds = getLeds();
    if (!leds || !window.buildPixelMap) return;
    const pixelMap = window.buildPixelMap(leds.layout, leds.num_pixels, width, height);
    sandbox.sendLeds(leds.num_pixels, pixelMap);
  });
}

window.createSketchSandbox = createSketchSandbox;
window.helpersFromBroadcast = helpersFromBroadcast;
window.wireSandboxLeds = wireSandboxLeds;
