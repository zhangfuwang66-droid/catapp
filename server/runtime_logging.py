"""运行日志仅保留事件与诊断信息；聊天正文由数据库单独管理。"""

import logging
import io
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sys


logger = logging.getLogger("cybermiao")


def redact_secrets(value: object) -> str:
    text = str(value)
    key = os.getenv("DASHSCOPE_API_KEY", "")
    if key:
        text = text.replace(key, "[凭证已隐藏]")
    text = re.sub(r"sk-[A-Za-z0-9_-]+", "[凭证已隐藏]", text)
    text = re.sub(r"(?i)\bBearer\s+[^\s\"',;}]+", "Bearer [凭证已隐藏]", text)
    text = re.sub(r"(?i)(\b(?:password|api_key|access_token|token|secret)\s*[=:]\s*)[^\s,;}]+",
                  r"\1[凭证已隐藏]", text)
    return text


def provider_error_tag(payload) -> str:
    """上游错误可能回显请求正文，只保留受限的错误类型和代码。"""
    if not isinstance(payload, dict):
        return "provider_error"
    tags = []
    for field in ("type", "code"):
        value = str(payload.get(field) or "")
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,79}", value):
            tags.append(f"{field}={redact_secrets(value)}")
    return " ".join(tags) or "provider_error"


def log_event(message, *, flush=True):
    logger.info("%s", redact_secrets(message).replace("\r", "\\r").replace("\n", "\\n")[:4096])


class PrivacyFilter(logging.Filter):
    def filter(self, record):
        text = redact_secrets(record.getMessage())
        if record.exc_info:
            text += f" [{record.exc_info[0].__name__}]"
            record.exc_info = None
            record.exc_text = None
        record.msg = text.replace("\r", "\\r").replace("\n", "\\n")[:4096]
        record.args = ()
        return True


class PrivateRotatingFileHandler(RotatingFileHandler):
    def _open(self):
        flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.baseFilename, flags, 0o600)
        try:
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
            return io.open(descriptor, mode=self.mode, encoding=self.encoding, errors=self.errors)
        except BaseException:
            os.close(descriptor)
            raise


def configure_logging(directory: Path, *, max_bytes=2 * 1024 * 1024, backups=5):
    """云端入口使用限量轮转日志；桌面进程继续沿用自身的日志通道。"""
    directory = Path(directory)
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    target = directory / "CyberMiao.log"
    if target.is_symlink():
        raise ValueError("日志文件不能是符号链接")
    handler = PrivateRotatingFileHandler(target, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    handler.addFilter(PrivacyFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.addFilter(PrivacyFilter())
        stream.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        root.addHandler(stream)
    for name in ("httpx", "httpcore", "openai", "websockets"):
        logging.getLogger(name).setLevel(logging.WARNING)
    return handler
