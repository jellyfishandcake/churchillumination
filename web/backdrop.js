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
let awaitingVersion = null; // set while this tab's own upload is in flight

const wsHandle = window.connectWS((data) => {
  // Rejections (bad/oversized/corrupted image) never bump shadow_backdrop's
  // version, so without this the button-disabled "Uploading…" state used to
  // just hang forever with no explanation - see server.py's set_shadow_backdrop.
  const ack = data.control_ack;
  if (ack && ack.action === "set_shadow_backdrop" && !ack.ok) {
    awaitingVersion = null;
    uploadButton.disabled = false;
    jobStatusEl.className = "error";
    jobStatusEl.textContent = ack.error || "Upload failed.";
    return;
  }

  const backdrop = data.shadow_backdrop;
  if (!backdrop) return;
  lastKnownVersion = backdrop.version;
  if (awaitingVersion !== null && backdrop.version >= awaitingVersion) {
    awaitingVersion = null;
    uploadButton.disabled = false;
    jobStatusEl.className = "success";
    jobStatusEl.textContent = "Backdrop updated - check the projector display.";
  }
}, status);

uploadButton.addEventListener("click", () => {
  if (!photoDataUrl) {
    jobStatusEl.className = "error";
    jobStatusEl.textContent = "Choose a photo first.";
    return;
  }

  uploadButton.disabled = true;
  jobStatusEl.className = "";
  jobStatusEl.textContent = "Uploading…";
  awaitingVersion = lastKnownVersion + 1;

  wsHandle.send({
    control: {
      action: "set_shadow_backdrop",
      image_data_url: photoDataUrl,
    },
  });
});
