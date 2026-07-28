// Handheld M5Stack accelerometer stick - reads the board's built-in IMU and
// prints one normalised acceleration reading per line over USB-serial, for
// src/sensing/accel_stick.py on the Pi side to read.
//
// Uses M5Unified (https://github.com/m5stack/M5Unified) rather than a
// chip-specific IMU library (e.g. MPU6886) - M5Unified auto-detects which
// IMU chip is actually on the board (MPU6886, BMI270, SH200Q, ...) via
// M5.Imu.getType(), so this sketch works unmodified across M5StickC,
// M5StickC Plus2, M5Stack CoreS3, etc. without committing to one specific
// model.
//
// Wire protocol (see accel_stick.py's docstring for the Pi-side contract):
//   {"acceleration": 0.3, "battery_pct": 87}\n
// One JSON object per line, "acceleration" in [0, 1] - same "deviation from
// 1g at rest" convention the Pi-side sensors use (see the old sense_hat.py).
// "battery_pct" is extra - accel_stick.py only reads "acceleration" today
// (see its json.loads(...)["acceleration"] line), so this field is ignored
// Pi-side for now, not a breaking change - just future-proofing for a
// low-battery warning on the admin dashboard later.
//
// Setup:
//   1. Arduino IDE > Boards Manager > install "M5Stack" (adds ESP32S3 board
//      defs for M5StickC/CoreS3/etc).
//   2. Library Manager > install "M5Unified".
//   3. Select your board + port, upload.
//   4. Point src/sensing/accel_stick.py's serial_port config at this
//      board's port (e.g. /dev/ttyUSB0 on the Pi, COMx on Windows).
//
// Three independent features live below, each in its own section so any one
// of them can be pulled out or disabled without touching the others:
//   1. Core acceleration read + send (the only thing accel_stick.py needs)
//   2. Battery level reporting
//   3. On-device screen feedback (a live bar for whoever's holding it)
//   4. Idle power save (screen sleep after a stretch of no motion)

#include <M5Unified.h>
#include <math.h>

// ---------------------------------------------------------------------------
// 1. Core acceleration read + send
// ---------------------------------------------------------------------------

// One axis reads ~1g from gravity alone at rest - deviation from that
// baseline is what counts as "movement". Same convention as SenseHatSensor
// used to compute acceleration on the Pi side.
static constexpr float BASELINE_G = 1.0f;

// Multiplies the raw g-deviation before clamping to [0, 1]. Was 1.0 (an
// untuned placeholder) - lowered after real-hardware feedback: at 1.0, a
// full [0,1] range needs a full 1g deviation from baseline (magnitude
// reaching 2g or 0g), which an ordinary hand-shake blows past almost
// instantly (real shakes routinely spike to 2-3g+ momentarily) - so both
// the screen bar (section 3) and the acceleration value itself pinned to
// max for nearly any real shake instead of scaling smoothly. Lower value
// = harder shake needed to reach 1.0, same [0, 1] contract either way for
// accel_stick.py and main.py's MOTION_BURST_THRESHOLD - still a
// placeholder in the sense that the exact number is feel, not physics,
// just a much closer starting point than 1.0 was.
static constexpr float SENSITIVITY = 0.35f;

// ~20Hz while actively held, matching the Pi's own sensor_loop rate. Slows
// down automatically while idle - see section 4.
static constexpr unsigned long ACTIVE_SEND_INTERVAL_MS = 50;

// Reads the IMU and returns the current shake magnitude, already clamped to
// [0, 1]. Pure function of the sensor - no side effects, so sections 2-4
// can all call it without needing to coordinate with each other.
float readAcceleration() {
  float ax, ay, az;
  M5.Imu.getAccel(&ax, &ay, &az);  // values in g; self-refreshes internally

  float magnitude = sqrtf(ax * ax + ay * ay + az * az);
  float acceleration = fabsf(magnitude - BASELINE_G) * SENSITIVITY;
  if (acceleration > 1.0f) acceleration = 1.0f;
  if (acceleration < 0.0f) acceleration = 0.0f;
  return acceleration;
}

void sendReading(float acceleration, int battery_pct) {
  if (battery_pct >= 0) {
    Serial.printf("{\"acceleration\": %.3f, \"battery_pct\": %d}\n", acceleration, battery_pct);
  } else {
    // Unknown/unavailable battery reading (e.g. running off USB with no
    // cell fitted) - omit the field entirely rather than send a fake -1,
    // same "only include a key when it's actually valid" pattern
    // heart_rate.py's spo2 field uses on the Pi side.
    Serial.printf("{\"acceleration\": %.3f}\n", acceleration);
  }
}

