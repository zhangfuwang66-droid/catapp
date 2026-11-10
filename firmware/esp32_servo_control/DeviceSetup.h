#pragma once

#include <DNSServer.h>
#include <WebServer.h>
#include <cJSON.h>
#include "DeviceConfig.h"
#include "DeviceLink.h"
#include "SetupSession.h"

// 只在受密码保护的设备 AP 接口提供设置，不向普通局域网暴露配对码。
class DeviceSetup {
 public:
  DeviceSetup(DeviceConfig& config, DeviceLink& link) : config_(config), link_(link) {}
  bool start(bool manual);
  void poll();
  void closeAutomatic();
  bool active() const { return active_; }
  bool holdsNetwork() const;
  bool shouldReboot() const;

 private:
  void routes();
  bool allowRead();
  bool allowWrite();
  void json(int code, cJSON* object);
  void error(int code, const String& message);
  void status();
  void configure();
  void scan();
  DeviceConfig& config_;
  DeviceLink& link_;
  WebServer server_{80};
  DNSServer dns_;
  String csrf_;
  bool initialized_ = false;
  bool active_ = false;
  bool manual_ = false;
  SetupSession session_;
  bool stationPaused_ = false;
  String saveRequestId_;
};
