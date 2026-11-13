#include "DeviceOta.h"

#if CYBERMIAO_OTA_ENABLED
#include <HTTPClient.h>
#include <NetworkClientSecure.h>
#include <Preferences.h>
#include <WiFi.h>
#include <esp_ota_ops.h>
#include <esp_app_desc.h>
#include <esp_task_wdt.h>
#include <mbedtls/base64.h>
#include <mbedtls/pk.h>
#include <mbedtls/sha256.h>
#include "DeviceLink.h"
#include "OtaPublicKey.h"
#include "OtaRecovery.h"
#if !CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE
#error "OTA 必须使用启用应用回退的 ESP32 核心与 bootloader"
#endif

// 核心默认提前确认新程序；延迟到硬件与 HTTPS 自检完成。
extern "C" bool verifyRollbackLater() { return true; }

namespace {
constexpr const char* HARDWARE = "cybermiao-s3-8m-opi-same-wiring";
constexpr const char* LAYOUT = "ab-2m-v1";
String text(cJSON* value, const char* key) {
  cJSON* item = cJSON_GetObjectItemCaseSensitive(value, key);
  return cJSON_IsString(item) && item->valuestring ? String(item->valuestring) : String();
}
int number(cJSON* value, const char* key) {
  cJSON* item = cJSON_GetObjectItemCaseSensitive(value, key);
  return cJSON_IsNumber(item) && item->valuedouble == item->valueint ? item->valueint : -1;
}
bool hexValid(const String& value, size_t size) {
  if (value.length() != size) return false;
  for (char c : value) if (!strchr("0123456789abcdef", c)) return false;
  return true;
}
bool atomValid(const String& value, size_t maximum) {
  if (!value.length() || value.length() > maximum) return false;
  for (char c : value) if (!isalnum(static_cast<unsigned char>(c)) && c != '.' && c != '-' && c != '_') return false;
  return true;
}
int slotIndex(const esp_partition_t* slot) { return slot && slot->subtype == ESP_PARTITION_SUBTYPE_APP_OTA_1 ? 1 : 0; }
bool rollbackReady() {
  const esp_partition_t* target = esp_ota_get_next_update_partition(nullptr);
  esp_ota_img_states_t state;
  return target && esp_ota_get_state_partition(target, &state) == ESP_OK && state == ESP_OTA_IMG_VALID;
}
bool compatibleLayout() {
  const esp_partition_t* first = esp_partition_find_first(ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_APP_OTA_0, nullptr);
  const esp_partition_t* second = esp_partition_find_first(ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_APP_OTA_1, nullptr);
  return first && second && first->address == 0x10000 && second->address == 0x210000 && first->size == 0x200000 && second->size == 0x200000;
}
bool beginHttp(const DeviceConfig& config, const String& path, HTTPClient& http, NetworkClientSecure& transport) {
  if (!config.network().tls || !DeviceLink::clockReady()) return false;
  transport.useBuiltinCACertBundle();
  transport.setHandshakeTimeout(8);
  http.setConnectTimeout(5000);
  http.setTimeout(10000);
  http.setFollowRedirects(HTTPC_DISABLE_FOLLOW_REDIRECTS);
  http.useHTTP10(true);
  if (!http.begin(transport, config.serverUrl() + path)) return false;
  http.addHeader("Authorization", config.authorization());
  return true;
}
}  // namespace

