// version on 2026-08-03 update
// Seeed XIAO ESP32S3 Sense presence/motion/loudness node - one of 2 boards
// distributed around the installation space, publishing over WiFi/MQTT to
// the Pi (see src/sensing/nodes.py for the Pi-side contract this firmware
// must match exactly).
//
// MQTT contract:
//   topic:   esp32/<NODE_ID>/sense
//   payload: {"loudness": 0.4, "motion": 0.1, "presence": 0.0}\n  (JSON)
//
// Camera note (decision 2026-07-28 - supersedes the earlier OV2640-now/
// thermal-later plan): this board no longer uses its bundled RGB OV2640 at
// all - swapped for a Grove AMG8833 thermal camera (Panasonic Grid-EYE,
// 8x8 = 64 pixels), matching the central Pi sensor's (motion.py) approach
// of an anonymised heat-blob frame-diff rather than a recognisable image -
// see CLAUDE.md / node-camera-privacy-decision. Lower resolution than the
// central MLX90640 (32x24 = 768 pixels), but the same underlying idea, and
// far cheaper/easier to wire (Grove's standard 4-pin connector).
//
// PIR (presence) decision 2026-07-28: no PIR on the node boards at all
// (not just "not yet wired" - a deliberate permanent choice). Section 4
// below derives presence from the same thermal motion signal instead of a
// separate sensor - see that section's own comment for why that's a
// reasonable substitute, not just a stand-in for a missing part.
//
// Setup:
//   1. Arduino IDE > Boards Manager > install "esp32" (Espressif's package -
//      provides the XIAO_ESP32S3 board entry).
//   2. Library Manager > install "PubSubClient" (by Nick O'Leary) AND
//      "Grove IR Matrix Temperature sensor AMG8833" (by Seeed Studio).
//      ESP_I2S ships with the esp32 board package - no separate install
//      needed for that one.
//   3. Tools > Board > XIAO_ESP32S3, matching port.
//   4. Fill in WIFI_SSID / WIFI_PASSWORD / MQTT_BROKER_HOST / NODE_ID below.
//   5. Upload. Flashing/download-mode details are the same as the M5Stick
//      (see firmware/accel_stick/accel_stick.ino's setup notes) - hold the
//      board's BOOT button while plugging in USB if it doesn't auto-enter
//      download mode.
//   6. For the second node, change NODE_ID to "node2" and re-upload - same
//      firmware file for both boards.

#include <WiFi.h>
#include <PubSubClient.h>
#include <ESP_I2S.h>
#include "Seeed_AMG8833_driver.h"

// ---------------------------------------------------------------------------
// 0. Fill in before flashing
// ---------------------------------------------------------------------------

static const char *WIFI_SSID = "churchillumination-nodes";
static const char *WIFI_PASSWORD = "iamanode123";

// The Pi's LAN IP (not "localhost" - this board isn't the Pi). Find it on
// the Pi with `hostname -I`. Must match the mosquitto listener you set up
// (listener 1883 0.0.0.0 in /etc/mosquitto/conf.d/churchillumination.conf).
static const char *MQTT_BROKER_HOST = "10.42.0.1";
static const uint16_t MQTT_BROKER_PORT = 1883;

// Must be one of config.yaml's sensors.nodes.node_ids ("node1"/"node2" by
// default - see config.py's DEFAULTS). Change and re-flash for the 2nd board.
static const char *NODE_ID = "node1";

static const char *TOPIC_PREFIX = "esp32";
static const unsigned long PUBLISH_INTERVAL_MS = 200;  // 5Hz - plenty for ambient sensing, keeps WiFi/MQTT traffic light

WiFiClient wifi_client;
PubSubClient mqtt_client(wifi_client);

// ---------------------------------------------------------------------------
// 1. WiFi + MQTT connection management
// ---------------------------------------------------------------------------
// Both reconnect on their own each loop if dropped - a node sitting in the
// installation space for weeks needs to recover from a WiFi blip or a
// mosquitto restart on the Pi without needing a manual power-cycle.

