# Project: Churchill College Creative Computing Installation

## What this is

An 8-week Summer Bursary project (29 June – 23 Aug 2026) at Churchill College,
Cambridge, building an AI/ML + LED-light interaction installation using
Raspberry Pi. 

Goal: an interdisciplinary "human-computer-environment interaction" (HCE)
platform — environmental sensors feed an ML/AI layer, which drives a
generative light installation displayed in a college space. It should also
leave behind a reusable curriculum for a Lent Term creative-computing
bootcamp.

## Task breakdown (from the proposal)

1. Physical installation: Environmental data sensing → smart algorithmic mapping (default mode) →  visual output
2. Local Interaction Platform / Terminal: 
Workstation monitor allowing users to control and display key parameters + interaction installation 
3. Pi being a server itself - server for user input: Sketches and sensor integration code created by users to be uploaded to server and obtained for display


## Timeline

| Phase | Weeks | Deliverables |
|---|---|---|
| I: Exploration | Week 1 | Site selection, concept drawings, sensor calibration |
| II: Development | Weeks 2–5 | ML/AI codebase prototyping, Pi–LED integration, interfacing/performance guidelines |
| III: Deployment | Weeks 6–8 | Final install in selected site, replication/reproduction guidelines for the Lent Term bootcamp |

## Budget context

- Raspberry Pi Starter kit (16GB) w/ screen — £500
- LED lights kit w/ power supply — £500
- LED housing kits/boxes — £500
- (Hardware funded separately via a college interdisciplinary project fund;
  this bursary funds the student's time/accommodation.)

## Architectural reference: MIT Illuminations

We're using MIT's Illuminations project (github.com/sosolimited/MIT-Illuminations,
Electron/Vue app, dormant repo) as a design reference — NOT a dependency.
Full file-by-file review lives at `docs/mit-illuminations-review.md`. Read it
before writing sensor, driver, or sketch-runtime code.

**Key decisions already made based on that review:**

- **Runtime:** Raspberry Pi, headless. No Electron.
- **LED driver:** `rpi_ws281x` (Python) driving NeoPixels directly via GPIO —
  no Arduino/serial middleman, no KiNET.
- **Sketch language:** keep p5.js — it's the pedagogical core for the
  bootcamp. Use **p5 instance mode**, not MIT's global-namespace `window.eval`
  approach.
- **Sketch sandboxing:** run sketches in a sandboxed `<iframe sandbox="allow-scripts">`,
  not `window.eval`. This matters once students are authoring code.
- **Output model:** replace MIT's "sample one row of a 1200×100 canvas" with a
  proper **pixel map** — an array of `(x, y)` per LED — so the installation
  isn't limited to a horizontal strip shape.
- **Sensor abstraction layer:** net-new. MIT has no analogue (its only
  "input" is curated media + in-browser mic). Needs a Pi-side service that
  reads mic / PIR motion / temp-humidity / etc., normalises, and publishes
  values.
- **`helpers.*` API surface for sketches:** expand MIT's tiny
  `{ canvas, lights }` config into something like:
  ```js
  {
    canvas: { width, height },
    lights: { count, layout: 'strip'|'grid'|'ring'|'map' },
    sensors: { noise: {level, spectrum}, motion: {...}, weather: {...} },
    ml: { mood: 0..1, activity: 'quiet'|'lively'|... }
  }
  ```
- **Reliability:** steal MIT's watchdog/heartbeat + "quarantine broken shows"
  pattern (their `background.js`), reimplemented via systemd `Restart=always`
  on the Pi rather than Electron IPC.
- **Reusable content:** MIT's seven starter p5 sketches (`starterPack/index.js`)
  and the `Equalizer`/`Vocalizer` FFT sketches (`shows.js`) are good bootcamp
  starting material — see the review doc's ranked reuse table before reusing
  any sketch code.

## Attribution requirement

If any MIT Illuminations code is reused (sketch source, the `Control.vue`
widget schema, the Arduino firmware, etc.), **preserve their MIT license
header and credit "Sosolimited in collaboration with MIT"**. This applies to
anything that ends up in bursary deliverables shown to admissions/college.

## Open questions to resolve early (Phase I)

- Where exactly does ML sit: sensor-side (Pi runs a small classifier on
  audio+motion → "busy/quiet/festive") or renderer-side? Sensor-side is more
  realistic for the timeline.
- What does "AI-driven algorithms to improve sensor data" concretely mean?
  Pick one and defend it: denoising a mic signal into a stable activity
  level, audio scene classification, or generative visuals from sensor
  state. Don't leave this vague past Week 3.
- Confirm installation site and get a pixel map (LED count + physical
  layout) before writing renderer code.

## Coding conventions

_(fill in once the repo structure exists — language choices, linting, test
runner, commit message style, branch naming)_
