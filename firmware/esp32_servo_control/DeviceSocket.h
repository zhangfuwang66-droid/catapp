#pragma once

#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include "DeviceTransport.h"

class DeviceSocketLock {
 public:
  explicit DeviceSocketLock(SemaphoreHandle_t mutex) : mutex_(mutex) {
    locked_ = mutex_ && xSemaphoreTakeRecursive(mutex_, portMAX_DELAY) == pdTRUE;
  }
  ~DeviceSocketLock() { if (locked_) xSemaphoreGiveRecursive(mutex_); }
  explicit operator bool() const { return locked_; }
  DeviceSocketLock(const DeviceSocketLock&) = delete;
  DeviceSocketLock& operator=(const DeviceSocketLock&) = delete;

 private:
  SemaphoreHandle_t mutex_;
  bool locked_ = false;
};

// 一次锁住完整 WebSocket 操作（帧头、载荷及内部 TLS 状态）。
// 递归锁允许连接/消息回调在同一任务中回复，不让其他任务插入半帧数据。
class DeviceSocket {
 public:
  explicit DeviceSocket(const DeviceConfig& config)
      : client_(std::make_shared<DeviceWebsocketTransport>(config)) {}
  DeviceSocket(const DeviceSocket&) = delete;
  DeviceSocket& operator=(const DeviceSocket&) = delete;
  bool begin() {
    if (!mutex_) mutex_ = xSemaphoreCreateRecursiveMutex();
    return mutex_ != nullptr;
  }
  // 下列配置只在 setup 阶段调用，创建业务任务前完成。
  void addHeader(const String& key, const String& value) { client_.addHeader(key, value); }
  void onEvent(websockets::PartialEventCallback callback) { client_.onEvent(callback); }
  void onMessage(websockets::PartialMessageCallback callback) { client_.onMessage(callback); }
  bool connect(const String& host, int port, const String& path) {
    DeviceSocketLock lock(mutex_);
    return lock && client_.connect(host, port, path);
  }
  bool available() { DeviceSocketLock lock(mutex_); return lock && client_.available(); }
  bool poll() { DeviceSocketLock lock(mutex_); return lock && client_.poll(); }
  bool send(const String& value) { DeviceSocketLock lock(mutex_); return lock && client_.send(value); }
  bool sendBinary(const char* bytes, size_t size) {
    DeviceSocketLock lock(mutex_);
    return lock && client_.sendBinary(bytes, size);
  }
  void close() { DeviceSocketLock lock(mutex_); if (lock) client_.close(); }

 private:
  websockets::WebsocketsClient client_;
  SemaphoreHandle_t mutex_ = nullptr;
};