void ensureWifiConnected() {
  if (WiFi.status() == WL_CONNECTED) return;

  Serial.print("[wifi] connecting...");
  // Force the WiFi stack to fully drop any in-progress/stuck state before
  // reconfiguring - without this, a previous attempt that didn't cleanly
  // resolve can still make the following WiFi.begin() fail with "sta is
  // connecting, return error"/"cannot set config", even with
  // waitForConnectResult() below. This is a known, still-not-fully-fixed
  // race in recent Arduino-ESP32 core versions (see e.g.
  // espressif/arduino-esp32#7095) - disconnect() first is the documented
  // workaround, but a plain disconnect() alone wasn't enough in practice
  // (2026-08-03 real-hardware log: every retry hit the exact same instant
  // "return error" - the driver never actually finished tearing down
  // between attempts). disconnect(true) also powers the radio off instead
  // of just de-associating - a fuller reset - and the delay below gives
  // that teardown time to actually finish (it's async under the hood)
  // before the next begin() call can race it again.
  WiFi.disconnect(true);
  delay(200);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  // waitForConnectResult() blocks until THIS attempt fully resolves (success
  // or failure) before returning, instead of a manual delay()-loop that can
  // give up and let the next loop() call WiFi.begin() again while the
  // previous attempt is still resolving in the background.
  auto result = WiFi.waitForConnectResult(15000);
  if (result == WL_CONNECTED) {
    // Confirms DHCP actually handed out a real address on the hotspot's
    // 10.42.0.0/24 subnet - "WiFi connected" alone doesn't guarantee that;
    // an unexpected 169.254.x.x here would mean association succeeded but
    // DHCP itself failed, which explains an otherwise-mysterious inability
    // to reach MQTT_BROKER_HOST afterwards.
    Serial.print(" connected, IP=");
    Serial.println(WiFi.localIP());
    return;
  }
  // WiFi.status() codes: 1=WL_NO_SSID_AVAIL (SSID not seen - wrong name,
  // wrong band, or out of range), 4=WL_CONNECT_FAILED (usually a wrong
  // password), 6=WL_DISCONNECTED. Printing the raw code rather than
  // guessing which failure this is - "failed, will retry" alone doesn't
  // distinguish a bad password from the known arduino-esp32 connect race
  // from the AP just not being visible, and those need different fixes.
  Serial.printf(" failed (status=%d), will retry next loop\n", (int)result);
}

void ensureMqttConnected() {
  if (WiFi.status() != WL_CONNECTED || mqtt_client.connected()) return;

  String client_id = String("xiao-") + NODE_ID;
  Serial.print("[mqtt] connecting...");
  if (mqtt_client.connect(client_id.c_str())) {
    Serial.println(" connected");
  } else {
    Serial.printf(" failed, rc=%d, will retry next loop\n", mqtt_client.state());
  }
}

// ---------------------------------------------------------------------------
// 2. Microphone (loudness)
// ---------------------------------------------------------------------------
// Onboard PDM mic, pins per Seeed's own XIAO ESP32S3 Sense docs. Tracks peak
// amplitude over a short window each call - "how loud right now", the same
// intent as the central AudioSensor's level metric on the Pi side.

static const int8_t MIC_CLK_PIN = 42;
static const int8_t MIC_DATA_PIN = 41;
static const uint32_t MIC_SAMPLE_RATE = 16000;
static const unsigned long MIC_WINDOW_MS = 20;

I2SClass i2s_mic;

void setupMic() {
  i2s_mic.setPinsPdmRx(MIC_CLK_PIN, MIC_DATA_PIN);
  if (!i2s_mic.begin(I2S_MODE_PDM_RX, MIC_SAMPLE_RATE, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO)) {
    Serial.println("[mic] init failed - loudness will read 0.0");
  }
}

float readLoudness() {
  int32_t peak = 0;
  unsigned long window_start = millis();
  while (millis() - window_start < MIC_WINDOW_MS) {
    int sample = i2s_mic.read();
    int32_t abs_sample = abs(sample);
    if (abs_sample > peak) peak = abs_sample;
  }
  return min(1.0f, peak / 32768.0f);  // 16-bit signed PCM range
}

