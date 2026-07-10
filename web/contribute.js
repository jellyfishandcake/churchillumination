// contribute.js — the public "build a palette from a photo" page. Reads a
// photo, downscales it client-side (no reason to send a multi-MB original
// - src/intelligence/palette_jobs.py thumbnails to 300x300 anyway), and
// sends it as a `{"control": {"action": "build_palette", ...}}` message
// over the same websocket channel every other page uses. No login needed
// - this is a public action, same trust tier as the dashboard's
// effect/palette picker.

const status = document.getElementById("status");
const photoInput = document.getElementById("photo-input");
const nameInput = document.getElementById("name-input");
const aiToggle = document.getElementById("ai-toggle");
const ncolorsField = document.getElementById("ncolors-field");
const ncolorsInput = document.getElementById("ncolors-input");
const photocolorsField = document.getElementById("photocolors-field");
const photocolorsInput = document.getElementById("photocolors-input");
const buildButton = document.getElementById("build-button");
const jobStatusEl = document.getElementById("job-status");
const swatchPreview = document.getElementById("swatch-preview");

const MAX_DIMENSION = 800;

let photoDataUrl = null;

photoInput.addEventListener("change", () => {
  const file = photoInput.files[0];
  if (!file) {
    photoDataUrl = null;
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
    URL.revokeObjectURL(img.src);
  };
  img.src = URL.createObjectURL(file);
});

aiToggle.addEventListener("change", () => {
  ncolorsField.style.display = aiToggle.checked ? "none" : "flex";
  photocolorsField.style.display = aiToggle.checked ? "flex" : "none";
});

function showSwatches(hexColors) {
  swatchPreview.innerHTML = "";
  for (const hex of hexColors || []) {
    const div = document.createElement("div");
    div.style.background = hex;
    swatchPreview.appendChild(div);
  }
}

const wsHandle = window.connectWS((data) => {
  const job = data.palette_job;
  if (!job) return;

  if (job.status === "queued" || job.status === "processing") {
    buildButton.disabled = true;
    jobStatusEl.className = "";
    jobStatusEl.textContent = "Building… (can take up to ~10s with AI)";
  } else if (job.status === "done") {
    buildButton.disabled = false;
    jobStatusEl.className = "success";
    jobStatusEl.textContent = `Saved palette "${job.name}"` + (job.overwritten ? " (replaced an existing palette)" : "");
    showSwatches(job.hex_colors);
  } else if (job.status === "error") {
    buildButton.disabled = false;
    jobStatusEl.className = "error";
    jobStatusEl.textContent = `Failed: ${job.error}`;
  }
  // "idle" - nothing to react to, leave whatever's currently shown
}, status);

buildButton.addEventListener("click", () => {
  const name = nameInput.value.trim();
  if (!name) {
    jobStatusEl.className = "error";
    jobStatusEl.textContent = "Give your palette a name first.";
    return;
  }
  if (!photoDataUrl) {
    jobStatusEl.className = "error";
    jobStatusEl.textContent = "Choose a photo first.";
    return;
  }

  buildButton.disabled = true;
  jobStatusEl.className = "";
  jobStatusEl.textContent = "Building…";
  swatchPreview.innerHTML = "";

  wsHandle.send({
    control: {
      action: "build_palette",
      name,
      image_data_url: photoDataUrl,
      use_ai: aiToggle.checked,
      n_colors: Number(ncolorsInput.value),
      photo_colors: Number(photocolorsInput.value),
    },
  });
});
