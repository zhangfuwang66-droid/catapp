#pragma once
#include <atomic>
#include <cstdint>

// 低位为待播放，高位为回复代次。旧 HTTP 播放结束不能清掉刚到的新回复。
class TtsPlaybackState {
 public:
  void request() {
    uint32_t previous = state_.load();
    while (!state_.compare_exchange_weak(previous, (previous + 2U) | 1U)) {}
  }
  void cancel() { state_.fetch_and(~1U); }
  bool available() const { return state_.load() & 1U; }
  uint32_t token() const { return state_.load(); }
  bool current(uint32_t token) const { return (token & 1U) && state_.load() == token; }
  void finish(uint32_t token) { state_.compare_exchange_strong(token, token & ~1U); }
 private:
  std::atomic<uint32_t> state_{0};
};
