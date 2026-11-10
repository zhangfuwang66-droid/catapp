#include "DeviceSetup.h"

#include <WiFi.h>
#include <cJSON.h>
#include <esp_system.h>
#include <esp_wifi.h>
#include <algorithm>
#include <cctype>
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
  if (!active_) return;
  // ESP32 只有一个 Wi-Fi 无线电。手机配网时不要继续关联旧网络或切换 AP 信道。
  if (WiFi.softAPgetStationNum()) session_.touch(millis());
  if (holdsNetwork() && WiFi.status() != WL_CONNECTED && !stationPaused_) {
    WiFi.setAutoReconnect(false);
    WiFi.disconnect(false, false);
    stationPaused_ = true;
  } else if (!holdsNetwork()) {
    stationPaused_ = false;
  }
  if (session_.scanExpired(millis())) {
    esp_wifi_scan_stop();
    WiFi.scanDelete();
    session_.expireScan();
  }
  dns_.processNextRequest();
  server_.handleClient();
}

bool DeviceSetup::holdsNetwork() const {
  return active_ && session_.holdsNetwork(millis(), WiFi.softAPgetStationNum() > 0);
}

void DeviceSetup::closeAutomatic() {
  if (!active_ || manual_ || holdsNetwork()) return;
  server_.stop(); dns_.stop();
  WiFi.softAPdisconnect(true);
  active_ = false; csrf_ = "";
}

bool DeviceSetup::shouldReboot() const { return session_.shouldReboot(millis()); }

void DeviceSetup::json(int code, cJSON* object) {
  server_.sendHeader("Cache-Control", "no-store");
  server_.sendHeader("X-Content-Type-Options", "nosniff");
  server_.sendHeader("Connection", "close");
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
  session_.touch(millis());
  return true;
}

bool DeviceSetup::allowWrite() {
  if (!allowRead()) return false;
  if (server_.header("Origin") != "http://" + WiFi.softAPIP().toString()
      || !csrf_.length() || server_.header("X-CyberMiao-CSRF") != csrf_) {
    error(403, "页面已失效，请刷新后重试"); return false;
  }
  if (session_.rebootPending()) { error(409, "配置已保存，设备正在重启"); return false; }
  return true;
}

void DeviceSetup::status() {
  if (!allowRead()) return;
  DeviceLinkSnapshot state = link_.snapshot();
  cJSON* body = cJSON_CreateObject();
  cJSON_AddStringToObject(body, "device_id", config_.deviceId().c_str());
  cJSON_AddStringToObject(body, "device_type", DeviceProfile::type);
  cJSON_AddStringToObject(body, "product_name", DeviceProfile::name);
  cJSON_AddStringToObject(body, "server_url", config_.serverUrl().c_str());
  cJSON_AddStringToObject(body, "wifi_ssid", config_.network().ssid.c_str());
  cJSON_AddBoolToObject(body, "wifi_connected", WiFi.status() == WL_CONNECTED);
  cJSON_AddBoolToObject(body, "password_saved", config_.network().password.length() > 0);
  cJSON_AddBoolToObject(body, "reboot_pending", session_.rebootPending());
  cJSON_AddStringToObject(body, "save_request_id", saveRequestId_.c_str());
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
  if (!server_.hasArg("ssid") || !server_.hasArg("password") || !server_.hasArg("server_url")) {
    error(400, "设置不完整，请刷新后重试"); return;
  }
  String requestId = server_.arg("request_id");
  if (requestId.length()) {
    if (requestId.length() != 32) { error(400, "保存请求无效，请刷新后重试"); return; }
    for (size_t i = 0; i < requestId.length(); ++i) {
      if (!isxdigit(static_cast<unsigned char>(requestId[i]))) { error(400, "保存请求无效，请刷新后重试"); return; }
    }
  }
  DeviceNetworkConfig candidate = config_.network();
  candidate.ssid = server_.arg("ssid");
  if (server_.arg("keep_password") == "1") {
    if (candidate.ssid != config_.network().ssid || server_.arg("password").length()) {
      error(400, "切换 Wi-Fi 后请重新输入密码"); return;
    }
  } else {
    candidate.password = server_.arg("password");
  }
  String mode = server_.arg("mode");
  if (mode != "accounts" && mode != "local") { error(400, "请选择连接方式"); return; }
  candidate.accounts = mode == "accounts";
  String message;
  if (!DeviceConfig::parseServerUrl(server_.arg("server_url"), candidate, message) || !config_.saveNetwork(candidate, message)) {
    error(400, message); return;
  }
  if (session_.scanning()) { esp_wifi_scan_stop(); WiFi.scanDelete(); session_.finishScan(); }
  saveRequestId_ = requestId;
  session_.saved(millis());
  cJSON* body = cJSON_CreateObject();
  cJSON_AddBoolToObject(body, "saved", true);
  cJSON_AddStringToObject(body, "save_request_id", saveRequestId_.c_str());
  cJSON_AddNumberToObject(body, "restart_in_ms", SetupSession::REBOOT_DELAY_MS);
  cJSON_AddStringToObject(body, "message", "设置已保存，即将重启。请等待" CYBERMIAO_SHORT_NAME "连接网络；连接失败时可重新加入设备热点修改。");
  json(200, body);
}

