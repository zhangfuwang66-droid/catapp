#pragma once

#include "ConnectionScreenRenderer.h"
#include "OtaScreenState.h"

namespace OtaScreen {
inline const char* title(Phase phase) {
  switch (phase) {
    case Phase::Preparing: return "正在准备更新";
    case Phase::Downloading: return "正在下载新版";
    case Phase::Verifying: return "正在校验固件";
    case Phase::Rebooting: return "即将重启小鹿";
    case Phase::Checking: return "正在启动自检";
    case Phase::Updated: return "更新已完成";
    case Phase::RollingBack: return "正在恢复旧版";
    case Phase::RolledBack: return "已回到上一版";
    case Phase::Failed: return "本次更新未完成";
    default: return "设备更新";
  }
}
inline void render(uint16_t* pixels, Snapshot state, uint8_t frame, const char* version) {
  using namespace ConnectionScreen;
  Painter p(pixels);
  const bool success = state.phase == Phase::Updated || state.phase == Phase::RolledBack;
  const bool failed = state.phase == Phase::Failed;
  const uint16_t highlight = success ? Success : failed ? Warning : Accent;
  p.rect(0, 0, Width, Height, Background);
  mascot(p, true, frame);
  p.text("CYBER DEER", 58, 11, Accent);
  p.text("设备更新", 58, 29, Text);
  p.card(226, 15, 40, 21, Panel);
  p.text("OTA", 235, 18, highlight);
  p.line(18, 51, 265, 51, Border);
  p.text(title(state.phase), 20, 63, Text);

  char value[16];
  if (failed || indeterminate(state.phase)) { strcpy(value, "--"); }
  else {
    decimal(state.progress, value);
    const size_t end = strlen(value); value[end] = '%'; value[end + 1] = 0;
  }
  p.text(value, 19, 88, highlight, 3, 150);
  p.text(version, 20, 127, Muted, 1, 160);
  // 八点轨道只表示正在工作，不会伪造下载进度，也不新建动画缓冲。
  const int dx[] = {0, 15, 22, 15, 0, -15, -22, -15};
  const int dy[] = {-22, -15, 0, 15, 22, 15, 0, -15};
  p.circle(229, 104, 30, Panel);
  for (unsigned i = 0; i < 8; ++i)
    p.circle(229 + dx[i], 104 + dy[i], i == frame % 8 ? 3 : 2,
             success ? Success : failed ? Border : i == frame % 8 ? Accent : Border);
  if (success) { p.line(219, 104, 226, 111, Success); p.line(226, 111, 240, 96, Success); }
  else if (failed) { p.rect(228, 95, 3, 11, Warning); p.rect(228, 110, 3, 3, Warning); }
  else { p.line(229, 112, 229, 97, Accent); p.line(224, 102, 229, 97, Accent); p.line(229, 97, 234, 102, Accent); }

  p.card(18, 149, 248, 10, Border);
  if (indeterminate(state.phase)) p.rect(20 + (frame % 8) * 25, 151, 48, 6, highlight);
  else if (!failed && state.progress) p.rect(20, 151, (244 * state.progress + 99) / 100, 6, highlight);
  const char* steps[] = {"下载", "校验", "自检"};
  for (unsigned i = 0; i < 3; ++i) {
    const int x = 18 + i * 86;
    const bool active = !failed && (success || i <= step(state.phase));
    p.card(x, 171, 76, 23, active ? Panel : Background);
    p.circle(x + 14, 182, 2, active ? highlight : Border);
    p.text(steps[i], x + 25, 175, active ? highlight : Muted);
  }
  p.centered(failed ? "当前版本继续使用" : success ? "正在恢复连接" : "请保持供电与网络", 204, success ? Success : Text);
  p.centered(failed ? "请到控制台查看详情" : success ? "即将返回原表情"
      : state.phase == Phase::Checking ? "检查通过后自动完成" : "更新期间请勿断电", 223, Muted);
}
}  // namespace OtaScreen
