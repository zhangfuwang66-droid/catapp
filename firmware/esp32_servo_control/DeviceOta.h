#pragma once

#include <Arduino.h>
#include <atomic>
#include <cJSON.h>
#include "DeviceConfig.h"
#include "FirmwareVersion.h"
#include "OtaScreenState.h"

class DeviceOta {
 public:
  explicit DeviceOta(const DeviceConfig& config) : config_(config) {}
  void begin();
  void activity(bool busy, uint32_t idleSeconds, bool healthy);
  bool updating() const { return updating_.load(); }
  OtaScreen::Snapshot screen() const {
    const uint32_t value = screenState_.load();
    return OtaScreen::decode(value, screenChangedAt_.load(), millis());
  }
  bool screenActive() const { return screen().phase != OtaScreen::Phase::Hidden; }
  void diagnostics(cJSON* object) const;
 private:
  static void task(void* value);
  void tick();
  bool exchange();
  bool install(cJSON* manifest);
  bool rollback();
  bool persist();
  void fail(const char* error);
  void setState(const char* state, int progress = -1);
  void showScreen(OtaScreen::Phase phase, int progress);
  const DeviceConfig& config_;
  std::atomic<bool> busy_{true}, healthy_{false}, updating_{false}, activityReady_{false};
  std::atomic<uint32_t> idleSeconds_{0};
  std::atomic<int> progress_{0};
  // 屏幕任务只读原子快照，不跨核访问 OTA 任务持有的 String。
  std::atomic<uint32_t> screenState_{0}, screenChangedAt_{0};
  String state_ = "idle", command_, previousVersion_, error_, targetVersion_, operation_;
  String slotVersions_[2];
  uint32_t nextCheck_ = 0, healthySince_ = 0;
  bool pending_ = false, confirmed_ = false;
  bool returning_ = false;
  bool authorized_ = false;
};
