// admin.js — the passcode-gated terminal: per-zone effect/palette
// controls, sensor toggles, activation/smoothing tuning, and a manual
// state override. Every control just sends a `{"control": {...}}` message
// (src/net/server.py's _handle_control validates and applies it) and
// reflects back whatever the server broadcasts, so multiple admin devices
// stay in sync. The zone controls used to be public on index.html, but
// moved here once the strip became multiple zones - see server.py's
// ADMIN_ACTIONS.

const { paintSwatchStrip, resolveSources, formatSources, buildSelectOptions } = window.zoneUtils;

const status = document.getElementById("status");
const loginPanel = document.getElementById("login-panel");
const controls = document.getElementById("controls");
const passcodeInput = document.getElementById("passcode-input");
const loginButton = document.getElementById("login-button");
const loginError = document.getElementById("login-error");
const zonesGrid = document.getElementById("zones-grid");
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
let tabsInitialized = false;

// One entry per zone, keyed by zone name: { zone, effectSelect,
// paletteSelect, canvas, ctx, sourceReadout }. Built once when
// leds.zones/effects/palettes all first arrive. dmxZoneCards is the same
// shape for `dmx` zones, kept in a separate object rather than merged into
// zoneCards - the two are painted differently (zoneCards' canvases slice
// led_frame by running offset; dmx zones have no slot in led_frame, they're
// painted straight from data.dmx_frame[name] - see the websocket handler).
const zoneCards = {};
const dmxZoneCards = {};

// Shared by both zone kinds - a heading, Pattern/Palette selects (wired to
// the same zone-name-generic set_zone_effect control action either way -
// see server.py's ADMIN_ACTIONS), a swatch canvas, and a source readout.
// Appends to zonesGrid and returns the card record; painting/labelling the
// canvas is the caller's job since led vs dmx zones size/paint it differently.
function createZoneCard(zone, effects, palettes) {
  const card = document.createElement("div");
  card.className = "zone-card";

  const heading = document.createElement("h3");
  heading.textContent = `${zone.name} (${zone.pixels}px)`;
  card.appendChild(heading);

  const effectField = document.createElement("div");
  effectField.className = "field";
  const effectLabel = document.createElement("span");
  effectLabel.className = "field-label";
  effectLabel.textContent = "Pattern";
  const effectSelect = document.createElement("select");
  buildSelectOptions(effectSelect, effects);
  effectField.append(effectLabel, effectSelect);
  card.appendChild(effectField);

  const paletteField = document.createElement("div");
  paletteField.className = "field";
  const paletteLabel = document.createElement("span");
  paletteLabel.className = "field-label";
  paletteLabel.textContent = "Palette";
  const paletteSelect = document.createElement("select");
  buildSelectOptions(paletteSelect, palettes);
  paletteField.append(paletteLabel, paletteSelect);
  card.appendChild(paletteField);

  const canvas = document.createElement("canvas");
  canvas.className = "swatch-strip";
  canvas.width = zone.pixels;
  canvas.height = 1;
  card.appendChild(canvas);

  const sourceReadout = document.createElement("div");
  sourceReadout.className = "source-readout";
  card.appendChild(sourceReadout);

  zonesGrid.appendChild(card);

  const sendChoice = () => {
    wsHandle.send({
      control: {
        action: "set_zone_effect",
        zone: zone.name,
        effect: effectSelect.value,
        palette: paletteSelect.value,
      },
    });
  };
  effectSelect.addEventListener("change", sendChoice);
  paletteSelect.addEventListener("change", sendChoice);

  return { zone, effectSelect, paletteSelect, canvas, ctx: canvas.getContext("2d"), sourceReadout };
}

function buildZoneCards(zones, effects, palettes) {
  for (const zone of zones) {
    zoneCards[zone.name] = createZoneCard(zone, effects, palettes);
  }
}

function buildDmxZoneCards(zones, effects, palettes) {
  for (const zone of zones) {
    dmxZoneCards[zone.name] = createZoneCard(zone, effects, palettes);
  }
}

function setLoggedIn(value) {
  loggedIn = value;
  loginPanel.style.display = value ? "none" : "block";
  controls.style.display = value ? "block" : "none";
  // Only wire up tab-click listeners once - initTabs() isn't idempotent
  // (repeated calls would stack duplicate click handlers), and login can
  // happen more than once per page load if the connection drops/reconnects.
  if (value && !tabsInitialized) {
    initTabs(controls);
    tabsInitialized = true;
  }
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

    const { state, sensor_health, runtime_settings, leds, effects, palettes, led_frame } = data;
    if (!runtime_settings) return;

    if (!sensorTogglesBuilt) {
      buildSensorToggles(runtime_settings.sensors_enabled, sensor_health);
    } else {
      syncSensorToggles(runtime_settings.sensors_enabled);
    }

    if (Object.keys(zoneCards).length === 0 && leds?.zones?.length && effects && palettes) {
      buildZoneCards(leds.zones, effects, palettes);
    }
    if (Object.keys(dmxZoneCards).length === 0 && leds?.dmx_zones?.length && effects && palettes) {
      buildDmxZoneCards(leds.dmx_zones, effects, palettes);
    }

    // Palettes, unlike effects, can appear at any time via contribute.html -
    // diff-append any name not already an <option> to every zone's palette
    // picker (led and dmx alike), same reasoning as app.js's read-only dashboard.
    if (palettes) {
      for (const cards of [zoneCards, dmxZoneCards]) {
        for (const name in cards) {
          const { paletteSelect } = cards[name];
          const existing = new Set(Array.from(paletteSelect.options).map((o) => o.value));
          const missing = palettes.filter((p) => !existing.has(p));
          if (missing.length) buildSelectOptions(paletteSelect, missing);
        }
      }
    }

    for (const cards of [zoneCards, dmxZoneCards]) {
      for (const name in cards) {
        const { zone, effectSelect, paletteSelect, sourceReadout } = cards[name];
        const zoneSettings = runtime_settings.zones?.[name];
        if (zoneSettings) {
          if (document.activeElement !== effectSelect) effectSelect.value = zoneSettings.effect;
          if (document.activeElement !== paletteSelect) paletteSelect.value = zoneSettings.palette;
        }
        sourceReadout.textContent = formatSources(resolveSources(data, zone.source));
      }
    }

    if (led_frame) {
      let offset = 0;
      for (const name in zoneCards) {
        const { zone, ctx, canvas } = zoneCards[name];
        paintSwatchStrip(ctx, canvas, led_frame.slice(offset, offset + zone.pixels));
        offset += zone.pixels;
      }
    }

    // dmx zones have no slot in led_frame (one shared strip vs N independent
    // fixture segments) - painted straight from their own published frame.
    if (data.dmx_frame) {
      for (const name in dmxZoneCards) {
        const { ctx, canvas } = dmxZoneCards[name];
        if (data.dmx_frame[name]) paintSwatchStrip(ctx, canvas, data.dmx_frame[name]);
      }
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
