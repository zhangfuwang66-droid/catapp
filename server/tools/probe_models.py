"""使用固定合成内容验证模型接口；输出仅含状态、计数和错误类型。"""

import argparse
import asyncio
import configparser
import io
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def load_api_key():
    key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not key:
        from dotenv import dotenv_values
        key = str(dotenv_values(Path(__file__).resolve().parents[1] / ".env").get("DASHSCOPE_API_KEY") or "").strip()
    if not key:
        path = Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "CyberMiao/settings.ini"
        if path.is_file():
            config = configparser.ConfigParser(interpolation=None)
            config.read(path, encoding="utf-8-sig")
            key = config.get("API", "dashscope_api_key", fallback="").strip()
    if not key or key in {"your_api_key_here", "sk-fake-placeholder"}:
        raise ValueError("没有可用 API Key，请先在桌面程序中保存")
    return key


def error_metadata(error):
    report = {"error_type": type(error).__name__}
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        report["http_status"] = status
    from runtime_logging import provider_error_tag
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        report["provider_error"] = provider_error_tag(body.get("error", body))
    return report


async def realtime_check():
    import omni_realtime_client as realtime
    done = asyncio.Event()
    stats = {"model": realtime.REALTIME_MODEL, "voice": realtime.REALTIME_VOICE,
             "session_ready": False, "text_chars": 0,
             "audio_bytes": 0, "response_done": False, "provider_errors": []}

    async def ignore(*args):
        pass

    async def text(value):
        stats["text_chars"] += len(value)

    async def audio(value):
        stats["audio_bytes"] += len(value)

    async def finished():
        stats["response_done"] = True
        done.set()

    async def failed(message):
        stats["provider_errors"].append(message)
        done.set()

    session = realtime.OmniRealtimeSession(loop=asyncio.get_running_loop(), system_prompt="只回答测试成功。",
                                           on_input_transcript=ignore, on_output_text_delta=text, on_output_audio=audio,
                                           on_response_started=ignore, on_response_done=finished, on_speech_started=ignore,
                                           on_error=failed)
    try:
        await session.ensure_connected()
        stats["session_ready"] = True
        await session.send_text_turn("请只说：测试成功。")
        await asyncio.wait_for(done.wait(), 35)
        stats["ok"] = bool(stats["response_done"] and stats["audio_bytes"] and stats["text_chars"] and not stats["provider_errors"])
    except Exception as error:
        stats.update(ok=False, **error_metadata(error))
    finally:
        await session.close()
    return stats


async def run_checks(key, realtime_model=None, omni_model=None, *, realtime_voice=None,
                     vision_model=None, text_model=None, checks=None):
    checks = tuple(checks or ("realtime", "omni", "summary", "vision"))
    os.environ["DASHSCOPE_API_KEY"] = key
    if realtime_model:
        os.environ["QWEN_REALTIME_MODEL"] = realtime_model
    if omni_model:
        os.environ["QWEN_OMNI_MODEL"] = omni_model
    if realtime_voice:
        os.environ["QWEN_REALTIME_VOICE"] = realtime_voice
    if vision_model:
        os.environ["QWEN_VISION_MODEL"] = vision_model
    if text_model:
        os.environ["QWEN_TEXT_MODEL"] = text_model
    from openai import AsyncOpenAI
    from PIL import Image
    from omni_client import QWEN_MODEL, QWEN_TEXT_MODEL, QWEN_VISION_MODEL
    report = {"created_at": time.time(), "synthetic_content_only": True, "checks": checks}
    if "realtime" in checks:
        report["realtime"] = await realtime_check()
    client = AsyncOpenAI(api_key=key, base_url=os.getenv("DASHSCOPE_COMPAT_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                         timeout=30, max_retries=0)
    async with client:
        if "omni" in checks:
            try:
                stream = await client.chat.completions.create(model=QWEN_MODEL,
                    messages=[{"role":"user", "content":[{"type":"text", "text":"请只说测试成功。"}]}],
                    modalities=["text","audio"], audio={"voice":"Mochi","format":"wav"}, stream=True,
                    stream_options={"include_usage":True})
                text_chars, audio_chars = 0, 0
                async with stream:
                    async for item in stream:
                        for choice in item.choices:
                            delta = choice.delta
                            text_chars += len(delta.content or "")
                            audio = getattr(delta, "audio", None)
                            if isinstance(audio, dict):
                                audio_chars += len(audio.get("data") or "")
                report["omni"] = {"model":QWEN_MODEL,"ok":bool(text_chars and audio_chars), "text_chars":text_chars, "audio_base64_chars":audio_chars}
            except Exception as error:
                report["omni"] = {"model":QWEN_MODEL, "ok":False, **error_metadata(error)}
        if "summary" in checks:
            try:
                result = await client.chat.completions.create(model=QWEN_TEXT_MODEL, max_tokens=200,
                    messages=[{"role":"system","content":'只输出 JSON，包含 summary、memories、open_topics 三个字段。'},
                              {"role":"user","content":"用户说喜欢蓝色，小猫回应下次一起画蓝色的小猫。请总结这段合成测试对话。"}])
                value = json.loads(result.choices[0].message.content or "")
                report["summary"] = {"model":QWEN_TEXT_MODEL, "ok":bool(isinstance(value,dict) and isinstance(value.get("summary"),str)
                                       and isinstance(value.get("memories"),list) and isinstance(value.get("open_topics"),list))}
            except Exception as error:
                report["summary"] = {"model":QWEN_TEXT_MODEL,"ok":False,**error_metadata(error)}
        if "vision" in checks:
            try:
                import base64
                stream = io.BytesIO()
                Image.new("RGB", (64,64), (210,30,30)).save(stream, format="JPEG")
                url = "data:image/jpeg;base64," + base64.b64encode(stream.getvalue()).decode()
                result = await client.chat.completions.create(model=QWEN_VISION_MODEL, max_tokens=40,
                    messages=[{"role":"user","content":[{"type":"image_url","image_url":{"url":url}},
                              {"type":"text","text":"只说这张合成图片的主要颜色。"}]}])
                report["vision"] = {"model":QWEN_VISION_MODEL,"ok":bool(result.choices[0].message.content)}
            except Exception as error:
                report["vision"] = {"model":QWEN_VISION_MODEL,"ok":False,**error_metadata(error)}
    report["ok"] = all(report[name]["ok"] for name in checks)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--realtime-model")
    parser.add_argument("--omni-model")
    parser.add_argument("--realtime-voice")
    parser.add_argument("--vision-model")
    parser.add_argument("--text-model")
    parser.add_argument("--checks", nargs="+", choices=("realtime", "omni", "summary", "vision"),
                        default=("realtime", "omni", "summary", "vision"))
    options = parser.parse_args()
    try:
        report = asyncio.run(run_checks(load_api_key(), options.realtime_model, options.omni_model,
                                       realtime_voice=options.realtime_voice, vision_model=options.vision_model,
                                       text_model=options.text_model, checks=options.checks))
    except Exception as error:
        report = {"ok":False, **error_metadata(error)}
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
