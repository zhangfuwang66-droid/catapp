#include "DeviceLink.h"

#include <HTTPClient.h>
#include <NetworkClientSecure.h>
#include <WiFi.h>
#include <cJSON.h>
#include <ctime>

namespace {
struct JsonResponse {
  int status = -1;
  String body;
};

JsonResponse request(const DeviceConfig& config, const String& path, bool post) {
  NetworkClient plain;
  NetworkClientSecure secure;
  secure.useBuiltinCACertBundle();
  secure.setHandshakeTimeout(8);
  NetworkClient& transport = config.network().tls ? static_cast<NetworkClient&>(secure) : plain;
  HTTPClient http;
  http.setConnectTimeout(4000);
  http.setTimeout(5000);
  http.setFollowRedirects(HTTPC_DISABLE_FOLLOW_REDIRECTS);
  JsonResponse response;
  if (!http.begin(transport, config.serverUrl() + path)) return response;
  http.addHeader("Authorization", config.authorization());
  response.status = post ? http.POST("") : http.GET();
  int size = http.getSize();
  if (response.status == 200 && size > 0 && size <= 2048) response.body = http.getString();
  http.end();
  return response;
}

bool validCode(const String& code) {
  if (code.length() != 9 || code[4] != '-') return false;
  for (size_t i = 0; i < code.length(); ++i) {
    if (i == 4) continue;
    if (!strchr("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", code[i])) return false;
  }
  return true;
}
}  // namespace

bool DeviceLink::clockReady() { return time(nullptr) >= 1704067200; }

const char* DeviceLink::statusName(DeviceLinkStatus status) {
  switch (status) {
    case DeviceLinkStatus::Local: return "local";
    case DeviceLinkStatus::WifiOffline: return "wifi_offline";
    case DeviceLinkStatus::NeedCredential: return "need_credential";
    case DeviceLinkStatus::WaitingClock: return "waiting_clock";
    case DeviceLinkStatus::ServerOffline: return "server_offline";
    case DeviceLinkStatus::Unbound: return "unbound";
    case DeviceLinkStatus::Bound: return "bound";
    case DeviceLinkStatus::CredentialInvalid: return "credential_invalid";
  }
  return "unknown";
}

bool DeviceLink::begin() {
  mutex_ = xSemaphoreCreateMutex();
  if (!mutex_) return false;
  return xTaskCreatePinnedToCore(task, "device_link", 8192, this, 1, nullptr, 0) == pdPASS;
}

void DeviceLink::task(void* value) {
  auto* self = static_cast<DeviceLink*>(value);
  for (;;) { self->tick(); vTaskDelay(pdMS_TO_TICKS(100)); }
}

void DeviceLink::setStatus(DeviceLinkStatus status, int bound) {
  if (!mutex_) return;
  xSemaphoreTake(mutex_, portMAX_DELAY);
  state_.updateStatus(status, bound);
  if (bound == 1 || status == DeviceLinkStatus::CredentialInvalid) { state_.pairingCode = ""; codeExpiresAt_ = 0; }
  xSemaphoreGive(mutex_);
}

void DeviceLink::completeBinding(bool bound, uint32_t startedRevision) {
  xSemaphoreTake(mutex_, portMAX_DELAY);
  state_.completeVerification(bound, startedRevision);
  if (bound) { state_.pairingCode = ""; codeExpiresAt_ = 0; }
  xSemaphoreGive(mutex_);
}

void DeviceLink::recordHttpStatus(int status) {
  xSemaphoreTake(mutex_, portMAX_DELAY);
  state_.lastHttpStatus = status;
  xSemaphoreGive(mutex_);
}

DeviceLinkSnapshot DeviceLink::snapshot() {
  if (!mutex_) return {};
  xSemaphoreTake(mutex_, portMAX_DELAY);
  DeviceLinkSnapshot result = state_;
  result.expiresIn = result.canDisplayPairing()
      ? pairingSecondsRemaining(millis(), codeExpiresAt_, result.pairingCode.length() > 0) : 0;
  if (!result.expiresIn) result.pairingCode = "";
  xSemaphoreGive(mutex_);
  return result;
}

bool DeviceLink::allowBusiness() {
  if (config_.network().tls && !clockReady()) return false;
  if (!config_.network().accounts) return true;
  return config_.hasCredential() && snapshot().businessAllowed();
}

