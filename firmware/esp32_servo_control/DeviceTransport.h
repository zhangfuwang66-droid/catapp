#pragma once

#include <ArduinoWebsockets.h>
#include <NetworkClient.h>
#include <NetworkClientSecure.h>
#include <tiny_websockets/network/tcp_client.hpp>
#include <atomic>
#include <cJSON.h>
#include "DeviceConfig.h"

// 由同一个传输对象选择明文或校验证书的 TLS，避免库内自动升级使用不同信任配置。
class DeviceWebsocketTransport : public websockets::network::TcpClient {
 public:
  explicit DeviceWebsocketTransport(const DeviceConfig& config) : config_(config) {
    secure_.useBuiltinCACertBundle();
    secure_.setHandshakeTimeout(8);
  }
  bool connect(const websockets::WSString& host, int port) override {
    ++connectAttempts_;
    connectPending_ = true;
    lastHttpStatus_ = 0;
    lastTlsError_ = 0;
    active_ = config_.network().tls ? static_cast<NetworkClient*>(&secure_) : &plain_;
    bool connected = active_->connect(host.c_str(), port, 4000);
    connectPending_ = false;
    if (connected) ++connectSuccesses_;
    else recordTlsError();
    if (connected) active_->setNoDelay(true);
    return connected;
  }
  bool poll() override { return active_->available() > 0; }
  bool available() override { return active_->connected(); }
  void send(const websockets::WSString& data) override { send(reinterpret_cast<const uint8_t*>(data.c_str()), data.size()); }
  void send(const websockets::WSString&& data) override { send(reinterpret_cast<const uint8_t*>(data.c_str()), data.size()); }
  void send(const uint8_t* data, uint32_t size) override {
    uint32_t sent = 0;
    uint32_t started = millis();
    while (sent < size && active_->connected() && millis() - started < 5000) {
      size_t count = active_->write(data + sent, size - sent);
      if (!count) break;
      sent += count;
    }
    bytesSent_ += sent;
    if (sent != size) { ++sendFailures_; recordTlsError(); active_->stop(); }
  }
  websockets::WSString readLine() override {
    websockets::WSString line;
    uint32_t started = millis();
    while (active_->connected() && millis() - started < 5000) {
      int value = active_->read();
      if (value < 0) { delay(1); continue; }
      line += static_cast<char>(value);
      ++bytesRead_;
      if (value == '\n') {
        int status = 0;
        if (sscanf(line.c_str(), "HTTP/%*s %d", &status) == 1) lastHttpStatus_ = status;
        return line;
      }
      if (line.length() > 2048) break;
    }
    ++headerFailures_;
    recordTlsError();
    return "";
  }
  uint32_t read(uint8_t* buffer, uint32_t size) override {
    int count = active_->read(buffer, size);
    if (count > 0) bytesRead_ += count;
    if (count <= 0) delay(1);
    return count > 0 ? static_cast<uint32_t>(count) : 0;
  }
  void close() override { active_->stop(); }
  // 不触碰 socket、不等待业务锁，即使连接任务卡住也能通过 USB 读到快照。
  void addDiagnostics(cJSON* object) const {
    cJSON_AddNumberToObject(object, "connect_attempts", connectAttempts_.load());
    cJSON_AddNumberToObject(object, "tcp_connect_successes", connectSuccesses_.load());
    cJSON_AddBoolToObject(object, "connect_pending", connectPending_.load());
    cJSON_AddNumberToObject(object, "last_tls_error", lastTlsError_.load());
    cJSON_AddNumberToObject(object, "last_http_status", lastHttpStatus_.load());
    cJSON_AddNumberToObject(object, "bytes_sent", bytesSent_.load());
    cJSON_AddNumberToObject(object, "bytes_read", bytesRead_.load());
    cJSON_AddNumberToObject(object, "send_failures", sendFailures_.load());
    cJSON_AddNumberToObject(object, "header_failures", headerFailures_.load());
  }

 protected:
  int getSocket() const override { return -1; }

 private:
  void recordTlsError() {
    if (config_.network().tls) { char ignored[1]; lastTlsError_ = secure_.lastError(ignored, sizeof(ignored)); }
  }
  const DeviceConfig& config_;
  NetworkClient plain_;
  NetworkClientSecure secure_;
  NetworkClient* active_ = &plain_;
  std::atomic<uint32_t> connectAttempts_{0}, connectSuccesses_{0}, bytesSent_{0}, bytesRead_{0};
  std::atomic<uint32_t> sendFailures_{0}, headerFailures_{0};
  std::atomic<int> lastTlsError_{0}, lastHttpStatus_{0};
  std::atomic<bool> connectPending_{false};
};
