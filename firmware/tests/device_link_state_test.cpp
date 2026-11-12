#include "../esp32_servo_control/DeviceLinkState.h"

#include <assert.h>
#include <stdint.h>

int main() {
  DeviceLinkState state;
  assert(!state.businessAllowed());
  assert(state.needsAttention());
  state.completeVerification(true, state.verificationRevision);
  assert(state.businessAllowed());
  assert(!state.needsAttention());
  assert(!state.canDisplayPairing());

  // 一次周期检查失败不主动打断尚未断开的正常业务。
  state.updateStatus(DeviceLinkStatus::ServerOffline);
  assert(state.bound && state.businessAllowed());
  assert(state.needsAttention());
  assert(!state.canDisplayPairing());

  // WS断开立刻停止旧授权重连；失败后仍不能重新开放。
  state.requestVerification();
  assert(state.bound && !state.businessAllowed());
  state.updateStatus(DeviceLinkStatus::ServerOffline);
  assert(!state.businessAllowed());
  state.completeVerification(false, state.verificationRevision);
  assert(!state.bound && !state.businessAllowed() && state.canDisplayPairing());

  // 联网解绑只改变绑定/业务状态，不清除独立保存的网络与身份。
  assert(state.needsAttention());
  state.completeVerification(true, state.verificationRevision);
  assert(state.businessAllowed() && !state.canDisplayPairing());

  // 查询过程中发生新的连接关闭：旧响应不能授权，必须再查。
  uint32_t inFlight = state.verificationRevision;
  state.requestVerification();
  state.completeVerification(true, inFlight);
  assert(state.bound && !state.businessAllowed() && state.needsAttention());
  state.completeVerification(true, state.verificationRevision);
  assert(state.businessAllowed());

  // 撤销身份不是普通解绑，禁止业务和配对码；新凭证配发后才能重查。
  state.updateStatus(DeviceLinkStatus::CredentialInvalid, 0);
  assert(!state.bound && !state.businessAllowed() && !state.canDisplayPairing());
  state.completeVerification(false, state.verificationRevision);
  assert(state.canDisplayPairing());
  state.updateStatus(DeviceLinkStatus::WifiOffline);
  assert(!state.canDisplayPairing());
  state.completeVerification(true, state.verificationRevision);
  state.updateStatus(DeviceLinkStatus::WaitingClock);
  assert(state.bound && !state.businessAllowed());
  state.updateStatus(DeviceLinkStatus::NeedCredential, 0);
  assert(!state.businessAllowed() && !state.canDisplayPairing());
  state.updateStatus(DeviceLinkStatus::Local);
  assert(!state.needsAttention());

  // 过期与毫秒回绕，包括有效截止时间恰好为0的情况。
  assert(pairingSecondsRemaining(1000, 2000, true) == 1);
  assert(pairingSecondsRemaining(1001, 2000, true) == 1);
  assert(pairingSecondsRemaining(1000, 1000, true) == 0);
  assert(pairingSecondsRemaining(1001, 1000, true) == 0);
  assert(pairingSecondsRemaining(UINT32_MAX - 999, 0, true) == 1);
  assert(pairingSecondsRemaining(UINT32_MAX - 500, 499, true) == 1);
  assert(pairingSecondsRemaining(500, 499, true) == 0);
  assert(pairingSecondsRemaining(1000, 2000, false) == 0);
  return 0;
}
