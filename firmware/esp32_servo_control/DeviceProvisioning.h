#pragma once

#include <Arduino.h>
#include "DeviceConfig.h"

class DeviceProvisioning {
 public:
  DeviceProvisioning(Stream& serial, DeviceConfig& config) : serial_(serial), config_(config) {}
  void poll();
  bool shouldReboot() const;
  bool takeSetupRequest();

 private:
  void command(const String& line);
  Stream& serial_;
  DeviceConfig& config_;
  String buffer_;
  bool discarding_ = false;
  bool setupRequested_ = false;
  bool rebootPending_ = false;
  uint32_t rebootAt_ = 0;
  uint32_t lastByteAt_ = 0;
};
