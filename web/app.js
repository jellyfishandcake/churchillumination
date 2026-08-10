// app.js — connects to the Python server and renders each zone's live LED
// output on the public dashboard. Read-only: effect/palette controls live
// in admin.html's Zones tab, not here - see server.py's ADMIN_ACTIONS.
//
// Sketch running/upload (create.html, the sandboxed iframe - see
// sketchSandbox.js/web/sketchRunner.html) is deliberately not embedded on
// this page - it doesn't drive the real LEDs yet (output_loop runs each
// zone's own configured effect, full stop), so showing it here read as
// more confusing than useful. create.html/cast.html are untouched and
// still work standalone; picking this back up is future work.

const { paintSwatchStrip, resolveSources, formatSources } = window.zoneUtils;

const status = document.getElementById("status");
const zonesGrid = document.getElementById("zones-grid");

// One entry per zone, keyed by zone name: { zone, canvas, ctx, sourceReadout }.
// Built once when leds.zones/leds.dmx_zones first arrive - zone count/pixel
// layout is fixed for the process's lifetime. Two separate maps (not one)
// because they're painted from two different broadcast fields below -
// zoneCards (output.type "led") slice led_frame by running offset,
// dmxZoneCards (output.type "dmx") paint straight from data.dmx_frame[name]
// - a dmx zone has no slot in led_frame at all, same reasoning main.py's
// own "leds"/"dmx_zones" split (and admin.js's matching dmxZoneCards) uses.
const zoneCards = {};
const dmxZoneCards = {};

function createZoneCard(zone) {
  const card = document.createElement("div");
  card.className = "zone-card";

  const heading = document.createElement("h3");
  heading.textContent = `${zone.name} (${zone.pixels}px)`;
  card.appendChild(heading);

  const canvas = document.createElement("canvas");
  canvas.className = "swatch-strip";
  canvas.width = zone.pixels;
  canvas.height = 1;
  card.appendChild(canvas);

  const sourceReadout = document.createElement("div");
  sourceReadout.className = "source-readout";
  card.appendChild(sourceReadout);

  zonesGrid.appendChild(card);
  return { zone, canvas, ctx: canvas.getContext("2d"), sourceReadout };
}

function buildZoneCards(zones, cardsMap) {
  for (const zone of zones) {
    cardsMap[zone.name] = createZoneCard(zone);
  }
}

window.connectWS((data) => {
  const { leds, led_frame } = data;

  // Built once, together - zonesGrid.innerHTML is only cleared here, so a
  // second build call (e.g. dmx_zones arriving on a later tick than zones)
  // can't wipe out cards the first call already appended.
  if (Object.keys(zoneCards).length === 0 && Object.keys(dmxZoneCards).length === 0
      && (leds?.zones?.length || leds?.dmx_zones?.length)) {
    zonesGrid.innerHTML = "";
    buildZoneCards(leds.zones || [], zoneCards);
    buildZoneCards(leds.dmx_zones || [], dmxZoneCards);
  }

  if (led_frame) {
    let offset = 0;
    for (const name in zoneCards) {
      const { zone, ctx, canvas, sourceReadout } = zoneCards[name];
      const slice = led_frame.slice(offset, offset + zone.pixels);
      paintSwatchStrip(ctx, canvas, slice);
      sourceReadout.textContent = formatSources(resolveSources(data, zone.source));
      offset += zone.pixels;
    }
  }

  if (data.dmx_frame) {
    for (const name in dmxZoneCards) {
      const { zone, ctx, canvas, sourceReadout } = dmxZoneCards[name];
      if (data.dmx_frame[name]) paintSwatchStrip(ctx, canvas, data.dmx_frame[name]);
      sourceReadout.textContent = formatSources(resolveSources(data, zone.source));
    }
  }
}, status);
