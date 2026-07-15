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
//   {"acceleration": 0.3}\n
// One JSON object per line, "acceleration" in [0, 1] - same "deviation from
// 1g at rest" convention the Pi-side sensors use (see the old sense_hat.py).
//
// Setup:
//   1. Arduino IDE > Boards Manager > install "M5Stack" (adds ESP32S3 board
//      defs for M5StickC/CoreS3/etc).
//   2. Library Manager > install "M5Unified".
//   3. Select your board + port, upload.
//   4. Point src/sensing/accel_stick.py's serial_port config at this
//      board's port (e.g. /dev/ttyUSB0 on the Pi, COMx on Windows).

#include <M5Unified.h>
#include <math.h>

// One axis reads ~1g from gravity alone at rest - deviation from that
// baseline is what counts as "movement". Same convention as SenseHatSensor
// used to compute acceleration on the Pi side.
static constexpr float BASELINE_G = 1.0f;

// Multiplies the raw g-deviation before clamping to [0, 1] - a placeholder
// until real hardware is in hand to tune against (how hard a "shake"
// actually needs to be before main.py's motion_tracker should treat it as
// a burst - see MOTION_BURST_THRESHOLD in main.py).
static constexpr float SENSITIVITY = 1.0f;

// ~20Hz, matching the Pi's own sensor_loop rate.
static constexpr unsigned long SEND_INTERVAL_MS = 50;

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
}

void loop() {
  static unsigned long last_send = 0;
  unsigned long now = millis();

  if (now - last_send >= SEND_INTERVAL_MS) {
    last_send = now;

    float ax, ay, az;
    M5.Imu.getAccel(&ax, &ay, &az);  // values in g

    float magnitude = sqrtf(ax * ax + ay * ay + az * az);
    float acceleration = fabsf(magnitude - BASELINE_G) * SENSITIVITY;
    if (acceleration > 1.0f) acceleration = 1.0f;
    if (acceleration < 0.0f) acceleration = 0.0f;

    Serial.printf("{\"acceleration\": %.3f}\n", acceleration);
  }

  M5.update();  // keeps M5Unified's internal button/touch state fresh
}
