// Seeed XIAO ESP32S3 Sense presence/motion/loudness node - one of 2 boards
// distributed around the installation space, publishing over WiFi/MQTT to
// the Pi (see src/sensing/nodes.py for the Pi-side contract this firmware
// must match exactly).
//
// MQTT contract:
//   topic:   esp32/<NODE_ID>/sense
//   payload: {"loudness": 0.4, "motion": 0.1, "presence": 0.0}\n  (JSON)
//
// Camera note (see project decision - OV2640 now, thermal later):
// This board's bundled camera is a regular RGB OV2640, NOT the MLX90640
// thermal camera the central Pi sensor (motion.py) uses. Using it for
// frame-diff motion here is a deliberate, temporary dev/testing choice -
// same "not deployment hardware" caveat motion.py's own webcam fallback
// already carries. Before this node is shown to the public, swap in an
// MLX90640 thermal breakout and rewrite section 3 below to match motion.py's
// approach (temperature-delta frame diff, not RGB-brightness frame diff).
//
// PIR (presence) is NOT wired to this board yet - section 4 is a clearly
// marked placeholder returning 0.0 until a PIR is physically attached.
//
// Setup:
//   1. Arduino IDE > Boards Manager > install "esp32" (Espressif's package -
//      provides the XIAO_ESP32S3 board entry).
//   2. Library Manager > install "PubSubClient" (by Nick O'Leary).
//      ESP_I2S and esp_camera ship with the esp32 board package - no
//      separate install needed for those two.
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
#include "esp_camera.h"

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

  Serial.print("[wifi] connecting");
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
    delay(250);
    Serial.print(".");
  }
  Serial.println(WiFi.status() == WL_CONNECTED ? " connected" : " failed, will retry next loop");
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
// 3. Camera (motion, via RGB frame-diff - see the OV2640/thermal note above)
// ---------------------------------------------------------------------------
// Same frame-diff principle as motion.py's central MotionSensor: grab a
// grayscale frame, compare to the previous one, mean absolute difference =
// how much changed. Small resolution (QQVGA) keeps this cheap enough to run
// alongside WiFi/MQTT/mic on one small MCU.

static const framesize_t CAMERA_FRAME_SIZE = FRAMESIZE_QQVGA;  // 160x120
static const float MOTION_SENSITIVITY = 8.0f;  // placeholder - tune once this board is actually observed, same as motion.py's webcam_sensitivity

static uint8_t *prev_frame = nullptr;
static size_t prev_frame_len = 0;
static bool camera_ok = false;

void setupCamera() {
  camera_config_t config = {};
  config.pin_pwdn = -1;
  config.pin_reset = -1;
  config.pin_xclk = 10;
  config.pin_siod = 40;
  config.pin_sioc = 39;
  config.pin_d7 = 48;
  config.pin_d6 = 11;
  config.pin_d5 = 12;
  config.pin_d4 = 14;
  config.pin_d3 = 16;
  config.pin_d2 = 18;
  config.pin_d1 = 17;
  config.pin_d0 = 15;
  config.pin_vsync = 38;
  config.pin_href = 47;
  config.pin_pclk = 13;
  config.xclk_freq_hz = 20000000;
  config.ledc_timer = LEDC_TIMER_0;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.pixel_format = PIXFORMAT_GRAYSCALE;  // raw brightness bytes - simplest possible frame-diff input
  config.frame_size = CAMERA_FRAME_SIZE;
  config.fb_count = 2;
  config.grab_mode = CAMERA_GRAB_LATEST;
  config.fb_location = CAMERA_FB_IN_PSRAM;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("[camera] init failed (0x%x) - motion will read 0.0\n", err);
    camera_ok = false;
    return;
  }
  camera_ok = true;
}

float readMotion() {
  if (!camera_ok) return 0.0f;

  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) return 0.0f;

  float motion = 0.0f;
  if (prev_frame != nullptr && prev_frame_len == fb->len) {
    uint32_t diff_sum = 0;
    for (size_t i = 0; i < fb->len; i++) {
      diff_sum += abs((int)fb->buf[i] - (int)prev_frame[i]);
    }
    float mean_diff = (float)diff_sum / fb->len;  // 0..255 average brightness change
    motion = min(1.0f, (mean_diff / 255.0f) * MOTION_SENSITIVITY);
  }

  if (prev_frame == nullptr || prev_frame_len != fb->len) {
    free(prev_frame);
    prev_frame = (uint8_t *)malloc(fb->len);
    prev_frame_len = fb->len;
  }
  memcpy(prev_frame, fb->buf, fb->len);

  esp_camera_fb_return(fb);
  return motion;
}

// ---------------------------------------------------------------------------
// 4. PIR (presence) - PLACEHOLDER, not wired to this board yet
// ---------------------------------------------------------------------------
// Once a PIR is attached, replace this with a digitalRead() on whatever GPIO
// it's wired to (same 1.0/0.0 convention as the Pi's own pir.py). Left as a
// flat 0.0 rather than reading an unconnected/floating pin, which would
// produce garbage "presence" spikes instead of a clean absence of data.

float readPresence() {
  return 0.0f;  // TODO: wire a PIR to a GPIO and read it here
}

// ---------------------------------------------------------------------------
// setup / loop
// ---------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  delay(1000);  // let the Serial console catch up before the first prints

  setupMic();
  setupCamera();
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
  float presence = readPresence();

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
