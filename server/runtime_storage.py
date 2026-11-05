"""录制图片的保留、容量控制；数据库、备份和未知文件不在清理范围内。"""

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
import shutil
import stat
import time

from async_helpers import run_blocking
from device_identity import validate_device_id
from runtime_logging import log_event


class RecordingSpaceError(OSError):
    pass


@dataclass(frozen=True)
class StorageSettings:
    retention_days: float = 7
    max_bytes: int = 1024 * 1024 * 1024
    min_free_bytes: int = 512 * 1024 * 1024
    interval_seconds: float = 300

    def __post_init__(self):
        if not math.isfinite(self.retention_days) or self.retention_days <= 0:
            raise ValueError("录制图片保留天数必须大于零")
        if self.max_bytes <= 0 or self.min_free_bytes < 0:
            raise ValueError("录制容量须大于零，磁盘预留不能为负")
        if not math.isfinite(self.interval_seconds) or self.interval_seconds <= 0:
            raise ValueError("录制清理间隔必须大于零")

    @classmethod
    def from_environment(cls):
        return cls(float(os.getenv("CYBERMIAO_RECORD_RETENTION_DAYS", "7")),
                   int(os.getenv("CYBERMIAO_RECORD_MAX_MB", "1024")) * 1024 * 1024,
                   int(os.getenv("CYBERMIAO_RECORD_MIN_FREE_MB", "512")) * 1024 * 1024,
                   float(os.getenv("CYBERMIAO_RECORD_CLEAN_INTERVAL_SECONDS", "300")))


def is_link(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


class RecordRepository:
    SESSION = re.compile(r"[0-9]{8}-[0-9]{6}\Z")
    IMAGE = re.compile(r"[0-9]{13,16}_[0-9]{4,}\.jpg\Z")

    def __init__(self, root: Path, settings: StorageSettings):
        self.root = Path(root).resolve()
        self.settings = settings

    def inventory(self):
        if not self.root.exists():
            return []
        entries = []
        for device in self.root.iterdir():
            try:
                validate_device_id(device.name)
                if is_link(device) or not device.is_dir():
                    continue
                for session in device.iterdir():
                    if not self.SESSION.fullmatch(session.name) or is_link(session) or not session.is_dir():
                        continue
                    for image in session.iterdir():
                        if not self.IMAGE.fullmatch(image.name) or is_link(image):
                            continue
                        info = image.stat()
                        if stat.S_ISREG(info.st_mode):
                            entries.append((info.st_mtime, image, info.st_size))
            except (OSError, ValueError):
                continue
        return sorted(entries, key=lambda item: (item[0], str(item[1])))

    def cleanup(self, protected, *, incoming=0):
        entries = self.inventory()
        total = sum(size for _, _, size in entries)
        cutoff = time.time() - self.settings.retention_days * 86400
        removed, freed = 0, 0
        for modified, path, size in entries:
            if path in protected or (modified >= cutoff and total + incoming <= self.settings.max_bytes):
                continue
            # 再次核对范围和链接，清理只针对应用的三层录制图片路径。
            if not path.resolve().is_relative_to(self.root) or is_link(path) or any(is_link(p) for p in (path.parent, path.parent.parent)):
                continue
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            total -= size
            removed += 1
            freed += size
        return {"managed_bytes": total, "removed_images": removed, "freed_bytes": freed}

    def write(self, path: Path, data: bytes, protected):
        path = Path(path).absolute()
        if not path.resolve().is_relative_to(self.root) or len(path.relative_to(self.root).parts) != 3:
            raise ValueError("录制图片路径越界")
        if os.path.lexists(path):
            raise FileExistsError("录制图片已存在，拒绝覆盖")
        report = self.cleanup(protected, incoming=len(data))
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        if report["managed_bytes"] + len(data) > self.settings.max_bytes:
            raise RecordingSpaceError("录制容量已满，当前使用中的图片受到保护")
        if shutil.disk_usage(self.root).free < self.settings.min_free_bytes + len(data):
            raise RecordingSpaceError("磁盘剩余空间不足，暂停生成录制图片")
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if any(is_link(p) for p in (path.parent, path.parent.parent)) or not path.resolve().is_relative_to(self.root):
            raise ValueError("录制目录不能是链接")
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
        except BaseException:
            path.unlink(missing_ok=True)
            raise


class RuntimeStorage:
    def __init__(self, root: Path, settings=None, protected_paths=None):
        self.settings = settings or StorageSettings.from_environment()
        self.repository = RecordRepository(root, self.settings)
        self.protected_paths = protected_paths or (lambda: set())
        self._pins = set()
        self._lock = asyncio.Lock()
        self._task = None
        self.last_report = None
        self.last_error_type = None

    @contextmanager
    def pin(self, path):
        path = Path(path).absolute()
        self._pins.add(path)
        try:
            yield
        finally:
            self._pins.discard(path)

    def protected(self):
        return self._pins | {Path(path).absolute() for path in self.protected_paths()}

    async def write_image(self, path, data):
        async with self._lock:
            await run_blocking(self.repository.write, Path(path), data, self.protected())

    async def run_once(self):
        async with self._lock:
            self.last_report = await run_blocking(self.repository.cleanup, self.protected())
            self.last_error_type = None
            return self.last_report

    async def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def close(self):
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self):
        while True:
            try:
                report = await self.run_once()
                if report["removed_images"]:
                    log_event(f"[STORAGE] removed_images={report['removed_images']} freed_bytes={report['freed_bytes']}")
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.last_error_type = type(error).__name__
                log_event(f"[STORAGE] cleanup_failed type={self.last_error_type}")
            await asyncio.sleep(self.settings.interval_seconds)