// ---------------------------------------------------------------------------
// 3. Thermal camera (motion, via temperature-delta frame-diff)
// ---------------------------------------------------------------------------
// Same frame-diff principle as motion.py's central MotionSensor: grab a
// frame of temperatures, compare to the previous one, mean absolute change
// (in degrees C) = how much changed - see this file's top-of-file comment
// for why this replaced the OV2640 RGB approach entirely.
//
// I2C address note: the Seeed_AMG8833 library's own AMG8833() constructor
// defaults to 0x68 (its DEFAULT_IIC_ADDR) - but the Grove AMG8833 board's
// actual out-of-box default is 0x69 (0x68 only applies if you've soldered
// the board's own "Addr" jumper, per Seeed's wiki). Passed explicitly below
// rather than trusting the library's default - a wrong hardcoded I2C
// address has bitten this project before (the MAX30102 heart-rate sensor,
// see src/sensing/heart_rate.py).

static const uint8_t THERMAL_I2C_ADDR = 0x69;
// Placeholder - tune once this board is actually observed, same as every
// other sensitivity constant in this file. Starting from motion.py's own
// MotionSensor default since both operate on the same "mean pixel-to-pixel
// change, in degrees C" scale - despite the very different pixel counts
// (64 here vs 768 centrally), the per-pixel temperature-delta magnitude a
// moving warm body produces shouldn't differ much, so it's a reasonable
// starting point rather than an arbitrary guess.
static const float MOTION_SENSITIVITY = 10.0f;

AMG8833 thermal_sensor(THERMAL_I2C_ADDR);
static float prev_frame[PIXEL_NUM] = {0};
static bool have_prev_frame = false;
static bool thermal_ok = false;

void setupThermalCamera() {
  if (thermal_sensor.init() != 0) {
    Serial.println("[thermal] init failed - motion will read 0.0");
    thermal_ok = false;
    return;
  }
  thermal_ok = true;
}

float readMotion() {
  if (!thermal_ok) return 0.0f;

  float frame[PIXEL_NUM];
  thermal_sensor.read_pixel_temperature(frame);

  float motion = 0.0f;
  if (have_prev_frame) {
    float diff_sum = 0.0f;
    for (int i = 0; i < PIXEL_NUM; i++) {
      diff_sum += fabs(frame[i] - prev_frame[i]);
    }
    float mean_diff = diff_sum / PIXEL_NUM;  // average change, in degrees C
    motion = min(1.0f, mean_diff * MOTION_SENSITIVITY);
  }

  memcpy(prev_frame, frame, sizeof(frame));
  have_prev_frame = true;
  return motion;
}

// ---------------------------------------------------------------------------
// 4. Presence - derived from thermal motion, since this board has no PIR
// ---------------------------------------------------------------------------
// Decision 2026-07-28: no PIR on the node boards at all - not a stand-in
// for a missing part, a deliberate choice. This isn't as much of a
// downgrade as it sounds: a PIR is itself fundamentally a motion/change
// detector (it doesn't see a person standing perfectly still either), so
// "thermal motion is above a floor" is the same underlying principle a PIR
// would have given, just derived from the sensor this board already has
// rather than a second dedicated part. PRESENCE_MOTION_THRESHOLD is a
// placeholder - tune once this board is actually observed.

static const float PRESENCE_MOTION_THRESHOLD = 0.15f;

float presenceFromMotion(float motion) {
  return motion > PRESENCE_MOTION_THRESHOLD ? 1.0f : 0.0f;
}

// ---------------------------------------------------------------------------
// setup / loop
// ---------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  delay(1000);  // let the Serial console catch up before the first prints

  setupMic();
  setupThermalCamera();
  mqtt_client.setServer(MQTT_BROKER_HOST, MQTT_BROKER_PORT);
}

void loop() {
  ensureWifiConnected();
  ensureMqttConnected();
  mqtt_client.loop();

  static unsigned long last_publish = 0;
  unsigned long now = millis();
  if (now - last_publish < PUBLISH_INTERVAL_MS) return;
  last_publish = now;

  float loudness = readLoudness();
  float motion = readMotion();
  float presence = presenceFromMotion(motion);

  char payload[128];
  snprintf(payload, sizeof(payload),
           "{\"loudness\": %.3f, \"motion\": %.3f, \"presence\": %.3f}",
           loudness, motion, presence);

  String topic = String(TOPIC_PREFIX) + "/" + NODE_ID + "/sense";
  if (mqtt_client.connected()) {
    mqtt_client.publish(topic.c_str(), payload);
  }
  Serial.println(payload);  // mirrored to Serial so you can debug without mosquitto_sub running
}
