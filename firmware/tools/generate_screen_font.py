"""生成连接屏幕专用的精简字库；仅开发时需要 Pillow 和 Noto Sans CJK SC。"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1] / "esp32_servo_control"
FONT_SHA256 = "2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b"


def generate(font_path: Path, output: Path):
    if hashlib.sha256(font_path.read_bytes()).hexdigest() != FONT_SHA256:
        raise ValueError("请使用指定版本的开源 NotoSansCJKsc-Regular.otf 字体")
    # 仅保留界面文案里的中文。可变热点名为产品生成的 ASCII 名称。
    source = "\n".join((ROOT / name).read_text(encoding="utf-8")
                       for name in ("ConnectionScreenRenderer.h", "OtaScreenRenderer.h"))
    literals = "".join(re.findall(r'"([^"\n]*)"', source))
    codes = sorted(set(range(32, 127)) | {ord(c) for c in literals if ord(c) >= 128})
    fonts = {11: ImageFont.truetype(str(font_path), 11), 14: ImageFont.truetype(str(font_path), 14)}
    rows = ["#pragma once", "", "#include <stddef.h>", "#include <stdint.h>", "",
            "// Noto Sans CJK SC 的 1-bit 精简字形；SIL OFL 1.1，见 licenses/NotoSansCJK-OFL.txt。",
            "// 由 firmware/tools/generate_screen_font.py 生成；字体仅存 Flash，不分配运行时堆。",
            "namespace ConnectionScreen {",
            "struct ScreenGlyph { uint32_t code; uint8_t advance; uint16_t rows[14]; };",
            "static const ScreenGlyph ScreenGlyphs[] = {"]
    for code in codes:
        char = chr(code)
        face = fonts[11 if code < 128 else 14]
        advance = min(16, max(3, round(face.getlength(char))))
        image = Image.new("L", (16, 14))
        ImageDraw.Draw(image).text((0, 12), char, font=face, fill=255, anchor="ls")
        bitmap = [sum((1 << (15 - x)) for x in range(advance) if image.getpixel((x, y)) >= 96)
                  for y in range(14)]
        rows.append("  {0x%04X, %d, {%s}}," % (code, advance, ", ".join("0x%04X" % row for row in bitmap)))
    rows += ["};", "constexpr size_t ScreenGlyphCount = sizeof(ScreenGlyphs) / sizeof(ScreenGlyphs[0]);",
             "}  // namespace ConnectionScreen", ""]
    output.write_text("\n".join(rows), encoding="utf-8")
    print("Generated %d glyphs (%d bytes of bitmap rows)" % (len(codes), len(codes) * 28))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "ConnectionScreenFont.h")
    args = parser.parse_args()
    generate(args.font, args.output)
