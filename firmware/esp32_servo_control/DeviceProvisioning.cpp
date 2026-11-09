#include "DeviceProvisioning.h"

#include <cJSON.h>

namespace {
constexpr const char* PREFIX = "CMIAO1 ";
constexpr size_t MAX_LINE = 1536;

String field(const cJSON* object, const char* name) {
  const cJSON* value = cJSON_GetObjectItemCaseSensitive(object, name);
  return cJSON_IsString(value) && value->valuestring ? String(value->valuestring) : String();
}

bool validRequestId(const String& id) {
  if (id.length() != 32) return false;
  for (size_t i = 0; i < id.length(); ++i) if (!isxdigit(static_cast<unsigned char>(id[i]))) return false;
  return true;
}
}  // namespace

void DeviceProvisioning::command(const String& line) {
  if (!line.startsWith(PREFIX)) return;
  cJSON* request = cJSON_ParseWithOpts(line.c_str() + strlen(PREFIX), nullptr, true);
  String requestId = field(request, "request_id");
  if (!cJSON_IsObject(request) || !validRequestId(requestId)) { cJSON_Delete(request); return; }
  String operation = field(request, "op");
  String error;
  bool ok = false;
  bool changed = false;
  if (rebootPending_ && operation != "status") error = "设备正在重启，请稍后重试";
  else if (operation == "status") ok = true;
  else if (operation == "diagnostics" && diagnostics_) ok = true;
  else if (operation == "provision") {
    ok = config_.saveCredential(field(request, "device_id"), field(request, "device_token"), error);
    changed = ok;
  } else if (operation == "configure") {
    DeviceNetworkConfig candidate = config_.network();
    if (cJSON_HasObjectItem(request, "wifi_ssid")) candidate.ssid = field(request, "wifi_ssid");
    if (cJSON_HasObjectItem(request, "wifi_password")) candidate.password = field(request, "wifi_password");
    String mode = field(request, "mode");
    if (mode.length() && mode != "local" && mode != "accounts") error = "模式仅支持 local 或 accounts";
    else {
      if (mode.length()) candidate.accounts = mode == "accounts";
      ok = DeviceConfig::parseServerUrl(field(request, "server_url"), candidate, error) && config_.saveNetwork(candidate, error);
      changed = ok;
    }
  } else if (operation == "setup") {
    setupRequested_ = true; ok = true;
  } else error = "不支持此操作";
  cJSON_Delete(request);
  if (changed) { rebootPending_ = true; rebootAt_ = millis() + 1000; }
  cJSON* response = cJSON_CreateObject();
  if (!response) return;
  cJSON_AddNumberToObject(response, "protocol", 1);
  cJSON_AddStringToObject(response, "request_id", requestId.c_str());
  cJSON_AddBoolToObject(response, "ok", ok);
  cJSON_AddStringToObject(response, "device_id", config_.deviceId().c_str());
  cJSON_AddBoolToObject(response, "credential_configured", config_.hasCredential());
  cJSON_AddBoolToObject(response, "pending_reboot", rebootPending_);
  cJSON_AddBoolToObject(response, "identity_saved", changed && operation == "provision");
  cJSON_AddBoolToObject(response, "has_wifi", config_.network().ssid.length() > 0);
  cJSON_AddStringToObject(response, "server_url", config_.serverUrl().c_str());
  cJSON_AddStringToObject(response, "mode", config_.network().accounts ? "accounts" : "local");
  cJSON_AddStringToObject(response, "setup_ssid", config_.setupSsid().c_str());
  if (ok && operation == "diagnostics") {
    cJSON* diagnostics = cJSON_AddObjectToObject(response, "diagnostics");
    if (diagnostics) diagnostics_(diagnostics);
  }
  if (!ok) cJSON_AddStringToObject(response, "error", error.c_str());
  char* encoded = cJSON_PrintUnformatted(response);
  cJSON_Delete(response);
  if (encoded) { serial_.println(String(PREFIX) + encoded); cJSON_free(encoded); }
}

void DeviceProvisioning::poll() {
  if (buffer_.length() && millis() - lastByteAt_ > 3000) { buffer_ = ""; discarding_ = true; }
  for (int count = 0; count < 192 && serial_.available(); ++count) {
    char c = static_cast<char>(serial_.read());
    lastByteAt_ = millis();
    if (c == '\n') {
      if (!discarding_) command(buffer_);
      buffer_ = ""; discarding_ = false;
    } else if (c != '\r' && !discarding_) {
      if (buffer_.length() >= MAX_LINE) { buffer_ = ""; discarding_ = true; }
      else buffer_ += c;
    }
  }
}

bool DeviceProvisioning::shouldReboot() const {
  return rebootPending_ && static_cast<int32_t>(millis() - rebootAt_) >= 0;
}

bool DeviceProvisioning::takeSetupRequest() {
  bool requested = setupRequested_; setupRequested_ = false; return requested;
}
