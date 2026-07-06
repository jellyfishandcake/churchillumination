// admin.js — the passcode-gated terminal: sensor toggles, activation/
// smoothing tuning, and a manual state override. Every control just sends
// a `{"control": {...}}` message (src/intelligence/server.py's
// _handle_control validates and applies it) and reflects back whatever
// the server broadcasts, so multiple admin devices stay in sync.

const status = document.getElementById("status");
const loginPanel = document.getElementById("login-panel");
const controls = document.getElementById("controls");
const passcodeInput = document.getElementById("passcode-input");
const loginButton = document.getElementById("login-button");
const loginError = document.getElementById("login-error");
const sensorToggles = document.getElementById("sensor-toggles");
const activationTimeoutInput = document.getElementById("activation-timeout-input");
const smoothingAlphaInput = document.getElementById("smoothing-alpha-input");
const smoothingAlphaValue = document.getElementById("smoothing-alpha-value");
const overrideMoodSelect = document.getElementById("override-mood-select");
const overrideActivityInput = document.getElementById("override-activity-input");
const overrideActivityValue = document.getElementById("override-activity-value");
const applyOverrideButton = document.getElementById("apply-override-button");
const clearOverrideButton = document.getElementById("clear-override-button");
const statusReadout = document.getElementById("status-readout");

let loggedIn = false;
let sensorTogglesBuilt = false;

function setLoggedIn(value) {
  loggedIn = value;
  loginPanel.style.display = value ? "none" : "block";
  controls.style.display = value ? "block" : "none";
}

function buildSensorToggles(sensorsEnabled, sensorHealth) {
  sensorToggles.innerHTML = "";
  for (const name of Object.keys(sensorsEnabled)) {
    const constructed = name in sensorHealth;
    const row = document.createElement("div");
    row.className = "field";

    const label = document.createElement("span");
    label.className = "field-label";
    label.textContent = name;
    if (!constructed) {
      const note = document.createElement("small");
      note.textContent = "not running — enable in config.yaml and restart";
      label.appendChild(note);
    }

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = sensorsEnabled[name];
    checkbox.disabled = !constructed;
    checkbox.addEventListener("change", () => {
      wsHandle.send({ control: { action: "toggle_sensor", sensor: name, enabled: checkbox.checked } });
    });
    checkbox.dataset.sensorName = name;

    row.appendChild(label);
    row.appendChild(checkbox);
    sensorToggles.appendChild(row);
  }
  sensorTogglesBuilt = true;
}

function syncSensorToggles(sensorsEnabled) {
  for (const checkbox of sensorToggles.querySelectorAll("input[type=checkbox]")) {
    const name = checkbox.dataset.sensorName;
    if (document.activeElement !== checkbox) {
      checkbox.checked = sensorsEnabled[name];
    }
  }
}

const wsHandle = window.connectWS(
  (data) => {
    if (data.control_ack?.action === "admin_login") {
      if (data.control_ack.ok) {
        loginError.textContent = "";
        setLoggedIn(true);
      } else {
        loginError.textContent = "Wrong passcode.";
      }
      return;
    }

    const { state, sensor_health, runtime_settings } = data;
    if (!runtime_settings) return;

    if (!sensorTogglesBuilt) {
      buildSensorToggles(runtime_settings.sensors_enabled, sensor_health);
    } else {
      syncSensorToggles(runtime_settings.sensors_enabled);
    }

    if (document.activeElement !== activationTimeoutInput) {
      activationTimeoutInput.value = runtime_settings.activation_timeout_seconds;
    }
    if (document.activeElement !== smoothingAlphaInput) {
      smoothingAlphaInput.value = runtime_settings.smoothing_alpha;
    }
    smoothingAlphaValue.textContent = Number(runtime_settings.smoothing_alpha).toFixed(2);

    statusReadout.textContent =
      `mood: ${state.mood}\n` +
      `activity_level: ${state.activity_level.toFixed(3)}\n` +
      `presence_count: ${state.presence_count}\n` +
      `state_override: ${runtime_settings.state_override ? JSON.stringify(runtime_settings.state_override) : "none"}\n\n` +
      `sensor_health:\n` +
      Object.entries(sensor_health).map(([name, h]) => `  ${name}: ${h.healthy ? "ok" : "FAILED — " + h.last_error}`).join("\n");
  },
  status,
  () => setLoggedIn(false) // fresh connection is unauthenticated server-side too
);

loginButton.addEventListener("click", () => {
  wsHandle.send({ control: { action: "admin_login", passcode: passcodeInput.value } });
});
passcodeInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") loginButton.click();
});

activationTimeoutInput.addEventListener("change", () => {
  wsHandle.send({ control: { action: "set_activation_timeout", seconds: Number(activationTimeoutInput.value) } });
});

smoothingAlphaInput.addEventListener("input", () => {
  smoothingAlphaValue.textContent = Number(smoothingAlphaInput.value).toFixed(2);
});
smoothingAlphaInput.addEventListener("change", () => {
  wsHandle.send({ control: { action: "set_smoothing_alpha", alpha: Number(smoothingAlphaInput.value) } });
});

overrideActivityInput.addEventListener("input", () => {
  overrideActivityValue.textContent = Number(overrideActivityInput.value).toFixed(2);
});

applyOverrideButton.addEventListener("click", () => {
  wsHandle.send({
    control: {
      action: "set_state_override",
      mood: overrideMoodSelect.value,
      activity_level: Number(overrideActivityInput.value),
    },
  });
});
clearOverrideButton.addEventListener("click", () => {
  wsHandle.send({ control: { action: "clear_state_override" } });
});