void DeviceOta::begin() {
  Preferences saved;
  if (saved.begin("cmiao_ota", true)) {
    const String record = saved.getString("record", "");
    cJSON* root = record.length() <= 2048 ? cJSON_Parse(record.c_str()) : nullptr;
    if (root) {
      command_ = text(root, "command"); state_ = text(root, "phase");
      targetVersion_ = text(root, "target"); operation_ = text(root, "operation");
      slotVersions_[0] = text(root, "app0"); slotVersions_[1] = text(root, "app1");
      cJSON_Delete(root);
    }
    saved.end();
  }
  const esp_partition_t* running = esp_ota_get_running_partition();
  esp_ota_img_states_t status;
  pending_ = running && esp_ota_get_state_partition(running, &status) == ESP_OK && status == ESP_OTA_IMG_PENDING_VERIFY;
  slotVersions_[slotIndex(running)] = CYBERMIAO_FIRMWARE_VERSION;
  previousVersion_ = slotVersions_[1 - slotIndex(running)];
  returning_ = operation_ == "rollback" || (targetVersion_.length() && targetVersion_ != CYBERMIAO_FIRMWARE_VERSION);
  switch (recoverOtaBoot(state_.c_str(), targetVersion_ == CYBERMIAO_FIRMWARE_VERSION, pending_, operation_ == "rollback")) {
    case OtaRecovery::Validate: setState("pending_validation", 98); break;
    case OtaRecovery::Updated: setState("updated", 100); break;
    case OtaRecovery::RolledBack: setState("rolled_back", 100); break;
    case OtaRecovery::Interrupted: setState("failed", 0); error_ = "power_interrupted"; break;
    default: if (!state_.length()) state_ = "idle"; break;
  }
  if (pending_) {
    esp_task_wdt_config_t watchdog = {120000, 0, true};
    if (esp_task_wdt_reconfigure(&watchdog) == ESP_ERR_INVALID_STATE) esp_task_wdt_init(&watchdog);
  }
  if (xTaskCreatePinnedToCore(task, "device_ota", 12288, this, 1, nullptr, 0) != pdPASS) {
    error_ = "task_start_failed";
    if (pending_) esp_ota_mark_app_invalid_rollback_and_reboot();
  }
}

void DeviceOta::activity(bool busy, uint32_t idleSeconds, bool healthy) {
  busy_ = busy; idleSeconds_ = idleSeconds; healthy_ = healthy; activityReady_ = true;
}
void DeviceOta::task(void* value) {
  auto* self = static_cast<DeviceOta*>(value);
  if (self->pending_) esp_task_wdt_add(nullptr);
  for (;;) {
    self->tick();
    if (self->pending_) esp_task_wdt_reset();
    vTaskDelay(pdMS_TO_TICKS(200));
  }
}

bool DeviceOta::persist() {
  cJSON* root = cJSON_CreateObject();
  if (!root) return false;
  cJSON_AddStringToObject(root, "command", command_.c_str());
  cJSON_AddStringToObject(root, "phase", state_.c_str());
  cJSON_AddStringToObject(root, "target", targetVersion_.c_str());
  cJSON_AddStringToObject(root, "operation", operation_.c_str());
  cJSON_AddStringToObject(root, "app0", slotVersions_[0].c_str());
  cJSON_AddStringToObject(root, "app1", slotVersions_[1].c_str());
  char* record = cJSON_PrintUnformatted(root); cJSON_Delete(root);
  if (!record) return false;
  Preferences saved;
  // 一个 NVS 字符串原子替换，不能让 phase 和 target 来自不同次写入。
  bool ok = saved.begin("cmiao_ota", false) && saved.putString("record", record) == strlen(record);
  saved.end(); cJSON_free(record); return ok;
}
void DeviceOta::fail(const char* error) {
  setState("failed"); error_ = error; updating_ = false; persist();
  nextCheck_ = millis() + 3000;
}
void DeviceOta::showScreen(OtaScreen::Phase phase, int progress) {
  const uint32_t next = OtaScreen::encode(phase, progress);
  if ((screenState_.load() & 255U) != static_cast<uint32_t>(phase)) screenChangedAt_ = millis();
  screenState_ = next;
}
void DeviceOta::setState(const char* state, int progress) {
  state_ = state;
  if (progress >= 0) progress_ = progress;
  showScreen(OtaScreen::phaseFor(state, operation_ == "rollback"), progress_.load());
}

