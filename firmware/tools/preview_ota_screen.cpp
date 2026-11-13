// 同一绘制器生成离线屏幕预览，无需连接设备。
#include "../esp32_servo_control/OtaScreenRenderer.h"
#include <stdio.h>

int main(int argc, char** argv) {
  if (argc != 2) return 2;
  using namespace OtaScreen;
  const char* names[] = {"preparing","downloading","verifying","rebooting","checking","updated","rolling-back","rolled-back","failed"};
  const Phase phases[] = {Phase::Preparing,Phase::Downloading,Phase::Verifying,Phase::Rebooting,
      Phase::Checking,Phase::Updated,Phase::RollingBack,Phase::RolledBack,Phase::Failed};
  uint16_t pixels[ConnectionScreen::Width * ConnectionScreen::Height];
  for (unsigned index = 0; index < sizeof(phases) / sizeof(phases[0]); ++index) {
    for (uint8_t frame = 0; frame < 8; ++frame) {
      const Snapshot state = decode(encode(phases[index], index == 1 ? 47 : index == 2 ? 96 : 98), 0, 0);
      render(pixels, state, frame, "0.3.0-ota.3");
      char path[1024];
      if (snprintf(path,sizeof(path),"%s/%s-%u.ppm",argv[1],names[index],frame) >= static_cast<int>(sizeof(path))) return 3;
      FILE* output=fopen(path,"wb"); if (!output) return 4;
      fprintf(output,"P6\n%d %d\n255\n",ConnectionScreen::Width,ConnectionScreen::Height);
      for (uint16_t color : pixels) {
        const unsigned char rgb[] = {static_cast<unsigned char>(((color>>11)&31)*255/31),
            static_cast<unsigned char>(((color>>5)&63)*255/63),static_cast<unsigned char>((color&31)*255/31)};
        if (fwrite(rgb,1,3,output)!=3) { fclose(output); return 5; }
      }
      if (fclose(output)) return 6;
    }
  }
  puts("Rendered OTA phases with the firmware screen renderer.");
}
