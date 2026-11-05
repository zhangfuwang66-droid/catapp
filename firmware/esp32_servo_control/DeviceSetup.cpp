#include "DeviceSetup.h"

#include <WiFi.h>
#include <cJSON.h>
#include <esp_system.h>
#include "SetupPage.h"

bool DeviceSetup::start(bool manual) {
  if (active_) { manual_ = manual_ || manual; return true; }
  // NVS 失败时不退回开放热点。
  if (config_.setupPassword().length() < 12) return false;
  if (!initialized_) { routes(); initialized_ = true; }
  csrf_ = "";
  for (int i = 0; i < 4; ++i) { char part[9]; snprintf(part, sizeof(part), "%08lx", static_cast<unsigned long>(esp_random())); csrf_ += part; }
  WiFi.mode(WIFI_AP_STA);
  if (!WiFi.softAP(config_.setupSsid().c_str(), config_.setupPassword().c_str())) return false;
  dns_.start(53, "*", WiFi.softAPIP());
  server_.begin();
  active_ = true; manual_ = manual;
  return true;
}

void DeviceSetup::poll() {
  if (active_) { dns_.processNextRequest(); server_.handleClient(); }
}

void DeviceSetup::closeAutomatic() {
  if (!active_ || manual_ || rebootPending_) return;
  server_.stop(); dns_.stop();
  WiFi.softAPdisconnect(true);
  active_ = false; csrf_ = "";
}

bool DeviceSetup::shouldReboot() const { return rebootPending_ && static_cast<int32_t>(millis() - rebootAt_) >= 0; }

void DeviceSetup::json(int code, cJSON* object) {
  server_.sendHeader("Cache-Control", "no-store");
  server_.sendHeader("X-Content-Type-Options", "nosniff");
  char* body = cJSON_PrintUnformatted(object);
  cJSON_Delete(object);
  if (!body) { server_.send(503, "application/json", "{\"error\":\"内存不足\"}"); return; }
  server_.send(code, "application/json; charset=utf-8", body);
  cJSON_free(body);
}

void DeviceSetup::error(int code, const String& message) {
  cJSON* body = cJSON_CreateObject();
  cJSON_AddStringToObject(body, "error", message.c_str());
  json(code, body);
}

bool DeviceSetup::allowRead() {
  if (!active_ || server_.client().localIP() != WiFi.softAPIP()) { error(403, "请连接设备热点后访问"); return false; }
  String host = server_.hostHeader();
  String ip = WiFi.softAPIP().toString();
  if (host != ip && host != ip + ":80") { error(403, "请通过设备热点 IP 访问设置"); return false; }
  return true;
}

bool DeviceSetup::allowWrite() {
  if (!allowRead()) return false;
  if (server_.header("Origin") != "http://" + WiFi.softAPIP().toString()
      || !csrf_.length() || server_.header("X-CyberMiao-CSRF") != csrf_) {
    error(403, "页面已失效，请刷新后重试"); return false;
  }
  if (rebootPending_) { error(409, "配置已保存，设备正在重启"); return false; }
  return true;
}

void DeviceSetup::status() {
  if (!allowRead()) return;
  DeviceLinkSnapshot state = link_.snapshot();
  cJSON* body = cJSON_CreateObject();
  cJSON_AddStringToObject(body, "device_id", config_.deviceId().c_str());
  cJSON_AddStringToObject(body, "server_url", config_.serverUrl().c_str());
  cJSON_AddStringToObject(body, "wifi_ssid", config_.network().ssid.c_str());
  cJSON_AddStringToObject(body, "mode", config_.network().accounts ? "accounts" : "local");
  cJSON_AddBoolToObject(body, "credential_configured", config_.hasCredential());
  cJSON_AddStringToObject(body, "status", DeviceLink::statusName(state.status));
  cJSON_AddBoolToObject(body, "bound", state.bound);
  cJSON_AddStringToObject(body, "pairing_code", state.pairingCode.c_str());
  cJSON_AddNumberToObject(body, "expires_in", state.expiresIn);
  cJSON_AddStringToObject(body, "csrf_token", csrf_.c_str());
  json(200, body);
}