void DeviceOta::tick() {
  if (pending_) {
    if (!healthy_) healthySince_ = 0;
    else if (!healthySince_) healthySince_ = millis();
    // 包括配网、外设初始化或 HTTPS 无法通过的情况，均不能无限待确认。
    if (millis() > 180000) {
      showScreen(OtaScreen::Phase::RollingBack, 0);
      vTaskDelay(pdMS_TO_TICKS(700));
      esp_ota_mark_app_invalid_rollback_and_reboot();
    }
  }
  if (!activityReady_ || !compatibleLayout() || !config_.hasCredential() || !config_.network().accounts) return;
  if (WiFi.status() != WL_CONNECTED || !DeviceLink::clockReady()) return;
  const uint32_t now = millis();
  if (nextCheck_ && static_cast<int32_t>(now - nextCheck_) < 0) return;
  nextCheck_ = now + 10000;
  exchange();
}

bool DeviceOta::exchange() {
  authorized_ = false;
  const esp_partition_t* running = esp_ota_get_running_partition();
  cJSON* report = cJSON_CreateObject();
  if (!report) return false;
  cJSON_AddStringToObject(report, "version", CYBERMIAO_FIRMWARE_VERSION);
  cJSON_AddNumberToObject(report, "sequence", CYBERMIAO_FIRMWARE_SEQUENCE);
  cJSON_AddBoolToObject(report, "ota_capable", compatibleLayout());
  cJSON_AddStringToObject(report, "slot", running ? running->label : "unknown");
  cJSON_AddStringToObject(report, "previous_version", previousVersion_.c_str());
  cJSON_AddBoolToObject(report, "rollback_available", !pending_ && rollbackReady() && previousVersion_.length() && previousVersion_ != CYBERMIAO_FIRMWARE_VERSION);
  cJSON_AddStringToObject(report, "state", state_.c_str());
  cJSON_AddNumberToObject(report, "progress", progress_.load());
  cJSON_AddStringToObject(report, "command_id", command_.c_str());
  cJSON_AddStringToObject(report, "error_code", error_.c_str());
  cJSON_AddBoolToObject(report, "busy", busy_.load());
  cJSON_AddNumberToObject(report, "idle_seconds", idleSeconds_.load());
  char* body = cJSON_PrintUnformatted(report); cJSON_Delete(report);
  if (!body) return false;
  String result; int status = -1;
  { // 先销毁 TLS 对象，随后下载时只占一套握手缓冲。
    NetworkClientSecure transport; HTTPClient http;
    if (beginHttp(config_, "/api/firmware/devices/" + config_.deviceId() + "/check", http, transport)) {
      http.addHeader("Content-Type", "application/json");
      status = http.POST(reinterpret_cast<uint8_t*>(body), strlen(body));
      if (status == 200 && http.getSize() > 0 && http.getSize() <= 8192) result = http.getString();
      http.end();
    }
  }
  cJSON_free(body);
  if (status != 200 || !result.length()) return false;
  cJSON* root = cJSON_Parse(result.c_str());
  if (!cJSON_IsObject(root) || !cJSON_HasObjectItem(root, "command")) { cJSON_Delete(root); return false; }
  if (pending_ && healthySince_ && millis() - healthySince_ >= 30000) {
    if (esp_ota_mark_app_valid_cancel_rollback() == ESP_OK) {
      esp_task_wdt_delete(nullptr); pending_ = false; confirmed_ = true;
      setState(returning_ ? "rolled_back" : "updated", 100); persist(); nextCheck_ = millis() + 1000;
    }
    cJSON_Delete(root); return true;
  }
  cJSON* command = cJSON_GetObjectItemCaseSensitive(root, "command");
  const String id = text(command, "id"), action = text(command, "action");
  authorized_ = hexValid(id, 32) && id == command_ && action == operation_;
  if (pending_ || !hexValid(id, 32) || busy_ || idleSeconds_ < 5 || updating_) { cJSON_Delete(root); return true; }
  if (id == command_ && (state_ == "updated" || state_ == "failed" || state_ == "rolled_back")) { cJSON_Delete(root); return true; }
  command_ = id; operation_ = action; error_ = ""; progress_ = 0;
  updating_ = true; state_ = "downloading";
  showScreen(action == "rollback" ? OtaScreen::Phase::RollingBack : OtaScreen::Phase::Preparing, 0);
  vTaskDelay(pdMS_TO_TICKS(700)); // 让业务任务先关闭 WS 与音频。
  bool ok = action == "rollback" ? rollback() : action == "install" ? install(cJSON_GetObjectItemCaseSensitive(command, "manifest")) : false;
  if (!ok && state_ != "failed") fail("invalid_command");
  cJSON_Delete(root); return true;
}

