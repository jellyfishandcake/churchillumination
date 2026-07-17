// create.js — write/paste a sketch, preview it live in the same sandbox
// index.html uses, then submit it. Submission (`{"control": {"action":
// "upload_sketch", ...}}`, see src/net/server.py) is fire-and-forget -
// there's no job object to poll (contrast contribute.js's palette_job,
// which needs one since build_palette does real async work). Success is
// confirmed here by watching the broadcast for the submitted name showing
// up in `sketches` shortly after - the same list index.html's picker reads
// from, so "it showed up here" means "it's actually selectable there" too.

const status = document.getElementById("status");
const nameInput = document.getElementById("name-input");
const fileInput = document.getElementById("file-input");
const codeInput = document.getElementById("code-input");
const previewButton = document.getElementById("preview-button");
const submitButton = document.getElementById("submit-button");
const jobStatusEl = document.getElementById("job-status");
const previewError = document.getElementById("preview-error");
const previewContainer = document.getElementById("preview-container");

// Mirrors server.py's SKETCH_NAME_RE - checked here too so a bad name is
// caught before a round trip, not just after the server rejects it.
const SKETCH_NAME_RE = /^[A-Za-z0-9_-]{1,40}$/;
const CONFIRM_TIMEOUT_MS = 3000;

const sandbox = window.createSketchSandbox(previewContainer);
let latestLeds = null;

window.wireSandboxLeds(sandbox, () => latestLeds);
sandbox.onError((message) => {
  previewError.textContent = `Sketch error: ${message}`;
});

fileInput.addEventListener("change", () => {
  const file = fileInput.files[0];
  if (!file) return;
  file.text().then((text) => { codeInput.value = text; });
});

previewButton.addEventListener("click", () => {
  previewError.textContent = "";
  sandbox.loadSketch(codeInput.value);
});

let pendingSubmitName = null;
let confirmTimeout = null;

const wsHandle = window.connectWS((data) => {
  if (data.leds) latestLeds = { num_pixels: data.leds.num_pixels, layout: data.leds.layout };
  sandbox.sendHelpers(window.helpersFromBroadcast(data));

  if (pendingSubmitName && data.sketches?.includes(pendingSubmitName)) {
    clearTimeout(confirmTimeout);
    jobStatusEl.className = "success";
    jobStatusEl.textContent = `Saved sketch "${pendingSubmitName}" — it's now selectable on the dashboard.`;
    submitButton.disabled = false;
    pendingSubmitName = null;
  }
}, status);

submitButton.addEventListener("click", () => {
  const name = nameInput.value.trim();
  const code = codeInput.value;

  if (!SKETCH_NAME_RE.test(name)) {
    jobStatusEl.className = "error";
    jobStatusEl.textContent = "Name must be 1-40 characters: letters, numbers, _ or - only.";
    return;
  }
  if (!code.trim()) {
    jobStatusEl.className = "error";
    jobStatusEl.textContent = "Write or load some sketch code first.";
    return;
  }

  submitButton.disabled = true;
  jobStatusEl.className = "";
  jobStatusEl.textContent = "Submitting…";
  pendingSubmitName = name;

  wsHandle.send({ control: { action: "upload_sketch", name, code } });

  confirmTimeout = setTimeout(() => {
    if (pendingSubmitName !== name) return; // already confirmed above
    submitButton.disabled = false;
    jobStatusEl.className = "error";
    jobStatusEl.textContent = "No confirmation from the server — check the name is valid and try again.";
    pendingSubmitName = null;
  }, CONFIRM_TIMEOUT_MS);
});
