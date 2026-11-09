#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>

// 单任务使用：先非阻塞预取 HTTP 音频，再喂 I2S。存储由调用方提供，
// 不分配内存、不补静音、不丢弃旧采样；HTTP 分块可以切在采样中间。
class PcmPlaybackBuffer {
 public:
  PcmPlaybackBuffer(uint8_t* storage, size_t capacity, uint32_t sampleRate,
                    uint32_t prebufferMs = 320)
      : storage_(storage), capacity_(storage ? capacity & ~size_t(1) : 0),
        target_(smaller(capacity_, static_cast<size_t>(sampleRate) * 2 * prebufferMs / 1000) & ~size_t(1)) {}

  template <typename Body>
  size_t receive(Body& body, size_t budget = 4096) {
    size_t received = 0;
    while (size_ < capacity_ && received < budget && !body.done()) {
      const size_t wanted = smaller(smaller(capacity_ - size_, capacity_ - write_), budget - received);
      const size_t count = body.read(storage_ + write_, wanted, 0);
      write_ = (write_ + count) % capacity_;
      size_ += count;
      received += count;
      if (count < wanted) break;
    }
    return received;
  }

  size_t take(uint8_t* destination, size_t wanted, bool sourceFinished) {
    wanted &= ~size_t(1);
    const size_t available = size_ & ~size_t(1);
    if (!wanted || !available) {
      if (!sourceFinished) buffering_ = true;
      return 0;
    }
    if (buffering_) {
      if (!sourceFinished && available < target_) return 0;
    } else if (!sourceFinished && available < wanted) {
      buffering_ = true;
      return 0;
    }
    const size_t count = smaller(wanted, available);
    const size_t first = smaller(count, capacity_ - read_);
    std::memcpy(destination, storage_ + read_, first);
    std::memcpy(destination + first, storage_, count - first);
    read_ = (read_ + count) % capacity_;
    size_ -= count;
    // 只有实际恢复输出才计数：句尾等待 EOF 不属于语音中途的重新缓冲。
    if (buffering_ && started_) ++rebuffers_;
    buffering_ = false;
    started_ = true;
    return count;
  }

  size_t size() const { return size_; }
  size_t capacity() const { return capacity_; }
  uint32_t rebuffers() const { return rebuffers_; }

 private:
  static size_t smaller(size_t a, size_t b) { return a < b ? a : b; }

  uint8_t* storage_;
  size_t capacity_, target_;
  size_t read_ = 0, write_ = 0, size_ = 0;
  bool buffering_ = true, started_ = false;
  uint32_t rebuffers_ = 0;
};
