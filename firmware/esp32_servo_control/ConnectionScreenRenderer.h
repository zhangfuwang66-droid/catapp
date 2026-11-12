#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "ConnectionScreenState.h"
#include "ConnectionScreenFont.h"

// 284×240 人眼方向画布；只复用调用者的 PSRAM，不申请字体或动画堆内存。
namespace ConnectionScreen {
constexpr int Width = 284, Height = 240;
constexpr uint16_t rgb(unsigned r, unsigned g, unsigned b) {
  return ((r & 248) << 8) | ((g & 252) << 3) | (b >> 3);
}
constexpr uint16_t Background = rgb(10, 14, 22), Panel = rgb(17, 29, 41);
constexpr uint16_t Accent = rgb(90, 210, 240), Text = rgb(205, 239, 246);
constexpr uint16_t Muted = rgb(116, 151, 169), Border = rgb(38, 62, 77);
constexpr uint16_t Warning = rgb(238, 187, 111), Success = rgb(110, 223, 190);

struct View {
  Stage stage = Stage::Boot;
  bool deer = false, online = false, accounts = true;
  const char* server = "";
  const char* deviceId = "";
  const char* setupSsid = "";
  const char* setupPassword = "";
  const char* pairingCode = "";
  uint32_t expiresIn = 0;
  uint8_t frame = 0;
};

class Painter {
 public:
  explicit Painter(uint16_t* pixels) : pixels_(pixels) {}
  void pixel(int x, int y, uint16_t color) {
    if (x >= 0 && x < Width && y >= 0 && y < Height) pixels_[y * Width + x] = color;
  }
  void rect(int x, int y, int w, int h, uint16_t color) {
    for (int row = y; row < y + h; ++row)
      for (int col = x; col < x + w; ++col) pixel(col, row, color);
  }
  void line(int x, int y, int x2, int y2, uint16_t color) {
    int dx = x2 > x ? x2 - x : x - x2, sx = x < x2 ? 1 : -1;
    int dy = y2 > y ? y - y2 : y2 - y, sy = y < y2 ? 1 : -1, error = dx + dy;
    for (;;) {
      pixel(x, y, color);
      if (x == x2 && y == y2) break;
      int twice = error * 2;
      if (twice >= dy) { error += dy; x += sx; }
      if (twice <= dx) { error += dx; y += sy; }
    }
  }
  void circle(int cx, int cy, int radius, uint16_t color, bool filled = true) {
    for (int y = -radius; y <= radius; ++y)
      for (int x = -radius; x <= radius; ++x) {
        int distance = x * x + y * y;
        if (distance <= radius * radius && (filled || distance >= (radius - 1) * (radius - 1)))
          pixel(cx + x, cy + y, color);
      }
  }
  void card(int x, int y, int w, int h, uint16_t color) {
    // 小圆角，边缘留白；不用新的 framebuffer 或抗锯齿缓存。
    rect(x + 4, y, w - 8, h, color);
    rect(x, y + 4, w, h - 8, color);
    circle(x + 4, y + 4, 4, color); circle(x + w - 5, y + 4, 4, color);
    circle(x + 4, y + h - 5, 4, color); circle(x + w - 5, y + h - 5, 4, color);
  }
  static uint32_t next(const char*& text) {
    uint8_t first = static_cast<uint8_t>(*text++);
    if (first < 128) return first;
    unsigned count = first < 224 ? 1 : first < 240 ? 2 : 3;
    uint32_t code = first & (count == 1 ? 31 : count == 2 ? 15 : 7);
    for (unsigned i = 0; i < count; ++i) {
      uint8_t byte = static_cast<uint8_t>(*text);
      if ((byte & 192) != 128) return '?';
      code = (code << 6) | (byte & 63); ++text;
    }
    return code;
  }
  static const ScreenGlyph& glyph(uint32_t code) {
    size_t low = 0, high = ScreenGlyphCount;
    while (low < high) {
      size_t mid = (low + high) / 2;
      if (ScreenGlyphs[mid].code < code) low = mid + 1; else high = mid;
    }
    return low < ScreenGlyphCount && ScreenGlyphs[low].code == code
        ? ScreenGlyphs[low] : ScreenGlyphs['?' - 32];
  }
  static int textWidth(const char* text, int size = 1) {
    int width = 0;
    if (text) while (*text) width += glyph(next(text)).advance * size;
    return width;
  }
  void text(const char* value, int x, int y, uint16_t color, int size = 1, int maxWidth = Width) {
    if (!value) return;
    int right = x + maxWidth;
    while (*value) {
      const ScreenGlyph& item = glyph(next(value));
      if (x + item.advance * size > right) {
        // 溢出只用于展示，不改变 URL、设备配置或真实 SSID。
        rect(right - 9, y + 10 * size, 2, 2, color);
        rect(right - 5, y + 10 * size, 2, 2, color);
        break;
      }
      for (int row = 0; row < 14; ++row)
        for (int col = 0; col < item.advance; ++col)
          if (item.rows[row] & (0x8000 >> col)) rect(x + col * size, y + row * size, size, size, color);
      x += item.advance * size;
    }
  }
  void centered(const char* value, int y, uint16_t color, int size = 1, int maxWidth = 248) {
    int width = textWidth(value, size);
    text(value, (Width - (width < maxWidth ? width : maxWidth)) / 2, y, color, size, maxWidth);
  }
 private:
  uint16_t* pixels_;
};

inline const char* title(Stage stage) {
  switch (stage) {
    case Stage::Boot: return "正在唤醒";
    case Stage::WifiConnecting: return "正在连接网络";
    case Stage::WifiSetup: return "等待网络设置";
    case Stage::TimeSync: return "正在校准时间";
    case Stage::ServerOffline: return "暂时连不上服务器";
    case Stage::NeedCredential: return "等待设备登记";
    case Stage::InvalidCredential: return "设备凭证待恢复";
    case Stage::PairingRequest: return "正在获取配对码";
    case Stage::PairingReady: return "等待绑定";
    case Stage::BindingCheck: return "正在确认账号";
    case Stage::Ready: return "连接成功";
  }
  return "正在连接";
}

inline void mascot(Painter& p, bool deer, uint8_t frame) {
  p.circle(31, 27, 19, Panel);
  if (deer) {
    for (int side = -1; side <= 1; side += 2) {
      p.line(31 + side * 7, 22, 31 + side * 10, 10, Accent);
      p.line(31 + side * 10, 15, 31 + side * 15, 12, Accent);
      p.line(31 + side * 10, 16, 31 + side * 7, 11, Accent);
    }
  } else {
    p.line(19, 25, 19, 14, Accent); p.line(19, 14, 27, 19, Accent);
    p.line(43, 25, 43, 14, Accent); p.line(43, 14, 35, 19, Accent);
  }
  p.line(21, 23, 21, 33, Accent); p.line(21, 33, 26, 37, Accent);
  p.line(26, 37, 36, 37, Accent); p.line(36, 37, 41, 33, Accent);
  p.line(41, 33, 41, 23, Accent);
  bool blink = frame % 10 == 9;
  if (blink) { p.line(25, 28, 28, 28, Text); p.line(34, 28, 37, 28, Text); }
  else { p.rect(26, 26, 2, 3, Text); p.rect(34, 26, 2, 3, Text); }
  p.pixel(31, 32, Accent);
}

inline void decimal(uint32_t value, char* result) {
  char reversed[11]; unsigned length = 0;
  do { reversed[length++] = static_cast<char>('0' + value % 10); value /= 10; } while (value);
  for (unsigned i = 0; i < length; ++i) result[i] = reversed[length - 1 - i];
  result[length] = 0;
}

inline void render(uint16_t* pixels, const View& view) {
  Painter p(pixels);
  p.rect(0, 0, Width, Height, Background);
  p.line(18, 51, 265, 51, Border);
  mascot(p, view.deer, view.frame);
  p.text(view.deer ? "CYBER DEER" : "CYBER MIAO", 58, 11, Accent);
  p.text(view.deer ? "赛博小鹿" : "赛博小喵", 58, 29, Text);
  p.circle(210, 23, 2, view.online ? Success : Muted);
  p.text(view.online ? "已联网" : "未联网", 219, 17, view.online ? Success : Muted);

  int active = view.stage == Stage::Boot || view.stage == Stage::WifiConnecting || view.stage == Stage::WifiSetup ? 0
      : view.stage == Stage::Ready ? 2 : 1;
  const char* steps[] = {"1 联网", view.accounts ? "2 绑定" : "2 连接", "3 就绪"};
  for (int i = 0; i < 3; ++i) {
    int x = 18 + i * 86;
    p.card(x, 60, 76, 21, i == active ? Panel : Background);
    p.text(steps[i], x + 12, 63, i <= active ? Accent : Muted);
    if (i < 2) p.line(x + 79, 70, x + 82, 70, Border);
  }
  bool warning = view.stage == Stage::NeedCredential || view.stage == Stage::InvalidCredential
      || view.stage == Stage::ServerOffline;
  uint16_t highlight = warning ? Warning : view.stage == Stage::Ready ? Success : Accent;
  p.centered(title(view.stage), 88, highlight);
  p.card(18, 109, 248, 42, Panel);

  const char* primary = "请稍候";
  const char* hint = "联网后将自动继续";
  switch (view.stage) {
    case Stage::Boot: hint = "正在准备屏幕与设备"; break;
    case Stage::WifiConnecting: primary = "连接熟悉的 Wi-Fi"; hint = "未能连接时将开启配网热点"; break;
    case Stage::WifiSetup: primary = view.setupSsid; hint = "手机连接上方热点"; break;
    case Stage::TimeSync: hint = "安全连接需要正确时间"; break;
    case Stage::ServerOffline: primary = "正在自动重连"; hint = "请检查网络或稍后重试"; break;
    case Stage::NeedCredential: primary = "需要 USB 配置"; hint = "请联系设备提供者完成登记"; break;
    case Stage::InvalidCredential: primary = "暂时无法绑定"; hint = "请联系管理员恢复设备身份"; break;
    case Stage::PairingRequest: primary = "正在申请"; hint = "配对码会自动出现，请稍候"; break;
    case Stage::PairingReady: primary = view.pairingCode; hint = "登录网页 > 添加设备 > 输入配对码"; break;
    case Stage::BindingCheck: hint = "正在核对设备归属"; break;
    case Stage::Ready: primary = "准备就绪"; hint = "即将回到你的专属表情"; break;
  }
  bool large = view.stage == Stage::PairingReady || view.stage == Stage::Ready;
  p.centered(primary, large ? 115 : 122, highlight, large ? 2 : 1);
  if (view.stage == Stage::Ready) {
    p.line(230, 128, 235, 133, Success); p.line(235, 133, 247, 121, Success);
  }
  p.centered(hint, 157, Text);
  if (view.stage == Stage::WifiSetup) {
    p.text("热点密码", 47, 176, Muted);
    p.text(view.setupPassword, 115, 176, Text, 1, 127);
  } else if (view.stage == Stage::PairingReady) {
    char seconds[11]; decimal(view.expiresIn, seconds);
    p.text("有效期", 93, 176, Muted);
    p.text(seconds, 141, 176, Accent);
    p.text("秒", 178, 176, Muted);
  } else {
    for (int i = 0; i < 3; ++i) p.circle(134 + i * 8, 181, i == view.frame % 3 ? 2 : 1, i == view.frame % 3 ? highlight : Border);
  }
  p.card(18, 197, 248, 22, Panel);
  p.centered(view.stage == Stage::WifiSetup ? "http://192.168.4.1" : view.server, 201, Accent, 1, 232);
  const char* tail = view.deviceId ? view.deviceId : "";
  size_t length = strlen(tail);
  if (length > 6) tail += length - 6;
  p.text("设备", 20, 223, Muted);
  p.text(tail, 53, 223, Muted);
  p.text(view.stage == Stage::WifiSetup ? "手机配网" : view.accounts ? "云端连接" : "本地连接", 208, 223, Muted);
}
}  // namespace ConnectionScreen
