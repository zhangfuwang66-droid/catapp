#pragma once

#include <cstddef>
#include <cstdint>

// HTTP 分块可能停在长度行、CRLF 或 PCM 的任意字节。短超时只结束本次读取，
// 不丢弃已收到的字节或解析位置；HTTP 分块边界不是音频采样边界。
template <typename Client>
class HttpAudioBody {
 public:
  using Clock = uint32_t (*)();
  using Idle = void (*)();

  HttpAudioBody(Client& client, bool chunked, Clock clock, Idle idle)
      : client_(client), chunked_(chunked), clock_(clock), idle_(idle),
        state_(chunked ? State::Size : State::Payload) {}

  size_t read(uint8_t* destination, size_t wanted, uint32_t timeoutMs) {
    size_t copied = 0;
    const uint32_t started = clock_();
    while (copied < wanted && !done()) {
      const int available = client_.available();
      if (available <= 0) {
        if (!client_.connected()) {
          state_ = chunked_ ? State::Error : State::End;
          break;
        }
        if (static_cast<uint32_t>(clock_() - started) >= timeoutMs) break;
        idle_();
        continue;
      }
      if (state_ == State::Payload) {
        size_t take = wanted - copied;
        if (take > static_cast<size_t>(available)) take = available;
        if (chunked_ && take > remaining_) take = remaining_;
        const int count = client_.read(destination + copied, take);
        if (count <= 0) {
          if (static_cast<uint32_t>(clock_() - started) >= timeoutMs) break;
          idle_();
          continue;
        }
        copied += count;
        if (chunked_) {
          remaining_ -= count;
          if (remaining_ == 0) state_ = State::PayloadCR;
        }
      } else {
        const int value = client_.read();
        if (value >= 0) consume(static_cast<uint8_t>(value));
        else {
          if (static_cast<uint32_t>(clock_() - started) >= timeoutMs) break;
          idle_();
        }
      }
    }
    return copied;
  }

  bool done() const { return state_ == State::End || state_ == State::Error; }
  bool failed() const { return state_ == State::Error; }

 private:
  enum class State { Size, SizeLF, Payload, PayloadCR, PayloadLF, End, Error };

  void consume(uint8_t value) {
    switch (state_) {
      case State::Size:
        if (value == '\r') {
          if (!digits_) state_ = State::Error;
          else state_ = State::SizeLF;
          return;
        }
        if (++lineBytes_ > 128 || value == '\n') { state_ = State::Error; return; }
        if (extension_) return;
        if (value == ';' && digits_) { extension_ = true; return; }
        {
          int digit = value >= '0' && value <= '9' ? value - '0' :
                      value >= 'a' && value <= 'f' ? value - 'a' + 10 :
                      value >= 'A' && value <= 'F' ? value - 'A' + 10 : -1;
          if (digit < 0 || remaining_ > (UINT32_MAX - digit) / 16) {
            state_ = State::Error;
            return;
          }
          remaining_ = remaining_ * 16 + digit;
          digits_ = true;
        }
        return;
      case State::SizeLF:
        state_ = value != '\n' ? State::Error : remaining_ ? State::Payload : State::End;
        return;
      case State::PayloadCR:
        state_ = value == '\r' ? State::PayloadLF : State::Error;
        return;
      case State::PayloadLF:
        if (value != '\n') { state_ = State::Error; return; }
        remaining_ = 0;
        lineBytes_ = 0;
        digits_ = extension_ = false;
        state_ = State::Size;
        return;
      default:
        state_ = State::Error;
    }
  }

  Client& client_;
  bool chunked_;
  Clock clock_;
  Idle idle_;
  State state_;
  uint32_t remaining_ = 0;
  uint16_t lineBytes_ = 0;
  bool digits_ = false;
  bool extension_ = false;
};
