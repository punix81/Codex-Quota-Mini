#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <WiFiManager.h>

#if __has_include("lan_secret.h")
#include "lan_secret.h"
#else
constexpr char kLanKey[] = "";
#endif

#include "display.h"

namespace {

constexpr uint8_t kTouchPin = 6;
constexpr uint32_t kDebounceMs = 35;
constexpr uint32_t kStaleMs = 90000;
constexpr uint32_t kFrameIntervalMs = 67;
constexpr uint32_t kGaugeTransitionMs = 420;
constexpr uint32_t kOrbitPeriodMs = 3200;
constexpr int kCenter = 120;
constexpr size_t kPageCount = 4;

struct Page {
  Page(const char* label) : title(label) {}

  const char* title;
  char value[24] = "--";
  char detail[24] = "";
  int percent = -1;
};

Page pages[kPageCount] = {
    {"5 HOURS"}, {"WEEKLY"}, {"CREDITS"}, {"TOKENS USED"},
};
Display tft;
WiFiManager wifi;
#ifndef QUOTA_URL
#define QUOTA_URL ""
#endif
constexpr char kQuotaUrl[] = QUOTA_URL;
uint32_t last_fetch = 0;
bool first_fetch = true;
LGFX_Sprite frame(&tft);
bool buffered = false;
size_t page_index = 0;
bool has_data = false;
bool codex_error = false;
uint32_t last_update = 0;
bool dirty = true;

char serial_line[512];
size_t line_length = 0;
bool discard_line = false;

bool stale(uint32_t now) {
  return has_data && (codex_error || now - last_update >= kStaleMs);
}

void render(uint32_t now) {
  auto& gfx = buffered ? static_cast<lgfx::LGFXBase&>(frame)
                       : static_cast<lgfx::LGFXBase&>(tft);
  const auto& page = pages[page_index];
  const bool data_stale = stale(now);
  const uint16_t background = 0x0000;
  const uint16_t white = 0xFFFF;
  const uint16_t grid = tft.color565(0, 32, 0);
  const uint16_t track = tft.color565(32, 64, 0);
  const uint16_t lime = tft.color565(150, 255, 0);
  const uint16_t muted = tft.color565(160, 190, 155);
  const uint16_t accent = data_stale || codex_error ? tft.color565(255, 160, 0) : lime;

  // Ease the gauge into the new reading without changing the displayed value.
  static size_t previous_page = kPageCount;
  static int previous_target = -1;
  static float displayed = 0.0f;
  static float transition_start = 0.0f;
  static uint32_t transition_time = 0;
  const int target = max(0, page.percent);
  if (page_index != previous_page || target != previous_target) {
    transition_start = page_index == previous_page ? displayed : 0.0f;
    transition_time = now;
    previous_page = page_index;
    previous_target = target;
  }
  const float progress = min(now - transition_time, kGaugeTransitionMs) /
                         static_cast<float>(kGaugeTransitionMs);
  const float eased = 1.0f - (1.0f - progress) * (1.0f - progress);
  displayed = buffered ? transition_start + (target - transition_start) * eased : target;

  gfx.startWrite();
  gfx.fillScreen(background);
  gfx.drawCircle(kCenter, kCenter, 109, grid);
  gfx.drawCircle(kCenter, kCenter, 96, grid);

  // Leave a gap at the bottom for the status and page indicators.
  constexpr int kFirstAngle = 140;
  constexpr int kSegmentStep = 10;
  constexpr int kSegments = 26;
  const float active_end = kFirstAngle + kSegments * kSegmentStep * displayed / 100.0f;
  for (int i = 0; i < kSegments; ++i) {
    const int start = kFirstAngle + i * kSegmentStep;
    const int end = start + 7;
    gfx.fillArc(kCenter, kCenter, 106, 100, start, end, track);
    if (active_end > start) {
      gfx.fillArc(kCenter, kCenter, 106, 100, start,
                  min(active_end, static_cast<float>(end)), accent);
    }
  }

  if (buffered) {
    const uint32_t phase = now % kOrbitPeriodMs;
    const float angle = 360.0f * phase / kOrbitPeriodMs;
    gfx.fillArc(kCenter, kCenter, 117, 114, angle - 30.0f, angle - 10.0f, track);
    gfx.fillArc(kCenter, kCenter, 117, 113, angle - 10.0f, angle, accent);
    const int scan_y = 94 + phase * 55 / kOrbitPeriodMs;
    gfx.drawFastHLine(39, scan_y, 162, grid);
  }

  gfx.drawFastHLine(35, 99, 10, accent);
  gfx.drawFastVLine(35, 99, 12, accent);
  gfx.drawFastHLine(195, 99, 10, accent);
  gfx.drawFastVLine(204, 99, 12, accent);
  gfx.drawFastHLine(35, 144, 10, accent);
  gfx.drawFastVLine(35, 133, 12, accent);
  gfx.drawFastHLine(195, 144, 10, accent);
  gfx.drawFastVLine(204, 133, 12, accent);

  gfx.setTextDatum(textdatum_t::middle_center);
  gfx.setFont(&lgfx::fonts::FreeMonoBold9pt7b);
  gfx.setTextColor(accent, background);
  gfx.drawString("CODEX // LINK", kCenter, 49);
  gfx.setFont(&lgfx::fonts::Font2);
  gfx.setTextColor(white, background);
  gfx.drawString(page.title, kCenter, 75);

  gfx.setFont(&lgfx::fonts::FreeMonoBold24pt7b);
  if (gfx.textWidth(page.value) > 154) {
    gfx.setFont(&lgfx::fonts::FreeMonoBold12pt7b);
  }
  if (gfx.textWidth(page.value) > 154) {
    gfx.setFont(&lgfx::fonts::Font2);
  }
  gfx.setTextColor(white, background);
  gfx.drawString(page.value, kCenter, 120);

  gfx.setFont(&lgfx::fonts::Font2);
  gfx.setTextColor(muted, background);
  gfx.drawString(page.detail, kCenter, 157);

  const char* status = "SIGNAL // LIVE";
  if (data_stale) {
    status = "STALE DATA";
  } else if (codex_error) {
    status = "CODEX UNAVAILABLE";
  } else if (!has_data) {
    status = !kQuotaUrl[0] || !kLanKey[0] ? "WAITING FOR USB" :
             WiFi.isConnected() ? "WAITING FOR PI" : "WIFI: CodexQuota-Setup";
  }
  gfx.setTextColor(accent, background);
  gfx.drawString(status, kCenter, 179);

  for (size_t i = 0; i < kPageCount; ++i) {
    const int x = 89 + static_cast<int>(i) * 17;
    gfx.fillRect(x, 201, 11, 4, i == page_index ? accent : track);
  }
  gfx.endWrite();

  if (buffered) {
    frame.pushSprite(0, 0);
  }
}

void acceptFrame(const char* payload, size_t length) {
  JsonDocument document;
  if (deserializeJson(document, payload, length) ||
      document["v"].as<int>() != 1 || !document["ok"].is<bool>()) {
    return;
  }
  if (!document["ok"].as<bool>()) {
    codex_error = true;
    dirty = true;
    return;
  }

  const JsonArrayConst incoming = document["pages"].as<JsonArrayConst>();
  if (incoming.size() != kPageCount) {
    return;
  }
  for (size_t i = 0; i < kPageCount; ++i) {
    const auto data = incoming[i];
    auto& page = pages[i];
    snprintf(page.value, sizeof(page.value), "%s", data["value"] | "--");
    snprintf(page.detail, sizeof(page.detail), "%s", data["detail"] | "");
    page.percent = constrain(data["percent"] | -1, -1, 100);
  }

  has_data = true;
  codex_error = false;
  last_update = millis();
  dirty = true;
}

void pollSerial() {
  while (Serial.available() > 0) {
    const char byte = static_cast<char>(Serial.read());
    if (byte == '\n') {
      if (!discard_line && line_length != 0) {
        acceptFrame(serial_line, line_length);
      }
      line_length = 0;
      discard_line = false;
    } else if (byte != '\r' && !discard_line) {
      if (line_length == sizeof(serial_line)) {
        discard_line = true;
      } else {
        serial_line[line_length++] = byte;
      }
    }
  }
}

void pollQuota(uint32_t now) {
  if (!kQuotaUrl[0] || !kLanKey[0] || !WiFi.isConnected() || (!first_fetch && now - last_fetch < 30000)) {
    return;
  }
  first_fetch = false;
  last_fetch = now;
  HTTPClient http;
  http.setConnectTimeout(1200);
  http.setTimeout(1500);
  if (http.begin(kQuotaUrl)) {
    http.addHeader("Authorization", String("Bearer ") + kLanKey);
    if (http.GET() == HTTP_CODE_OK && http.getSize() > 0 && http.getSize() <= 512) {
      const int expected = http.getSize();
      const String body = http.getString();
      if (body.length() == static_cast<unsigned>(expected)) {
        acceptFrame(body.c_str(), body.length());
      }
    }
    http.end();
  }
}

void pollTouch(uint32_t now) {
  static bool last_reading = false;
  static bool stable_pressed = false;
  static uint32_t last_change = 0;

  const bool pressed = digitalRead(kTouchPin) == HIGH;
  if (pressed != last_reading) {
    last_reading = pressed;
    last_change = now;
  }
  if (pressed == stable_pressed || now - last_change < kDebounceMs) {
    return;
  }

  stable_pressed = pressed;
  if (pressed) {
    page_index = (page_index + 1) % kPageCount;
    dirty = true;
  }
}

}  // namespace

