#pragma once

#include <Arduino.h>

struct DeviceNetworkConfig {
  String ssid;
  String password;
  String host;
  uint16_t port = 8081;
  bool tls = false;
  bool accounts = false;
};

// 本次启动的配置保持不变。保存新配置后重启，避免网络任务并发读取被修改的 String。
class DeviceConfig {
 public:
  bool begin();
  const String& deviceId() const { return deviceId_; }
  const DeviceNetworkConfig& network() const { return network_; }
  bool hasCredential() const { return credential_.length() >= 32; }
  String authorization() const { return "Bearer " + credential_; }
  String serverUrl() const;
  const String& setupPassword() const { return setupPassword_; }
  String setupSsid() const;
  bool saveNetwork(const DeviceNetworkConfig& config, String& error);
  bool saveCredential(const String& deviceId, const String& credential, String& error);
  static bool parseServerUrl(String url, DeviceNetworkConfig& config, String& error);
  static bool validCredential(const String& credential);

 private:
  String deviceId_;
  String credential_;
  String setupPassword_;
  DeviceNetworkConfig network_;
};