bool DeviceOta::install(cJSON* manifest) {
  const String id = text(manifest, "id"), version = text(manifest, "version"), sha = text(manifest, "sha256"), notesHash = text(manifest, "notes_sha256");
  const int size = number(manifest, "size"), sequence = number(manifest, "sequence");
  if (number(manifest, "schema") != 1 || text(manifest, "profile") != "deer" || text(manifest, "hardware") != HARDWARE || text(manifest, "layout") != LAYOUT || sequence <= CYBERMIAO_FIRMWARE_SEQUENCE || size < 256 || size > 0x200000 || !hexValid(sha, 64) || !hexValid(notesHash, 64) || !atomValid(id, 96) || !atomValid(version, 48)) { fail("manifest_invalid"); return false; }
  const String signedText = "cybermiao-ota-v1\n" + id + "\ndeer\n" + version + "\n" + String(sequence) + "\n" + sha + "\n" + String(size) + "\n" + HARDWARE + "\n" + LAYOUT + "\n" + notesHash + "\n";
  unsigned char signature[96], expected[32], hash[32]; size_t signatureSize = 0;
  const String encoded = text(manifest, "signature");
  if (encoded.length() > 128 || mbedtls_base64_decode(signature, sizeof(signature), &signatureSize, reinterpret_cast<const unsigned char*>(encoded.c_str()), encoded.length()) != 0) { fail("signature_invalid"); return false; }
  mbedtls_sha256(reinterpret_cast<const unsigned char*>(signedText.c_str()), signedText.length(), hash, 0);
  mbedtls_pk_context key; mbedtls_pk_init(&key);
  int verified = mbedtls_pk_parse_public_key(&key, reinterpret_cast<const unsigned char*>(CYBERMIAO_OTA_PUBLIC_KEY), sizeof(CYBERMIAO_OTA_PUBLIC_KEY));
  if (!verified) verified = mbedtls_pk_verify(&key, MBEDTLS_MD_SHA256, hash, sizeof(hash), signature, signatureSize);
  mbedtls_pk_free(&key);
  if (verified) { fail("signature_invalid"); return false; }
  for (int i = 0; i < 32; ++i) {
    const char hi = sha[i*2], lo = sha[i*2+1];
    expected[i] = static_cast<unsigned char>((hi <= '9' ? hi-'0' : hi-'a'+10) * 16 + (lo <= '9' ? lo-'0' : lo-'a'+10));
  }
  targetVersion_ = version;
  if (!persist()) { fail("nvs_failed"); return false; }
  const esp_partition_t* destination = esp_ota_get_next_update_partition(nullptr);
  if (!destination) { fail("flash_begin_failed"); return false; }
  // 新下载将覆盖备用槽，先撤下其旧版本标记。中途掉电不能把半份镜像显示为可回退版。
  slotVersions_[slotIndex(destination)] = ""; previousVersion_ = "";
  if (!persist()) { fail("nvs_failed"); return false; }
  esp_ota_handle_t handle = 0; bool started = false; int received = 0;
  mbedtls_sha256_context digest; mbedtls_sha256_init(&digest); mbedtls_sha256_starts(&digest, 0);
  {
    NetworkClientSecure transport; HTTPClient http;
    if (!beginHttp(config_, "/api/firmware/devices/" + config_.deviceId() + "/download/" + command_, http, transport) || http.GET() != 200 || http.getSize() != size) {
      http.end(); mbedtls_sha256_free(&digest); fail("download_failed"); return false;
    }
    if (!destination || destination->size < static_cast<size_t>(size) || esp_ota_begin(destination, size, &handle) != ESP_OK) {
      http.end(); mbedtls_sha256_free(&digest); fail("flash_begin_failed"); return false;
    }
    started = true;
    setState("downloading", 0);
    uint8_t buffer[4096]; uint32_t progressAt = millis(), reportedAt = millis();
    while (received < size) {
      NetworkClient* stream = http.getStreamPtr(); int available = stream->available();
      if (!available) {
        if (!stream->connected() || millis() - progressAt > 15000) break;
        vTaskDelay(pdMS_TO_TICKS(5)); continue;
      }
      size_t take = min<size_t>(sizeof(buffer), min<size_t>(available, size - received));
      int count = stream->read(buffer, take);
      if (count <= 0) continue;
      if (esp_ota_write(handle, buffer, count) != ESP_OK) break;
      mbedtls_sha256_update(&digest, buffer, count); received += count;
      progress_ = received * 95 / size; progressAt = millis();
      showScreen(OtaScreen::Phase::Downloading, progress_.load());
      // 进度上报失败不阻断下载；写启动槽前必须再次成功核验授权。
      if (millis() - reportedAt >= 5000) { exchange(); reportedAt = millis(); }
      vTaskDelay(pdMS_TO_TICKS(1));
    }
    http.end();
  }
  mbedtls_sha256_finish(&digest, hash); mbedtls_sha256_free(&digest);
  if (!started || received != size || memcmp(expected, hash, 32)) { if (started) esp_ota_abort(handle); fail("download_integrity_failed"); return false; }
  setState("verifying", 96);
  if (esp_ota_end(handle) != ESP_OK) { fail("image_invalid"); return false; }
  if (!exchange() || !authorized_) { fail("authorization_lost"); return false; }
  slotVersions_[slotIndex(destination)] = version;
  setState("rebooting", 98);
  // 先原子保存启动意图。任何一步失败都不切换启动槽。
  if (!persist()) { fail("nvs_failed"); return false; }
  if (esp_ota_set_boot_partition(destination) != ESP_OK) { fail("boot_select_failed"); return false; }
  exchange(); vTaskDelay(pdMS_TO_TICKS(700)); ESP.restart(); return true;
}

