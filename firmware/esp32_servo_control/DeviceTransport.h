#pragma once

#include <ArduinoWebsockets.h>
#include <NetworkClient.h>
#include <NetworkClientSecure.h>
#include <tiny_websockets/network/tcp_client.hpp>
#include "DeviceConfig.h"

// 由同一个传输对象选择明文或校验证书的 TLS，避免库内自动升级使用不同信任配置。
class DeviceWebsocketTransport : public websockets::network::TcpClient {
 public:
  explicit DeviceWebsocketTransport(const DeviceConfig& config) : config_(config) {
    secure_.useBuiltinCACertBundle();
    secure_.setHandshakeTimeout(8);
  }
  bool connect(const websockets::WSString& host, int port) override {
    active_ = config_.network().tls ? static_cast<NetworkClient*>(&secure_) : &plain_;
    bool connected = active_->connect(host.c_str(), port, 4000);
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
    if (sent != size) active_->stop();
  }
  websockets::WSString readLine() override {
    websockets::WSString line;
    uint32_t started = millis();
    while (active_->connected() && millis() - started < 5000) {
      int value = active_->read();
      if (value < 0) { delay(1); continue; }
      line += static_cast<char>(value);
      if (value == '\n') return line;
      if (line.length() > 2048) break;
    }
    return "";
  }
  uint32_t read(uint8_t* buffer, uint32_t size) override {
    int count = active_->read(buffer, size);
    if (count <= 0) delay(1);
    return count > 0 ? static_cast<uint32_t>(count) : 0;
  }
  void close() override { active_->stop(); }

 protected:
  int getSocket() const override { return -1; }

 private:
  const DeviceConfig& config_;
  NetworkClient plain_;
  NetworkClientSecure secure_;
  NetworkClient* active_ = &plain_;
};
