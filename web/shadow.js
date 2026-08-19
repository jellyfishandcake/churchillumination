// shadow.js — full-screen, no-chrome projector display: draws a background
// image, then darkens wherever the central MLX90640 thermal camera
// currently sees a person (src/sensing/motion.py's thermal_mask, published
// over the same broadcast as everything else - see wsClient.js), so someone
// standing in front of the camera reads as a shadow cast over the image.
//
// Deliberately built on motion.py's slow-baseline blob mask, not the
// `motion` frame-diff scalar every LED zone uses - a person standing still
// should keep casting a shadow, not fade back to nothing after a couple
// frames the way frame-diff motion does.
//
// Which image: normally whatever's been uploaded via backdrop.html (see
// set_shadow_backdrop in src/net/server.py) - this page watches
// shadow_backdrop.version in the broadcast and re-fetches
// /uploads/shadow_backdrop.jpg whenever it changes, rather than the version
// broadcast itself carrying image bytes. ?image=<url> in the URL (e.g.
// /shadow.html?image=/my.jpg) overrides that, for testing a specific image
// without needing to upload it first. Falls back to a plain dark backdrop
// if neither is present yet or the file fails to load.
//
// FLIP_X/FLIP_Y below are placeholders - the sensor's pixel order (is row 0
// the top or bottom, is it mirrored left/right) hasn't been confirmed
// against a real person standing in a known spot in front of the real
// camera yet (see motion.py's own placeholder comment on this). If the
// shadow shows up mirrored or upside-down relative to where someone's
// actually standing, flip the relevant one here rather than in motion.py -
// this is a display-orientation fix, not a sensor one.

const FLIP_X = true;
const FLIP_Y = true;

const canvas = document.getElementById("canvas");
const ctx = canvas.getContext("2d");

// TEMPORARY 2026-08-19 debug state (see drawDebugOverlay below) - remove
// once the "backdrop always shows the cream fallback" bug is found.
// console.error/console.log alone weren't answerable: this page gets
// tested by scanning a QR code on a phone (see backdrop.html/qr_backdrop.png),
// where opening devtools isn't realistically available the way it is on a
// laptop, so the same information needs to also be readable directly off
// the screen, not just the console.
let debugWsMessageCount = 0;
let debugLastBackdropVersion = null;
let debugLoadResult = "(no image load attempted yet)";

const bg = new Image();
bg.onerror = () => {
  debugLoadResult = `FAILED to load: ${bg.src}`;
  console.error("[shadow] backdrop image failed to load:", bg.src);
};
bg.onload = () => {
  debugLoadResult = `loaded OK: ${bg.src} (${bg.naturalWidth}x${bg.naturalHeight})`;
  console.log("[shadow] backdrop image loaded:", bg.src, `${bg.naturalWidth}x${bg.naturalHeight}`);
};
const imageUrlOverride = new URLSearchParams(location.search).get("image");
if (imageUrlOverride) bg.src = imageUrlOverride;

let loadedBackdropVersion = null;

function maybeLoadBackdrop(version) {
  // ?image= always wins, and once set there's no live backdrop to swap to -
  // don't fight a manual override with the broadcast. version 0 means
  // nobody's uploaded a backdrop yet (server.py's initial state) - nothing
  // to fetch, stay on the plain dark fallback.
  if (imageUrlOverride || version === loadedBackdropVersion || version === 0) return;
  loadedBackdropVersion = version;
  bg.src = `/uploads/shadow_backdrop.jpg?v=${version}`; // cache-bust: same path, new query string forces a re-fetch
}

// TEMPORARY - see the debug state comment above. Only draws while the
// cream fallback is showing (i.e. exactly the broken state), so it's
// invisible/no-op once a backdrop is actually displaying correctly.
function drawDebugOverlay() {
  const lines = [
    `ws messages received: ${debugWsMessageCount}`,
    `last shadow_backdrop.version seen: ${debugLastBackdropVersion}`,
    `?image= override: ${imageUrlOverride || "(none)"}`,
    `image load: ${debugLoadResult}`,
  ];
  ctx.font = "14px monospace";
  ctx.textBaseline = "top";
  lines.forEach((line, i) => {
    const y = 10 + i * 18;
    ctx.fillStyle = "rgba(255,255,255,0.85)";
    ctx.fillRect(8, y - 2, ctx.measureText(line).width + 8, 18);
    ctx.fillStyle = "#111";
    ctx.fillText(line, 12, y);
  });
}

