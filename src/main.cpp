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
  const uint16_t background = tft.color565(0, 8, 18);
  const uint16_t track = tft.color565(38, 56, 66);
  const uint16_t accent = tft.color565(72, 240, 170);
  const uint16_t muted = tft.color565(155, 177, 185);
  const uint16_t text = tft.color565(250, 255, 255);

  gfx.startWrite();
  gfx.fillScreen(background);
  gfx.fillArc(kCenter, kCenter, 114, 108, -220.0f, 40.0f, track);
  if (page.percent > 0) {
    gfx.fillArc(kCenter, kCenter, 114, 108, -220.0f,
                -220.0f + 2.6f * page.percent, accent);
  }

  gfx.setTextDatum(textdatum_t::middle_center);
  gfx.setFont(&lgfx::fonts::Font2);
  gfx.setTextColor(muted, background);
  gfx.drawString("CODEX", kCenter, 51);
  gfx.setTextColor(accent, background);
  gfx.drawString(page.title, kCenter, 79);

  gfx.setFont(&lgfx::fonts::FreeSansBold24pt7b);
  if (gfx.textWidth(page.value) > 180) {
    gfx.setFont(&lgfx::fonts::FreeSansBold12pt7b);
  }
  gfx.setTextColor(text, background);
  gfx.drawString(page.value, kCenter, 123);

  gfx.setFont(&lgfx::fonts::Font2);
  gfx.setTextColor(muted, background);
  gfx.drawString(page.detail, kCenter, 158);
  if (stale(now)) {
    gfx.drawString("STALE DATA", kCenter, 182);
  } else if (codex_error) {
    gfx.drawString("CODEX UNAVAILABLE", kCenter, 182);
  } else if (!has_data) {
    gfx.drawString(!kQuotaUrl[0] || !kLanKey[0] ? "WAITING FOR USB" :
                   WiFi.isConnected() ? "WAITING FOR PI" : "WIFI: CodexQuota-Setup", kCenter, 182);
  }
  for (size_t i = 0; i < kPageCount; ++i) {
    gfx.fillCircle(96 + static_cast<int>(i) * 16, 202, 3,
                   i == page_index ? accent : track);
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
  const uint32_t render_time = millis();
  const bool is_stale = stale(render_time);
  if (dirty || is_stale != was_stale) {
    render(render_time);
    dirty = false;
    was_stale = is_stale;
  }
  delay(5);
}