void setup() {
  Serial.setRxBufferSize(1024);
  Serial.begin(115200);
  pinMode(kTouchPin, INPUT_PULLDOWN);
  tft.init();
  tft.setRotation(0);
  frame.setColorDepth(8);
  buffered = frame.createSprite(240, 240) != nullptr;
  if (!kQuotaUrl[0] || !kLanKey[0]) {
    return;
  }
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  wifi.setConfigPortalBlocking(false);
  wifi.setConfigPortalTimeout(180);
  wifi.setConnectTimeout(8);
  wifi.autoConnect("CodexQuota-Setup");
}

void loop() {
  pollSerial();
  const uint32_t now = millis();
  pollTouch(now);
  wifi.process();
  static bool was_connected = false;
  if (WiFi.isConnected() != was_connected) {
    was_connected = WiFi.isConnected();
    first_fetch = true;
    dirty = true;
  }
  pollQuota(now);

  static bool was_stale = false;
  static uint32_t last_frame = 0;
  const uint32_t render_time = millis();
  const bool is_stale = stale(render_time);
  const bool animate = buffered && render_time - last_frame >= kFrameIntervalMs;
  if (dirty || is_stale != was_stale || animate) {
    last_frame = render_time;
    render(render_time);
    dirty = false;
    was_stale = is_stale;
  }
  delay(5);
}
