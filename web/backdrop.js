// backdrop.js — public "set the shadow display's background photo" page.
// Reads a photo, downscales/re-encodes it client-side as JPEG (same reason
// contribute.js does this: no point sending a multi-MB original over a
// local WiFi link), and sends it as a `{"control": {"action":
// "set_shadow_backdrop", ...}}` message over the same websocket channel
// every other page uses. No login needed - same public trust tier as
// contribute.html's build_palette action (see CLAUDE.md/server.py's
// ADMIN_ACTIONS on the admin/public split).
//
// The server saves this to a single shared file (web/uploads/
// shadow_backdrop.jpg) and bumps a version counter in the broadcast state
// - web/shadow.js watches that counter and re-fetches the file when it
// changes, rather than this page (or the server) pushing image bytes
// through the 20Hz broadcast itself.

const status = document.getElementById("status");
const photoInput = document.getElementById("photo-input");
const uploadButton = document.getElementById("upload-button");
const jobStatusEl = document.getElementById("job-status");
const photoPreview = document.getElementById("photo-preview");
const photoPreviewImg = document.getElementById("photo-preview-img");
const resetButton = document.getElementById("reset-button");
const presetButtons = document.querySelectorAll(".preset-button");

// Presets are checked-in files (web/presets/*.jpg, see server.py's
// PRESET_BACKDROPS) that may not have been added to the server yet - a
// missing file 404s the thumbnail <img> itself. Disable that specific
// button rather than leaving a broken-image icon someone could still
// click (server.py would reject it anyway, but this fails visibly and
// earlier, right where you can see the thumbnail didn't load).
presetButtons.forEach((button) => {
  const img = button.querySelector("img");
  img.addEventListener("error", () => { button.disabled = true; });
});

// Bigger than contribute.js's MAX_DIMENSION (800) - that page only needs
// enough pixels to extract a colour palette from, this one is the actual
// image a projector displays full-screen.
const MAX_DIMENSION = 1600;

let photoDataUrl = null;

photoInput.addEventListener("change", () => {
  const file = photoInput.files[0];
  if (!file) {
    photoDataUrl = null;
    photoPreview.style.display = "none";
    return;
  }
  const img = new Image();
  img.onload = () => {
    const scale = Math.min(1, MAX_DIMENSION / Math.max(img.width, img.height));
    const w = Math.round(img.width * scale);
    const h = Math.round(img.height * scale);
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    canvas.getContext("2d").drawImage(img, 0, 0, w, h);
    photoDataUrl = canvas.toDataURL("image/jpeg", 0.85);
    photoPreviewImg.src = photoDataUrl;
    photoPreview.style.display = "block";
    URL.revokeObjectURL(img.src);
  };
  img.src = URL.createObjectURL(file);
});

let lastKnownVersion = 0;
// All set together while any one action (upload/preset/reset) is in
// flight from this tab. Upload/preset both increment the version by
// exactly 1 (see server.py) - awaitingVersion is that target, satisfied
// by version reaching *at least* that (>=, not ===: a second person's
// upload landing in between could push the version past what this tab
// specifically expected, and this tab's own request still succeeded
// either way). Reset is different: it always sets version to exactly 0,
// the one value ">= target" can't express (0 >= 0 is trivially true
// before the reset has even happened) - awaitingReset checks for that
// action separately instead. busyButton is whichever button triggered
// the in-flight action, so it (and only it) gets re-enabled once the
// ack/version lands - the other two stay clickable throughout, since they
// don't conflict with an in-flight request the way clicking the SAME
// action twice would.
let awaitingVersion = null;
let awaitingReset = false;
let busyButton = null;
let busyLabel = "";

function finishBusy(success, message) {
  if (busyButton) busyButton.disabled = false;
  busyButton = null;
  awaitingVersion = null;
  awaitingReset = false;
  jobStatusEl.className = success ? "success" : "error";
  jobStatusEl.textContent = message;
}

function startAction(button, label, action, payload) {
  busyButton = button;
  busyLabel = label;
  button.disabled = true;
  jobStatusEl.className = "";
  jobStatusEl.textContent = `${label}…`;
  if (action === "reset_shadow_backdrop") {
    awaitingReset = true;
  } else {
    awaitingVersion = lastKnownVersion + 1;
  }
  wsHandle.send({ control: { action, ...payload } });
}

const wsHandle = window.connectWS((data) => {
  // Rejections (bad/oversized/corrupted image, unknown/missing preset)
  // never bump shadow_backdrop's version, so without this the button-
  // disabled "…ing" state used to just hang forever with no explanation -
  // see server.py's set_shadow_backdrop/set_shadow_backdrop_preset/
  // reset_shadow_backdrop.
  const ack = data.control_ack;
  const knownActions = ["set_shadow_backdrop", "set_shadow_backdrop_preset", "reset_shadow_backdrop"];
  if (ack && knownActions.includes(ack.action) && !ack.ok) {
    finishBusy(false, ack.error || `${busyLabel || "Action"} failed.`);
    return;
  }

  const backdrop = data.shadow_backdrop;
  if (!backdrop) return;
  lastKnownVersion = backdrop.version;
  if (awaitingReset && backdrop.version === 0) {
    finishBusy(true, "Backdrop reset to default.");
  } else if (awaitingVersion !== null && backdrop.version >= awaitingVersion) {
    finishBusy(true, "Backdrop updated - check the projector display.");
  }
}, status);

uploadButton.addEventListener("click", () => {
  if (!photoDataUrl) {
    jobStatusEl.className = "error";
    jobStatusEl.textContent = "Choose a photo first.";
    return;
  }
  startAction(uploadButton, "Uploading", "set_shadow_backdrop", { image_data_url: photoDataUrl });
});

presetButtons.forEach((button) => {
  button.addEventListener("click", () => {
    startAction(button, "Applying preset", "set_shadow_backdrop_preset", { preset: button.dataset.preset });
  });
});

resetButton.addEventListener("click", () => {
  startAction(resetButton, "Resetting", "reset_shadow_backdrop", {});
});