void DeviceLink::requestPairing() {
  if (!mutex_) return;
  xSemaphoreTake(mutex_, portMAX_DELAY);
  refreshPairing_ = true;
  xSemaphoreGive(mutex_);
}

void DeviceLink::refreshBinding() {
  if (!mutex_) return;
  xSemaphoreTake(mutex_, portMAX_DELAY);
  refreshBinding_ = true;
  state_.requestVerification();
  xSemaphoreGive(mutex_);
}

void DeviceLink::tick() {
  if (WiFi.status() != WL_CONNECTED) { setStatus(DeviceLinkStatus::WifiOffline); nextPollAt_ = 0; return; }
  if (!timeRequested_) {
    configTime(0, 0, "ntp.aliyun.com", "time.cloudflare.com", "pool.ntp.org");
    timeRequested_ = true;
  }
  if (config_.network().tls && !clockReady()) { setStatus(DeviceLinkStatus::WaitingClock); nextPollAt_ = 0; return; }
  if (!config_.network().accounts) { setStatus(DeviceLinkStatus::Local); return; }
  if (!config_.hasCredential()) { setStatus(DeviceLinkStatus::NeedCredential, 0); return; }
  uint32_t now = millis();
  xSemaphoreTake(mutex_, portMAX_DELAY);
  if (refreshBinding_ && now - lastBindingPollAt_ >= 5000) {
    nextPollAt_ = 0;
    refreshBinding_ = false;
  }
  xSemaphoreGive(mutex_);
  if (nextPollAt_ && static_cast<int32_t>(now - nextPollAt_) < 0) return;
  uint32_t startedRevision = snapshot().verificationRevision;
  lastBindingPollAt_ = now;
  nextPollAt_ = now + 5000;
  String prefix = "/api/devices/" + config_.deviceId();
  JsonResponse response = request(config_, prefix + "/binding", false);
  recordHttpStatus(response.status);
  if (response.status == 401 || response.status == 403) {
    setStatus(DeviceLinkStatus::CredentialInvalid, 0); nextPollAt_ = millis() + 30000; return;
  }
  cJSON* body = cJSON_ParseWithOpts(response.body.c_str(), nullptr, true);
  cJSON* bound = cJSON_GetObjectItemCaseSensitive(body, "bound");
  if (response.status != 200 || !cJSON_IsBool(bound)) {
    cJSON_Delete(body); setStatus(DeviceLinkStatus::ServerOffline); nextPollAt_ = millis() + 15000; return;
  }
  bool isBound = cJSON_IsTrue(bound);
  cJSON_Delete(body);
  completeBinding(isBound, startedRevision);
  if (isBound) { nextPollAt_ = millis() + 30000; return; }
  DeviceLinkSnapshot current = snapshot();
  xSemaphoreTake(mutex_, portMAX_DELAY);
  bool refresh = refreshPairing_;
  xSemaphoreGive(mutex_);
  now = millis();
  if ((!current.pairingCode.length() || refresh) && (!hasRequestedPair_ || now - lastPairAt_ >= 30000)) {
    lastPairAt_ = now; hasRequestedPair_ = true;
    response = request(config_, prefix + "/pairing", true);
    recordHttpStatus(response.status);
    if (response.status == 401 || response.status == 403) {
      setStatus(DeviceLinkStatus::CredentialInvalid, 0); nextPollAt_ = millis() + 30000; return;
    }
    body = cJSON_ParseWithOpts(response.body.c_str(), nullptr, true);
    cJSON* code = cJSON_GetObjectItemCaseSensitive(body, "code");
    cJSON* expires = cJSON_GetObjectItemCaseSensitive(body, "expires_in");
    if (response.status == 200 && cJSON_IsString(code) && validCode(code->valuestring) && cJSON_IsNumber(expires)
        && expires->valueint > 0 && expires->valueint <= 300) {
      xSemaphoreTake(mutex_, portMAX_DELAY);
      state_.pairingCode = code->valuestring;
      codeExpiresAt_ = millis() + static_cast<uint32_t>(expires->valueint) * 1000;
      refreshPairing_ = false;
      xSemaphoreGive(mutex_);
    }
    cJSON_Delete(body);
  }
}
