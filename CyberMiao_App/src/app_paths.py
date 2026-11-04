"""只读程序资源与用户可写数据的路径。"""
import os
import sys
from pathlib import Path


def resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]


def server_root() -> Path:
    if getattr(sys, "frozen", False):
        return resource_root() / "server"
    return Path(__file__).resolve().parents[2] / "esp32_servo_control" / "server"


def user_data_dir() -> Path:
    override = os.getenv("CYBERMIAO_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path(os.getenv("LOCALAPPDATA", str(Path.home() / ".local" / "share"))) / "CyberMiao"
