#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "DeviceLinkState.h"

namespace ConnectionScreen {

enum class Stage {
  Boot, WifiConnecting, WifiSetup, TimeSync, ServerOffline, NeedCredential,
  InvalidCredential, PairingRequest, PairingReady, BindingCheck, Ready
};

struct Input {
  bool online = false;
  bool portal = false;
  bool accounts = false;
  bool tls = false;
  bool clockReady = false;
  DeviceLinkState link;
  bool hasPairingCode = false;
  uint32_t expiresIn = 0;
};

inline Stage selectStage(const Input& input) {
  if (input.portal) return Stage::WifiSetup;
  if (!input.online) return Stage::WifiConnecting;
  if (input.tls && !input.clockReady) return Stage::TimeSync;
  if (!input.accounts) return Stage::Ready;

  switch (input.link.status) {
    case DeviceLinkStatus::NeedCredential: return Stage::NeedCredential;
    case DeviceLinkStatus::CredentialInvalid: return Stage::InvalidCredential;
    case DeviceLinkStatus::ServerOffline: return Stage::ServerOffline;
    case DeviceLinkStatus::Bound:
      return input.link.businessAllowed() ? Stage::Ready : Stage::BindingCheck;
    case DeviceLinkStatus::Unbound:
      // 旧码仍在内存中不代表可以展示；授权状态与有效期必须同时允许。
      if (!input.link.canDisplayPairing()) return Stage::BindingCheck;
      return input.hasPairingCode && input.expiresIn > 0 ? Stage::PairingReady : Stage::PairingRequest;
    default:
      // 网络/时钟已恢复而账号任务尚未复核时，不沿用旧绑定或旧配对码。
      return Stage::BindingCheck;
  }
}

// 只简化展示文本，不改变连接地址；只移除 HTTPS authority 的默认端口。
// 输入、输出可以是同一个缓冲区。容量不足时清空，避免展示截断后的错误地址。
inline bool formatDisplayUrl(const char* input, char* output, size_t capacity) {
  if (!output || capacity == 0) return false;
  if (!input) { output[0] = '\0'; return false; }
  const size_t length = strlen(input);
  size_t portAt = length;
  const char scheme[] = "https://";
  bool https = length >= sizeof(scheme) - 1;
  for (size_t index = 0; https && index < sizeof(scheme) - 1; ++index) {
    char value = input[index];
    if (value >= 'A' && value <= 'Z') value = static_cast<char>(value + ('a' - 'A'));
    if (value != scheme[index]) https = false;
  }
  if (https) {
    const size_t authorityAt = sizeof(scheme) - 1;
    size_t end = authorityAt;
    while (end < length && input[end] != '/' && input[end] != '?' && input[end] != '#') ++end;
    if (end >= authorityAt + 5 && memcmp(input + end - 4, ":443", 4) == 0) {
      const size_t candidate = end - 4;
      bool validHost = false;
      if (input[authorityAt] == '[') {
        // IPv6 字面量必须有成对括号，端口只能出现在右括号之后。
        size_t closing = authorityAt + 1;
        while (closing < candidate && input[closing] != ']') ++closing;
        validHost = closing > authorityAt + 1 && closing + 1 == candidate;
      } else {
        validHost = candidate > authorityAt;
        for (size_t index = authorityAt; validHost && index < candidate; ++index)
          if (input[index] == ':' || input[index] == '@' || input[index] == '[' || input[index] == ']') validHost = false;
      }
      if (validHost) portAt = candidate;
    }
  }
  const size_t removed = portAt == length ? 0 : 4;
  const size_t displayLength = length - removed;
  if (displayLength >= capacity) { output[0] = '\0'; return false; }
  if (removed) {
    memmove(output, input, portAt);
    memmove(output + portAt, input + portAt + removed, length - portAt - removed + 1);
  } else {
    memmove(output, input, length + 1);
  }
  return true;
}

struct SuccessFeedback {
  bool visible(Stage next, uint32_t now) {
    if (next != Stage::Ready) {
      previous_ = next;
      active_ = false;
      return false;
    }
    if (previous_ != Stage::Ready) {
      active_ = previous_ == Stage::PairingReady || previous_ == Stage::PairingRequest
          || previous_ == Stage::BindingCheck;
      startedAt_ = now;
      previous_ = next;
    }
    // 无符号差值跨 millis() 回绕仍正确；过期后同一 Ready 不会再次点亮。
    if (active_ && static_cast<uint32_t>(now - startedAt_) >= 1200) active_ = false;
    return active_;
  }

 private:
  Stage previous_ = Stage::Boot;
  uint32_t startedAt_ = 0;
  bool active_ = false;
};

}  // namespace ConnectionScreen
