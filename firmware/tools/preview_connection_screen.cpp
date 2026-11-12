// 使用与固件相同的绘制器生成离线预览；所有设备、密码、配对码均为虚构样例。
#include "../esp32_servo_control/ConnectionScreenRenderer.h"
#include <stdio.h>

int main(int argc, char** argv) {
  using namespace ConnectionScreen;
  if (argc != 2) return 2;
  uint16_t pixels[Width * Height];
  const char* names[] = {"boot", "wifi-connecting", "wifi-setup", "time-sync", "server-offline",
      "need-credential", "invalid-credential", "pairing-request", "pairing-ready", "binding-check", "ready"};
  for (unsigned deer = 0; deer < 2; ++deer) {
    for (unsigned stage = 0; stage < sizeof(names) / sizeof(names[0]); ++stage) {
      for (unsigned frame = 0; frame < 10; ++frame) {
        View view;
        view.stage = static_cast<Stage>(stage);
        view.deer = deer;
        view.online = stage >= 3;
        view.server = "https://123.57.23.227";
        view.deviceId = deer ? "deer-000000ABCDEF" : "cat-000000ABCDEF";
        view.setupSsid = deer ? "CyberDeer-ABCDEF" : "CyberMiao-ABCDEF";
        view.setupPassword = "ABCDEFGH2345";
        view.pairingCode = "ABCD-EFGH";
        view.expiresIn = 240;
        view.frame = frame;
        render(pixels, view);
        char path[1024];
        if (snprintf(path, sizeof(path), "%s/%s-%s-%u.ppm", argv[1], deer ? "deer" : "cat", names[stage], frame)
            >= static_cast<int>(sizeof(path))) return 3;
        FILE* output = fopen(path, "wb");
        if (!output) return 4;
        fprintf(output, "P6\n%d %d\n255\n", Width, Height);
        for (uint16_t color : pixels) {
          unsigned char rgb[] = {static_cast<unsigned char>(((color >> 11) & 31) * 255 / 31),
              static_cast<unsigned char>(((color >> 5) & 63) * 255 / 63),
              static_cast<unsigned char>((color & 31) * 255 / 31)};
          if (fwrite(rgb, 1, 3, output) != 3) { fclose(output); return 5; }
        }
        if (fclose(output)) return 6;
      }
    }
  }
  puts("Rendered cat/deer states and animation frames using the firmware renderer.");
  return 0;
}
