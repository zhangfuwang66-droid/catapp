"""程序资源保持只读；桌面版记录和模型存到用户数据目录。"""
import os
import sys
from pathlib import Path

SERVER_ROOT = Path(sys._MEIPASS) / "server" if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
DATA_ROOT = Path(os.getenv("CYBERMIAO_DATA_DIR", str(SERVER_ROOT))).expanduser().resolve()


def model_path(filename: str) -> Path:
    cached = DATA_ROOT / "models" / filename
    if cached.is_file():
        return cached
    bundled = SERVER_ROOT / "models" / filename
    return bundled if bundled.is_file() else cached
