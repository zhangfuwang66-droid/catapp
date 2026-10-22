# -*- coding: utf-8 -*-
"""生成配网语音提示文件 config_prompt.pcm（PCM16 / 24kHz / 单声道原始数据）。

使用阿里云百炼（DashScope）CosyVoice 专用 TTS，逐字朗读，输出 24kHz/16bit/
单声道原始 PCM，直接供 ESP32 I2S 播放（与服务器 TTS 输出格式一致）。

用法（在 server 目录下运行）：
    python gen_config_prompt.py

生成的文件需上传到 ESP32 的 LittleFS 根目录，命名为 config_prompt.pcm。
若 ESP32 缺少该文件，会退化为两声提示音，不影响配网功能。
"""
import os

from dotenv import load_dotenv

load_dotenv()

from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer  # noqa: E402

PROMPT_TEXT = "已进入配网模式，请用手机连接热点，打开浏览器进行配网。"
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_prompt.pcm")


def main() -> None:
    synthesizer = SpeechSynthesizer(
        model="cosyvoice-v1",
        voice="longxiaochun",
        format=AudioFormat.PCM_24000HZ_MONO_16BIT,
    )
    pcm = synthesizer.call(PROMPT_TEXT)
    if not pcm:
        resp = synthesizer.get_response()
        print("[ERROR] TTS 未返回音频")
        if resp is not None:
            print(f"        status_code={getattr(resp, 'status_code', None)}")
            print(f"        message={getattr(resp, 'message', None)}")
            print(f"        code={getattr(resp, 'code', None)}")
        return

    with open(OUT_PATH, "wb") as f:
        f.write(pcm)

    seconds = len(pcm) / 24000 / 2
    print(f"[OK] 已生成 {OUT_PATH}")
    print(f"     字节数={len(pcm)}  时长约={seconds:.2f}s @24kHz PCM16 mono")


if __name__ == "__main__":
    main()
