#pragma once
#include <cstring>

// 在旧槽重启、候选槽首次启动、确认后掉电之间区分结果；不依赖 Arduino。
enum class OtaRecovery { Keep, Validate, Updated, RolledBack, Interrupted };
inline OtaRecovery recoverOtaBoot(const char* phase, bool targetIsCurrent, bool pending, bool rollback) {
  if (pending) return OtaRecovery::Validate;
  if (!std::strcmp(phase, "rebooting") || !std::strcmp(phase, "pending_validation")) {
    return targetIsCurrent ? (rollback ? OtaRecovery::RolledBack : OtaRecovery::Updated)
                           : (rollback ? OtaRecovery::Interrupted : OtaRecovery::RolledBack);
  }
  if (!std::strcmp(phase, "downloading") || !std::strcmp(phase, "verifying")) return OtaRecovery::Interrupted;
  return OtaRecovery::Keep;
}
