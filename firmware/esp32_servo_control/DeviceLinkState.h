#pragma once

#include <stdint.h>

enum class DeviceLinkStatus { Local, WifiOffline, NeedCredential, WaitingClock, ServerOffline, Unbound, Bound, CredentialInvalid };

// 业务授权与上次绑定结果分开：断线后不能用旧 bound 抢先重连。
// 周期检查暂时失败则保留已有授权，避免弱网时反复打断正常语音。
struct DeviceLinkState {
  DeviceLinkStatus status = DeviceLinkStatus::WifiOffline;
  bool bound = false;
  bool bindingVerified = false;
  uint32_t verificationRevision = 0;

  void updateStatus(DeviceLinkStatus next, int binding = -1) {
    status = next;
    if (binding >= 0) bound = binding != 0;
    if (!bound || next == DeviceLinkStatus::WifiOffline || next == DeviceLinkStatus::WaitingClock
        || next == DeviceLinkStatus::NeedCredential || next == DeviceLinkStatus::CredentialInvalid) {
      bindingVerified = false;
    }
  }

  void requestVerification() {
    bindingVerified = false;
    ++verificationRevision;
  }

  void completeVerification(bool isBound, uint32_t startedRevision) {
    updateStatus(isBound ? DeviceLinkStatus::Bound : DeviceLinkStatus::Unbound, isBound ? 1 : 0);
    // 若查询过程中另一条连接断开，旧响应不能取消这次新的复核请求。
    bindingVerified = isBound && startedRevision == verificationRevision;
  }

  bool businessAllowed() const { return bound && bindingVerified; }
  bool needsAttention() const {
    return status != DeviceLinkStatus::Local && (status != DeviceLinkStatus::Bound || !bindingVerified);
  }
  bool canDisplayPairing() const { return status == DeviceLinkStatus::Unbound && !bound; }
};

inline uint32_t pairingSecondsRemaining(uint32_t now, uint32_t expiresAt, bool hasCode) {
  int32_t remaining = static_cast<int32_t>(expiresAt - now);
  return hasCode && remaining > 0 ? (static_cast<uint32_t>(remaining) + 999) / 1000 : 0;
}
