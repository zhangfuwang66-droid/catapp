# -*- coding: utf-8 -*-
import os
from runtime_logging import log_event
from typing import Dict

from openai import OpenAI

API_KEY = os.getenv("DASHSCOPE_API_KEY", "")

EMOTIONS = {
    "happy",
    "sad",
    "angry",
    "surprised",
    "thinking",
    "sleepy",
    "excited",
    "confused",
    "love",
    "neutral",
    "fear",
    "shy",
    "listening",
}

DEFAULT_EMOTION = "neutral"

EMOTION_EXTRACT_PROMPT = """你是一个情绪分析助手。请从文本中判断最主要的情绪，只能返回以下英文标签之一：
happy, sad, angry, surprised, thinking, sleepy, excited, confused, love, neutral, fear, shy, listening

规则：
1. 只能返回一个标签
2. 不要解释
3. 无法判断时返回 neutral"""


def _make_client(api_key) -> OpenAI:
    return OpenAI(
        api_key=api_key,
        base_url=os.getenv("DASHSCOPE_COMPAT_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        timeout=4.0,
        max_retries=0,
    )


def analyze_emotion(text: str, api_key=None) -> str:
    api_key = API_KEY if api_key is None else api_key
    if not api_key:
        return DEFAULT_EMOTION
    if not text or len(text.strip()) < 2:
        return DEFAULT_EMOTION
    try:
        with _make_client(api_key) as client:
            response = client.chat.completions.create(
                model=os.getenv("QWEN_MODEL", "qwen-turbo"),
                messages=[
                    {"role": "system", "content": EMOTION_EXTRACT_PROMPT},
                    {"role": "user", "content": text.strip()[:500]},
                ],
                stream=False,
                max_tokens=20,
            )
        emotion = (response.choices[0].message.content or "").strip().lower()
        if emotion in EMOTIONS:
            return emotion
        for candidate in EMOTIONS:
            if candidate in emotion:
                return candidate
    except Exception as exc:
        log_event(f"[EMOTION] parse failed: {type(exc).__name__}", flush=True)
    return DEFAULT_EMOTION
