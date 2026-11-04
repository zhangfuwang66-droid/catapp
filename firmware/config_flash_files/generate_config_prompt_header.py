#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 data/config_prompt.pcm 生成 config_prompt.h（嵌入为 PROGMEM 字节数组）。

用法（在本目录下运行）：
    python generate_config_prompt_header.py

生成的 config_prompt.h 会被 config_flash_files.ino 引用，
烧录后串口输入 W 即可把语音文件写入 LittleFS 根目录 /config_prompt.pcm。
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE_DIR, "data", "config_prompt.pcm")
OUT = os.path.join(BASE_DIR, "config_prompt.h")


def main():
    if not os.path.isfile(SRC):
        print(f"[错误] 找不到源文件: {SRC}")
        return

    with open(SRC, "rb") as f:
        data = f.read()

    hex_values = [f"0x{b:02X}" for b in data]
    lines = [",".join(hex_values[i:i + 16]) for i in range(0, len(hex_values), 16)]
    body = ",\n  ".join(lines)

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("// 配网语音提示 /config_prompt.pcm\n")
        f.write("// 自动生成，请勿手动编辑\n\n")
        f.write("#ifndef CONFIG_PROMPT_H\n")
        f.write("#define CONFIG_PROMPT_H\n\n")
        f.write("#include <Arduino.h>\n\n")
        f.write(f"const uint32_t CONFIG_PROMPT_SIZE = {len(data)};\n\n")
        f.write("const uint8_t config_prompt_data[] PROGMEM = {\n  ")
        f.write(body)
        f.write("\n};\n\n")
        f.write("#endif // CONFIG_PROMPT_H\n")

    print(f"[OK] 已生成 {OUT}（{len(data)} 字节）")


if __name__ == "__main__":
    main()