// ---------------------------------------------------------------------------
// 2. Battery level reporting
// ---------------------------------------------------------------------------
// Polled far less often than acceleration - the battery percentage doesn't
// change meaningfully at 20Hz, and M5.Power's fuel-gauge read is a slower
// I2C round-trip than the IMU's, no reason to pay that cost every tick.

static constexpr unsigned long BATTERY_POLL_INTERVAL_MS = 5000;
static int cached_battery_pct = -1;
static unsigned long last_battery_poll = 0;

void updateBatteryReading(unsigned long now) {
  if (now - last_battery_poll < BATTERY_POLL_INTERVAL_MS) return;
  last_battery_poll = now;

  int32_t level = M5.Power.getBatteryLevel();  // 0-100, or a negative sentinel if unknown
  cached_battery_pct = (level >= 0 && level <= 100) ? (int)level : -1;
}

// ---------------------------------------------------------------------------
// 3. On-device screen feedback
// ---------------------------------------------------------------------------
// A simple bottom-up bar - taller and brighter the harder the stick is
// shaken - so whoever's holding it gets immediate feedback without having
// to look across the room at the LED strip. Skipped entirely while the
// display is asleep (section 4) - drawing to a sleeping panel wastes power
// for nothing visible.

void updateScreen(float acceleration, bool display_asleep) {
  if (display_asleep) return;

  auto &dsp = M5.Display;
  int32_t w = dsp.width();
  int32_t h = dsp.height();
  int32_t bar_h = (int32_t)(acceleration * h);

  dsp.startWrite();
  dsp.fillRect(0, 0, w, h - bar_h, TFT_BLACK);   // empty space above the bar
  dsp.fillRect(0, h - bar_h, w, bar_h, TFT_CYAN); // the bar itself
  dsp.endWrite();
  // Brute-force full-height redraw every call - fine at this size/rate. If
  // it ever looks flickery, the fix is drawing into an off-screen sprite
  // and pushing that in one go, not optimising this function directly.
}

// ---------------------------------------------------------------------------
// 4. Idle power save
// ---------------------------------------------------------------------------
// NOT true deep-sleep-until-shaken - that would need the IMU's motion-
// interrupt pin wired to an ESP32 wake pin, which needs this exact board's
// schematic to confirm safely (not verified here, no hardware in hand to
// test against). What this DOES do, safely and verifiably via M5Unified's
// own API: after a stretch of no meaningful motion, turn the screen (the
// single biggest power draw on this board while idle) off, and slow the
// send/sample rate down. Both reverse instantly on the next real shake.

static constexpr float IDLE_THRESHOLD = 0.05f;          // below this counts as "not being handled"
static constexpr unsigned long IDLE_TIMEOUT_MS = 30000; // how long idle before the screen sleeps
static constexpr unsigned long IDLE_SEND_INTERVAL_MS = 500; // slower send/sample rate once idle

static bool display_asleep = false;
static unsigned long last_active_at = 0;

// Returns the send interval that should be used right now, and updates
// display sleep/wake state as a side effect - kept together since both are
// driven by the same "how long has it been idle" clock.
unsigned long updateIdlePowerSave(float acceleration, unsigned long now) {
  if (acceleration > IDLE_THRESHOLD) {
    last_active_at = now;
    if (display_asleep) {
      M5.Display.wakeup();
      display_asleep = false;
    }
    return ACTIVE_SEND_INTERVAL_MS;
  }

  if (!display_asleep && (now - last_active_at > IDLE_TIMEOUT_MS)) {
    M5.Display.sleep();  // panel + backlight off
    display_asleep = true;
  }
  return display_asleep ? IDLE_SEND_INTERVAL_MS : ACTIVE_SEND_INTERVAL_MS;
}

// ---------------------------------------------------------------------------
// setup / loop
// ---------------------------------------------------------------------------

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);
  Serial.begin(115200);

  if (M5.Imu.getType() == m5::imu_none) {
    // No IMU detected on this board - nothing sensible to report. Loop
    // forever rather than print garbage; accel_stick.py falls back to its
    // own mock if the Pi never sees a line from this port at all.
    for (;;) {
      delay(1000);
    }
  }

  last_active_at = millis();
}

void loop() {
  static unsigned long last_send = 0;
  unsigned long now = millis();
  unsigned long send_interval = display_asleep ? IDLE_SEND_INTERVAL_MS : ACTIVE_SEND_INTERVAL_MS;

  if (now - last_send >= send_interval) {
    last_send = now;

    float acceleration = readAcceleration();
    updateBatteryReading(now);
    send_interval = updateIdlePowerSave(acceleration, now);  // may flip display_asleep for next loop
    updateScreen(acceleration, display_asleep);
    sendReading(acceleration, cached_battery_pct);
  }

  M5.update();  // keeps M5Unified's internal button/touch state fresh
}