void DeviceSetup::scan() {
  if (!allowWrite()) return;
  String action = server_.arg("action");
  if (action.length() && action != "start" && action != "poll") { error(400, "扫描请求无效"); return; }
  if (session_.scanTimedOut() && action != "start") { error(504, "扫描超时，请重新扫描或手动输入 Wi-Fi 名称"); return; }
  if (!session_.scanning()) {
    if (action == "poll") { error(409, "本次扫描已结束，请重新扫描"); return; }
    WiFi.scanDelete();
    WiFi.setScanTimeout(SetupSession::SCAN_TIMEOUT_MS);
    // 异步扫描；单信道驻留 120ms，避免长时间占用手机与 AP 之间的无线连接。
    int started = WiFi.scanNetworks(true, false, false, 120);
    if (started == WIFI_SCAN_FAILED) { error(503, "暂时无法扫描，请稍后重试，也可以手动输入 Wi-Fi 名称"); return; }
    session_.startScan(millis());
  }
  int count = WiFi.scanComplete();
  if (count == WIFI_SCAN_FAILED) {
    esp_wifi_scan_stop(); WiFi.scanDelete(); session_.finishScan();
    error(503, "扫描未完成，请重新扫描或手动输入 Wi-Fi 名称"); return;
  }
  cJSON* body = cJSON_CreateObject();
  cJSON* networks = cJSON_AddArrayToObject(body, "networks");
  cJSON_AddBoolToObject(body, "scanning", count == WIFI_SCAN_RUNNING);
  if (count >= 0) {
    // 同名同加密方式保留信号最强的 AP，避免 mesh 网络把列表挤满。
    int best[20];
    int kept = 0;
    for (int i = 0; i < count; ++i) {
      String ssid = WiFi.SSID(i);
      if (!ssid.length()) continue;
      int duplicate = -1;
      for (int j = 0; j < kept; ++j) {
        if (WiFi.SSID(best[j]) == ssid && WiFi.encryptionType(best[j]) == WiFi.encryptionType(i)) { duplicate = j; break; }
      }
      if (duplicate >= 0) {
        if (WiFi.RSSI(i) > WiFi.RSSI(best[duplicate])) best[duplicate] = i;
      } else if (kept < 20) best[kept++] = i;
      else if (WiFi.RSSI(i) > WiFi.RSSI(best[kept - 1])) best[kept - 1] = i;
      std::sort(best, best + kept, [](int a, int b) { return WiFi.RSSI(a) > WiFi.RSSI(b); });
    }
    for (int j = 0; j < kept; ++j) {
      int i = best[j];
      cJSON* item = cJSON_CreateObject();
      cJSON_AddStringToObject(item, "ssid", WiFi.SSID(i).c_str());
      cJSON_AddNumberToObject(item, "rssi", WiFi.RSSI(i));
      cJSON_AddBoolToObject(item, "secure", WiFi.encryptionType(i) != WIFI_AUTH_OPEN);
      cJSON_AddItemToArray(networks, item);
    }
    WiFi.scanDelete();
    session_.finishScan();
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
