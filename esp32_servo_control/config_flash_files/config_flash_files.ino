/*
 * 配网语音提示文件写入工具
 *
 * 作用：把 /config_prompt.pcm 写入 LittleFS 根目录，供主程序进入配网模式时播放。
 *
 * 使用步骤：
 * 1. （首次或换语音时）运行 generate_config_prompt_header.py 生成 config_prompt.h
 * 2. 用本目录下的 custom partitions.csv 烧录本程序
 * 3. 打开串口监视器(115200)，只输入字母 W
 * 4. 看到“写入完成”后，重新烧录主程序 esp32_servo_control.ino
 *
 * 注意：
 * - 必须和主程序使用同一份 custom partitions.csv（本目录已放了一份）
 * - 不要输入 F，避免清空已有文件系统
 */

#include <LittleFS.h>

#include "config_prompt.h"

void writePrompt() {
  Serial.println("\n[写入] 开始写入 /config_prompt.pcm ...");
  File file = LittleFS.open("/config_prompt.pcm", "w");
  if (!file) {
    Serial.println("[错误] 打开文件失败");
    return;
  }

  uint32_t written = 0;
  uint8_t buffer[256];
  while (written < CONFIG_PROMPT_SIZE) {
    int toWrite = min((uint32_t)256, CONFIG_PROMPT_SIZE - written);
    for (int j = 0; j < toWrite; j++) {
      buffer[j] = pgm_read_byte(config_prompt_data + written + j);
    }
    file.write(buffer, toWrite);
    written += toWrite;
  }
  file.close();

  if (written == CONFIG_PROMPT_SIZE) {
    Serial.printf("[完成] 写入 %d 字节 -> /config_prompt.pcm\n", written);
    Serial.println("★ 可以重新烧录主程序 esp32_servo_control.ino 了");
  } else {
    Serial.printf("[错误] 只写入 %d/%d 字节\n", written, CONFIG_PROMPT_SIZE);
  }
}

void setup() {
  Serial.begin(115200);
  delay(1500);

  Serial.println("\n==== 配网语音提示写入工具 ====");
  if (!LittleFS.begin(true)) {
    Serial.println("[错误] LittleFS 初始化失败");
    while (1) delay(100);
  }
  Serial.printf("[就绪] LittleFS 总空间 %d KB，可用 %d KB\n",
                LittleFS.totalBytes() / 1024,
                (LittleFS.totalBytes() - LittleFS.usedBytes()) / 1024);
  Serial.println("串口输入 W 写入 /config_prompt.pcm");
}

void loop() {
  if (Serial.available()) {
    char c = Serial.read();
    while (Serial.available()) Serial.read();

    if (c == 'W' || c == 'w') {
      writePrompt();
    } else if (c == 'L' || c == 'l') {
      File f = LittleFS.open("/config_prompt.pcm", "r");
      if (f) {
        Serial.printf("[列表] /config_prompt.pcm 存在，%d 字节\n", f.size());
        f.close();
      } else {
        Serial.println("[列表] /config_prompt.pcm 不存在");
      }
    } else {
      Serial.println("输入 W 写入；输入 L 检查文件是否存在");
    }
  }
  delay(10);
}