bool DeviceOta::rollback() {
  const esp_partition_t* previous = esp_ota_get_next_update_partition(nullptr);
  if (!rollbackReady() || !previous || !previousVersion_.length() || previousVersion_ == CYBERMIAO_FIRMWARE_VERSION) { fail("rollback_unavailable"); return false; }
  esp_app_desc_t description;
  if (esp_ota_get_partition_description(previous, &description) != ESP_OK) { fail("rollback_invalid"); return false; }
  targetVersion_ = previousVersion_; setState("rebooting", 98);
  if (!exchange() || !authorized_) { fail("authorization_lost"); return false; }
  if (!persist()) { fail("nvs_failed"); return false; }
  if (esp_ota_set_boot_partition(previous) != ESP_OK) { fail("rollback_failed"); return false; }
  vTaskDelay(pdMS_TO_TICKS(700)); ESP.restart(); return true;
}

void DeviceOta::diagnostics(cJSON* object) const {
  cJSON_AddBoolToObject(object, "ota_capable", compatibleLayout());
  cJSON_AddBoolToObject(object, "ota_updating", updating_.load());
  cJSON_AddNumberToObject(object, "ota_progress", progress_.load());
  cJSON_AddStringToObject(object, "firmware_version", CYBERMIAO_FIRMWARE_VERSION);
  cJSON_AddNumberToObject(object, "firmware_sequence", CYBERMIAO_FIRMWARE_SEQUENCE);
  const OtaScreen::Snapshot visible = screen();
  cJSON_AddBoolToObject(object, "ota_screen_visible", visible.phase != OtaScreen::Phase::Hidden);
  cJSON_AddNumberToObject(object, "ota_screen_phase", static_cast<unsigned>(visible.phase));
}
#else
void DeviceOta::begin() {}
void DeviceOta::activity(bool, uint32_t, bool) {}
void DeviceOta::diagnostics(cJSON* object) const { cJSON_AddBoolToObject(object, "ota_capable", false); }
#endif