// The mask arrives at sensor resolution (e.g. 32x24) - painted onto this
// small offscreen canvas first, then drawn scaled up onto the real canvas
// with smoothing on. That upscale blur is what turns a low-res sensor grid
// into a soft-edged shadow instead of a blocky one.
const maskCanvas = document.createElement("canvas");
const maskCtx = maskCanvas.getContext("2d");

let latestMask = null; // { width, height, values } from the most recent broadcast tick

function resize() {
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
}
window.addEventListener("resize", resize);
resize();

function drawBackground() {
  if (!bg.complete || bg.naturalWidth === 0) {
    // Light, not dark (was #111 until 2026-08-17) - the shadow mask drawn
    // on top of this is itself dark/black (see drawShadow's alpha-only
    // black fill), so a dark fallback made the whole page read as solid
    // black with nothing visible any time no backdrop photo had been
    // uploaded yet - exactly the case during bring-up/testing, before
    // backdrop.html has ever been used. A light fallback means the shadow
    // is actually visible against it instead of blending into the dark.
    // Matches style.css's --bg (#f7f3ec) so this no-chrome page (no
    // <link> to style.css - see this file's own header comment on why)
    // still reads as the same cream as the rest of the dashboard rather
    // than a mismatched grey.
    ctx.fillStyle = "#f7f3ec";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    drawDebugOverlay();  // TEMPORARY - see its own comment
    return;
  }
  // "cover" fit - fill the canvas, cropping whichever axis overflows, same
  // idea as CSS background-size: cover.
  const scale = Math.max(canvas.width / bg.naturalWidth, canvas.height / bg.naturalHeight);
  const w = bg.naturalWidth * scale;
  const h = bg.naturalHeight * scale;
  ctx.drawImage(bg, (canvas.width - w) / 2, (canvas.height - h) / 2, w, h);
}

function flippedIndex(i, width, height) {
  let x = i % width;
  let y = Math.floor(i / width);
  if (FLIP_X) x = width - 1 - x;
  if (FLIP_Y) y = height - 1 - y;
  return y * width + x;
}

function drawShadow() {
  if (!latestMask) return;
  const { width, height, values } = latestMask;

  maskCanvas.width = width;
  maskCanvas.height = height;
  const imageData = maskCtx.createImageData(width, height);
  for (let i = 0; i < values.length; i++) {
    const srcIndex = (FLIP_X || FLIP_Y) ? flippedIndex(i, width, height) : i;
    const alpha = Math.round(Math.min(1, Math.max(0, values[srcIndex])) * 255);
    imageData.data[i * 4 + 0] = 0;
    imageData.data[i * 4 + 1] = 0;
    imageData.data[i * 4 + 2] = 0;
    imageData.data[i * 4 + 3] = alpha;
  }
  maskCtx.putImageData(imageData, 0, 0);

  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(maskCanvas, 0, 0, width, height, 0, 0, canvas.width, canvas.height);
}

function render() {
  drawBackground();
  drawShadow();
  requestAnimationFrame(render);
}
requestAnimationFrame(render);

window.connectWS((data) => {
  debugWsMessageCount++;  // TEMPORARY - see drawDebugOverlay's comment. A count stuck at 0 means the websocket itself never connected/received anything.

  const mask = data.sensors?.thermal_mask;
  const width = data.sensors?.thermal_width;
  const height = data.sensors?.thermal_height;
  if (Array.isArray(mask) && width && height) {
    latestMask = { width, height, values: mask };
  }

  if (data.shadow_backdrop) {
    debugLastBackdropVersion = data.shadow_backdrop.version;  // TEMPORARY
    maybeLoadBackdrop(data.shadow_backdrop.version);
  }
}, null); // no status element on this page - it's meant to be projected clean
