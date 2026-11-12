#pragma once

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include "DeviceConfig.h"
#include "DeviceLinkState.h"

struct DeviceLinkSnapshot : DeviceLinkState {
  String pairingCode;
  uint32_t expiresIn = 0;
  int lastHttpStatus = 0;
};

class DeviceLink {
 public:
  explicit DeviceLink(const DeviceConfig& config) : config_(config) {}
  bool begin();
  DeviceLinkSnapshot snapshot();
  bool allowBusiness();
  void requestPairing();
  void refreshBinding();
  static const char* statusName(DeviceLinkStatus status);
  static bool clockReady();

 private:
  static void task(void* self);
  void tick();
  void setStatus(DeviceLinkStatus status, int bound = -1);
  void completeBinding(bool bound, uint32_t startedRevision);
  void recordHttpStatus(int status);
  const DeviceConfig& config_;
  SemaphoreHandle_t mutex_ = nullptr;
  DeviceLinkSnapshot state_;
  uint32_t codeExpiresAt_ = 0;
  uint32_t nextPollAt_ = 0;
  uint32_t lastPairAt_ = 0;
  bool hasRequestedPair_ = false;
  bool refreshPairing_ = false;
  bool timeRequested_ = false;
  bool refreshBinding_ = false;
  uint32_t lastBindingPollAt_ = 0;
};
