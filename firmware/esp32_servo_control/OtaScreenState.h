#pragma once

#include <stdint.h>
#include <string.h>

namespace OtaScreen {
enum class Phase : uint8_t {
  Hidden, Preparing, Downloading, Verifying, Rebooting, Checking,
  Updated, RollingBack, RolledBack, Failed
};
struct Snapshot {
  Phase phase = Phase::Hidden;
  uint8_t progress = 0;
};

inline Phase phaseFor(const char* state, bool returning = false) {
  if (!state) return Phase::Hidden;
  if (strcmp(state, "downloading") == 0) return Phase::Downloading;
  if (strcmp(state, "verifying") == 0) return Phase::Verifying;
  if (strcmp(state, "rebooting") == 0) return returning ? Phase::RollingBack : Phase::Rebooting;
  if (strcmp(state, "pending_validation") == 0) return Phase::Checking;
  if (strcmp(state, "updated") == 0) return Phase::Updated;
  if (strcmp(state, "rolled_back") == 0) return Phase::RolledBack;
  if (strcmp(state, "failed") == 0) return Phase::Failed;
  return Phase::Hidden;
}

inline uint32_t encode(Phase phase, int progress) {
  // 100% 仅表示启动自检通过；下载百分比直接来自收到的固件字节数。
  if (phase == Phase::Updated || phase == Phase::RolledBack) progress = 100;
  else { if (progress < 0) progress = 0; if (progress > 98) progress = 98; }
  return static_cast<uint32_t>(phase) | (static_cast<uint32_t>(progress) << 8);
}
inline uint32_t feedbackDuration(Phase phase) {
  return phase == Phase::Failed ? 8000U
      : phase == Phase::Updated || phase == Phase::RolledBack ? 4000U : 0U;
}
inline Snapshot decode(uint32_t encoded, uint32_t changedAt, uint32_t now) {
  Snapshot result;
  const uint32_t phase = encoded & 255U;
  if (phase > static_cast<uint32_t>(Phase::Failed)) return result;
  result.phase = static_cast<Phase>(phase);
  result.progress = static_cast<uint8_t>((encode(result.phase, (encoded >> 8) & 255U) >> 8) & 255U);
  const uint32_t duration = feedbackDuration(result.phase);
  if (duration && static_cast<uint32_t>(now - changedAt) >= duration) result.phase = Phase::Hidden;
  return result;
}
inline bool indeterminate(Phase phase) {
  return phase == Phase::Preparing || phase == Phase::RollingBack;
}
inline unsigned step(Phase phase) {
  if (phase == Phase::Verifying) return 1;
  if (phase == Phase::Rebooting || phase == Phase::Checking || phase == Phase::Updated
      || phase == Phase::RollingBack || phase == Phase::RolledBack) return 2;
  return 0;
}
}  // namespace OtaScreen
