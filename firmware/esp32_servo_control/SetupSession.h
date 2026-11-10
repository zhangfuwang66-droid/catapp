#pragma once

#include <cstdint>

// 配网页请求、扫描和重启共用的时序；不依赖 Arduino，便于用模拟时钟验证。
class SetupSession {
 public:
  static constexpr uint32_t CLIENT_GRACE_MS = 10000;
  static constexpr uint32_t SCAN_TIMEOUT_MS = 12000;
  static constexpr uint32_t REBOOT_DELAY_MS = 8000;

  void touch(uint32_t now) { touched_ = true; lastActivity_ = now; }
  bool holdsNetwork(uint32_t now, bool hasClients) const {
    return hasClients || scanning_ || rebootPending_
        || (touched_ && now - lastActivity_ < CLIENT_GRACE_MS);
  }
  void startScan(uint32_t now) { scanning_ = true; scanStarted_ = now; scanTimedOut_ = false; }
  bool scanExpired(uint32_t now) const { return scanning_ && now - scanStarted_ >= SCAN_TIMEOUT_MS; }
  void finishScan() { scanning_ = false; }
  void expireScan() { scanning_ = false; scanTimedOut_ = true; }
  bool scanning() const { return scanning_; }
  bool scanTimedOut() const { return scanTimedOut_; }
  void saved(uint32_t now) { rebootPending_ = true; savedAt_ = now; }
  bool rebootPending() const { return rebootPending_; }
  bool shouldReboot(uint32_t now) const { return rebootPending_ && now - savedAt_ >= REBOOT_DELAY_MS; }

 private:
  bool touched_ = false;
  bool scanning_ = false;
  bool scanTimedOut_ = false;
  bool rebootPending_ = false;
  uint32_t lastActivity_ = 0;
  uint32_t scanStarted_ = 0;
  uint32_t savedAt_ = 0;
};
