#include <assert.h>
#include <stdint.h>
#include <string.h>
#include "../esp32_servo_control/OtaScreenState.h"
#include "../esp32_servo_control/OtaScreenRenderer.h"

int main() {
  using namespace OtaScreen;
  assert(phaseFor("downloading") == Phase::Downloading);
  assert(phaseFor("verifying") == Phase::Verifying);
  assert(phaseFor("pending_validation") == Phase::Checking);
  assert(phaseFor("rebooting", true) == Phase::RollingBack);
  assert(phaseFor("rebooting", false) == Phase::Rebooting);
  assert(phaseFor(nullptr) == Phase::Hidden);
  assert(decode(255, 0, 0).phase == Phase::Hidden);
  assert(decode(encode(Phase::Downloading, 101), 0, 1).progress == 98);
  assert(decode(encode(Phase::Downloading, -9), 0, 1).progress == 0);
  assert(decode(encode(Phase::Updated, 98), 12, 13).progress == 100);
  assert(decode(encode(Phase::Updated, 98), 12, 4011).phase == Phase::Updated);
  assert(decode(encode(Phase::Updated, 98), 12, 4012).phase == Phase::Hidden);
  assert(decode(encode(Phase::Failed, 33), 12, 8011).phase == Phase::Failed);
  assert(decode(encode(Phase::Failed, 33), 12, 8012).phase == Phase::Hidden);
  // millis 回绕时仍按差值结束反馈；自检与下载不能按提示时长提前消失。
  const uint32_t nearWrap = 0xfffffff0U;
  assert(decode(encode(Phase::RolledBack, 0), nearWrap, nearWrap + 4000U).phase == Phase::Hidden);
  assert(decode(encode(Phase::Checking, 98), 0, 180000).phase == Phase::Checking);
  assert(decode(encode(Phase::Downloading, 20), 0, 180000).phase == Phase::Downloading);
  // 字体缺字和越界写在桌面就可发现；与设备使用完全相同的绘制器。
  const Phase phases[] = {Phase::Preparing, Phase::Downloading, Phase::Verifying, Phase::Rebooting,
      Phase::Checking, Phase::Updated, Phase::RollingBack, Phase::RolledBack, Phase::Failed};
  uint16_t guarded[ConnectionScreen::Width * ConnectionScreen::Height + 2];
  for (Phase phase : phases) {
    const char* label = title(phase);
    while (*label) { const uint32_t code = ConnectionScreen::Painter::next(label);
      assert(ConnectionScreen::Painter::glyph(code).code == code); }
    for (uint8_t frame = 0; frame < 8; ++frame) {
      guarded[0] = 0x1234; guarded[sizeof(guarded) / sizeof(guarded[0]) - 1] = 0x5678;
      const Snapshot state{phase, static_cast<uint8_t>(frame ? 73 : 0)};
      render(guarded + 1, state, frame, "0.3.0-ota.3");
      assert(guarded[0] == 0x1234 && guarded[sizeof(guarded) / sizeof(guarded[0]) - 1] == 0x5678);
    }
  }
}
