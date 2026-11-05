#include "DeviceConfig.h"

#include <Preferences.h>
#include <IPAddress.h>
#include <cJSON.h>
#include <cctype>
#include <esp_system.h>

namespace {
String stringField(const cJSON* object, const char* key) {
  const cJSON* field = cJSON_GetObjectItemCaseSensitive(object, key);
  return cJSON_IsString(field) && field->valuestring ? String(field->valuestring) : String();
}

bool putJson(const char* ns, const char* key, cJSON* object, String& error) {
  char* json = cJSON_PrintUnformatted(object);
  cJSON_Delete(object);
  if (!json) { error = "配置编码失败"; return false; }
  Preferences preferences;
  bool opened = preferences.begin(ns, false);
  size_t size = strlen(json);
  size_t written = opened ? preferences.putString(key, json) : 0;
  if (opened) preferences.end();
  cJSON_free(json);
  if (written != size) { error = "配置存储失败"; return false; }
  return true;
}

bool validHost(const String& host) {
  if (!host.length() || host.length() > 253) return false;
  bool numeric = true;
  size_t labelLength = 0;
  for (size_t i = 0; i < host.length(); ++i) {
    char c = host[i];
    if (c == '.') {
      if (!labelLength || host[i - 1] == '-') return false;
      labelLength = 0;
    } else {
      if (!isalnum(static_cast<unsigned char>(c)) && c != '-') return false;
      if (!labelLength && c == '-') return false;
      if (++labelLength > 63) return false;
      if (!isdigit(static_cast<unsigned char>(c))) numeric = false;
    }
  }
  if (!labelLength || host[host.length() - 1] == '-') return false;
  if (numeric) {
    IPAddress ip;
    return ip.fromString(host);
  }
  return true;
}
}  // namespace

bool DeviceConfig::parseServerUrl(String url, DeviceNetworkConfig& config, String& error) {
  url.trim();
  if (!url.length() || url.length() > 300) { error = "请填写服务器地址"; return false; }
  bool tls = false;
  uint16_t port = 8081;
  if (url.startsWith("https://")) { tls = true; port = 443; url.remove(0, 8); }
  else if (url.startsWith("http://")) { port = 80; url.remove(0, 7); }
  else if (url.indexOf("://") >= 0) { error = "仅支持 http 或 https"; return false; }
  if (url.endsWith("/")) url.remove(url.length() - 1);
  int colon = url.indexOf(':');
  String host = colon < 0 ? url : url.substring(0, colon);
  if (!validHost(host)) { error = "服务器需为域名或 IPv4，不能包含路径、账号或查询参数"; return false; }
  if (colon >= 0) {
    String number = url.substring(colon + 1);
    if (!number.length() || number.length() > 5) { error = "端口需为 1～65535"; return false; }
    for (size_t i = 0; i < number.length(); ++i) {
      if (!isdigit(static_cast<unsigned char>(number[i]))) { error = "端口需为 1～65535"; return false; }
    }
    unsigned long parsed = strtoul(number.c_str(), nullptr, 10);
    if (!parsed || parsed > 65535) { error = "端口需为 1～65535"; return false; }
    port = static_cast<uint16_t>(parsed);
  }
  config.host = host; config.port = port; config.tls = tls;
  return true;
}

bool DeviceConfig::validCredential(const String& credential) {
  if (credential.length() < 32 || credential.length() > 128) return false;
  for (size_t i = 0; i < credential.length(); ++i) {
    char c = credential[i];
    if (!isalnum(static_cast<unsigned char>(c)) && c != '-' && c != '_') return false;
  }
  return true;
}

