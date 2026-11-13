#pragma once

#include <Arduino.h>
#include <atomic>
#include <cJSON.h>
#include "DeviceConfig.h"
#include "FirmwareVersion.h"

class DeviceOta {
 public:
  explicit DeviceOta(const DeviceConfig& config) : config_(config) {}
  void begin();
  void activity(bool busy, uint32_t idleSeconds, bool healthy);
  bool updating() const { return updating_.load(); }
  void diagnostics(cJSON* object) const;
 private:
  static void task(void* value);
  void tick();
  bool exchange();
  bool install(cJSON* manifest);
  bool rollback();
  bool persist();
  void fail(const char* error);
  const DeviceConfig& config_;
  std::atomic<bool> busy_{true}, healthy_{false}, updating_{false}, activityReady_{false};
  std::atomic<uint32_t> idleSeconds_{0};
  std::atomic<int> progress_{0};
  String state_ = "idle", command_, previousVersion_, error_, targetVersion_, operation_;
  String slotVersions_[2];
  uint32_t nextCheck_ = 0, healthySince_ = 0;
  bool pending_ = false, confirmed_ = false;
  bool returning_ = false;
  bool authorized_ = false;
};