void DeviceSetup::configure() {
  if (!allowWrite()) return;
  DeviceNetworkConfig candidate = config_.network();
  candidate.ssid = server_.arg("ssid"); candidate.password = server_.arg("password");
  String mode = server_.arg("mode");
  if (mode != "accounts" && mode != "local") { error(400, "请选择连接方式"); return; }
  candidate.accounts = mode == "accounts";
  String message;
  if (!DeviceConfig::parseServerUrl(server_.arg("server_url"), candidate, message) || !config_.saveNetwork(candidate, message)) {
    error(400, message); return;
  }
  cJSON* body = cJSON_CreateObject();
  cJSON_AddStringToObject(body, "message", "设置已保存，即将重启。请等待小喵连接网络；连接失败时可重新加入设备热点修改。");
  json(200, body);
  rebootPending_ = true; rebootAt_ = millis() + 1200;
}

void DeviceSetup::scan() {
  if (!allowWrite()) return;
  int count = WiFi.scanComplete();
  cJSON* body = cJSON_CreateObject();
  cJSON* networks = cJSON_AddArrayToObject(body, "networks");
  if (count == WIFI_SCAN_FAILED) {
    count = WiFi.scanNetworks(true);
  }
  cJSON_AddBoolToObject(body, "scanning", count == WIFI_SCAN_RUNNING);
  if (count >= 0) {
    for (int i = 0; i < count && i < 20; ++i) {
      String ssid = WiFi.SSID(i);
      if (!ssid.length()) continue;
      cJSON* item = cJSON_CreateObject();
      cJSON_AddStringToObject(item, "ssid", ssid.c_str());
      cJSON_AddNumberToObject(item, "rssi", WiFi.RSSI(i));
      cJSON_AddItemToArray(networks, item);
    }
    WiFi.scanDelete();
  }
  json(200, body);
}

void DeviceSetup::routes() {
  const char* headers[] = {"Origin", "X-CyberMiao-CSRF"};
  server_.collectHeaders(headers, 2);
  server_.on("/", HTTP_GET, [this]() {
    String ip = WiFi.softAPIP().toString();
    if (active_ && server_.client().localIP() == WiFi.softAPIP() && server_.hostHeader() != ip && server_.hostHeader() != ip + ":80") {
      server_.sendHeader("Location", "http://" + ip + "/");
      server_.sendHeader("Cache-Control", "no-store");
      server_.send(302, "text/plain", "Open device setup"); return;
    }
    if (!allowRead()) return;
    server_.sendHeader("Cache-Control", "no-store");
    server_.sendHeader("X-Frame-Options", "DENY");
    server_.sendHeader("X-Content-Type-Options", "nosniff");
    server_.sendHeader("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'none'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'");
    server_.send_P(200, "text/html; charset=utf-8", DEVICE_SETUP_HTML);
  });
  server_.on("/status", HTTP_GET, [this]() { status(); });
  server_.on("/configure", HTTP_POST, [this]() { configure(); });
  server_.on("/scan", HTTP_POST, [this]() { scan(); });
  server_.on("/pairing", HTTP_POST, [this]() {
    if (!allowWrite()) return;
    if (!config_.network().accounts || !config_.hasCredential() || link_.snapshot().bound) { error(409, "当前设备无需申请配对码"); return; }
    link_.requestPairing();
    cJSON* body = cJSON_CreateObject(); cJSON_AddBoolToObject(body, "ok", true); json(200, body);
  });
  server_.onNotFound([this]() {
    if (!active_ || server_.client().localIP() != WiFi.softAPIP() || server_.method() != HTTP_GET) { error(404, "未找到页面"); return; }
    server_.sendHeader("Location", "http://" + WiFi.softAPIP().toString() + "/");
    server_.sendHeader("Cache-Control", "no-store");
    server_.send(302, "text/plain", "Open device setup");
  });
}