bool DeviceConfig::begin() {
  uint64_t mac = ESP.getEfuseMac();
  char id[24];
  snprintf(id, sizeof(id), "cat-%04X%08X", static_cast<unsigned int>(mac >> 32), static_cast<unsigned int>(mac));
  deviceId_ = id;
  Preferences preferences;
  if (!preferences.begin("cmiao_id", false)) return false;
  String identity = preferences.getString("identity", "");
  setupPassword_ = preferences.getString("setup_pass", "");
  if (setupPassword_.length() < 12) {
    constexpr char alphabet[] = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
    setupPassword_ = "";
    for (int i = 0; i < 12; ++i) setupPassword_ += alphabet[esp_random() % (sizeof(alphabet) - 1)];
    if (preferences.putString("setup_pass", setupPassword_) != setupPassword_.length()) {
      preferences.end(); return false;
    }
  }
  preferences.end();
  cJSON* object = cJSON_Parse(identity.c_str());
  if (object) {
    String savedId = stringField(object, "device_id");
    String savedCredential = stringField(object, "device_token");
    const cJSON* version = cJSON_GetObjectItemCaseSensitive(object, "version");
    if (cJSON_IsNumber(version) && version->valueint == 1 && savedId == deviceId_ && validCredential(savedCredential)) credential_ = savedCredential;
    cJSON_Delete(object);
  }
  if (!preferences.begin("cmiao_cfg", true)) {
    // 未创建的新命名空间是正常情况，下面读取原有 Wi-Fi 配置。
  } else {
    String saved = preferences.getString("network", "");
    preferences.end();
    object = cJSON_Parse(saved.c_str());
    if (saved.length() && !object) return false;
    if (object) {
      DeviceNetworkConfig candidate;
      candidate.ssid = stringField(object, "ssid");
      candidate.password = stringField(object, "password");
      candidate.accounts = cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(object, "accounts"));
      String error;
      const cJSON* version = cJSON_GetObjectItemCaseSensitive(object, "version");
      bool valid = cJSON_IsNumber(version) && version->valueint == 1 && parseServerUrl(stringField(object, "server_url"), candidate, error);
      cJSON_Delete(object);
      if (valid && candidate.ssid.length() <= 32 && candidate.password.length() <= 64) {
        network_ = candidate; return true;
      }
      return false;
    }
  }
  if (preferences.begin("wifi", true)) {
    network_.ssid = preferences.getString("ssid", "");
    network_.password = preferences.getString("pass", "");
    String oldHost = preferences.getString("host", "");
    preferences.end();
    if (oldHost.length()) {
      String error;
      if (!parseServerUrl(oldHost, network_, error)) return false;
    }
  }
  return true;
}

String DeviceConfig::serverUrl() const {
  if (!network_.host.length()) return "";
  return String(network_.tls ? "https://" : "http://") + network_.host + ":" + String(network_.port);
}

String DeviceConfig::setupSsid() const { return "CyberMiao-" + deviceId_.substring(deviceId_.length() - 6); }

bool DeviceConfig::saveNetwork(const DeviceNetworkConfig& config, String& error) {
  if (!config.ssid.length() || config.ssid.length() > 32 || config.password.length() > 64) {
    error = "Wi-Fi 名称需为 1～32 字节，密码不能超过 64 字节"; return false;
  }
  if (!validHost(config.host) || !config.port) { error = "服务器配置无效"; return false; }
  if (config.accounts && !hasCredential()) { error = "请先通过 USB 配置设备身份，再选择账号模式"; return false; }
  cJSON* object = cJSON_CreateObject();
  if (!object) { error = "内存不足"; return false; }
  cJSON_AddNumberToObject(object, "version", 1);
  cJSON_AddStringToObject(object, "ssid", config.ssid.c_str());
  cJSON_AddStringToObject(object, "password", config.password.c_str());
  String url = String(config.tls ? "https://" : "http://") + config.host + ":" + String(config.port);
  cJSON_AddStringToObject(object, "server_url", url.c_str());
  cJSON_AddBoolToObject(object, "accounts", config.accounts);
  return putJson("cmiao_cfg", "network", object, error);
}

bool DeviceConfig::saveCredential(const String& deviceId, const String& credential, String& error) {
  if (deviceId != deviceId_) { error = "凭证设备编号与当前芯片不一致"; return false; }
  if (!validCredential(credential)) { error = "设备凭证格式无效"; return false; }
  cJSON* object = cJSON_CreateObject();
  if (!object) { error = "内存不足"; return false; }
  cJSON_AddNumberToObject(object, "version", 1);
  cJSON_AddStringToObject(object, "device_id", deviceId.c_str());
  cJSON_AddStringToObject(object, "device_token", credential.c_str());
  return putJson("cmiao_id", "identity", object, error);
}
