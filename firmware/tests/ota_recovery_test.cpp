#include <cassert>
#include "../esp32_servo_control/OtaRecovery.h"
#include "../esp32_servo_control/TtsPlaybackState.h"

int main() {
  assert(recoverOtaBoot("downloading", false, false, false) == OtaRecovery::Interrupted);
  assert(recoverOtaBoot("verifying", false, false, false) == OtaRecovery::Interrupted);
  assert(recoverOtaBoot("rebooting", true, true, false) == OtaRecovery::Validate);
  assert(recoverOtaBoot("pending_validation", false, false, false) == OtaRecovery::RolledBack);
  assert(recoverOtaBoot("rebooting", true, false, false) == OtaRecovery::Updated);
  assert(recoverOtaBoot("rebooting", true, false, true) == OtaRecovery::RolledBack);
  assert(recoverOtaBoot("rebooting", false, false, true) == OtaRecovery::Interrupted);
  assert(recoverOtaBoot("updated", true, false, false) == OtaRecovery::Keep);
  TtsPlaybackState audio;
  audio.request(); const auto old = audio.token();
  audio.request(); const auto fresh = audio.token();
  audio.finish(old);  // 老回复刚结束，新回复仍在等待。
  assert(audio.available() && audio.current(fresh) && !audio.current(old));
  audio.finish(fresh); assert(!audio.available());
  audio.request(); const auto cancelled = audio.token();
  audio.cancel(); audio.finish(cancelled); assert(!audio.available());
  audio.request(); const auto reconnect = audio.token();
  audio.finish(cancelled); assert(audio.current(reconnect));
}
