"""可配置的定时一致性备份；只轮换本组件为同一源库创建且校验通过的文件。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import Optional
import uuid

from async_helpers import run_blocking
from runtime_logging import log_event
from tools.memory_database import backup_database, file_sha256, inspect_database


@dataclass(frozen=True)
class BackupSettings:
    directory: Optional[Path] = None
    interval_seconds: float = 21600
    retain: int = 28
    min_free_bytes: int = 256 * 1024 * 1024
    timeout_seconds: float = 30

    def __post_init__(self):
        if not math.isfinite(self.interval_seconds) or self.interval_seconds < .1:
            raise ValueError("备份间隔必须是大于零的有限数值")
        if not isinstance(self.retain, int) or not 1 <= self.retain <= 1000:
            raise ValueError("备份保留数量需为 1～1000")
        if not isinstance(self.min_free_bytes, int) or self.min_free_bytes < 0:
            raise ValueError("备份磁盘预留空间不能小于零")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("备份超时必须是大于零的有限数值")

    @classmethod
    def from_environment(cls):
        directory = os.getenv("CYBERMIAO_BACKUP_DIR", "").strip()
        if not directory:
            return cls()  # 未显式配置时不写入备份、不删除文件。
        interval = float(os.getenv("CYBERMIAO_BACKUP_INTERVAL_SECONDS", "21600"))
        if interval < 60:
            raise ValueError("自动备份间隔至少 60 秒")
        return cls(Path(directory).expanduser().resolve(), interval,
                   int(os.getenv("CYBERMIAO_BACKUP_KEEP", "28")),
                   int(os.getenv("CYBERMIAO_BACKUP_MIN_FREE_MB", "256")) * 1024 * 1024,
                   float(os.getenv("CYBERMIAO_BACKUP_TIMEOUT_SECONDS", "30")))


class BackupRepository:
    def __init__(self, database: Path, settings: BackupSettings):
        self.database = database.expanduser().resolve()
        self.settings = settings
        self.directory = Path(settings.directory).expanduser().resolve() if settings.directory is not None else None
        source = os.path.normcase(str(self.database))
        self.source_id = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
        self.pattern = re.compile(r"cybermiao-" + self.source_id + r"-\d{8}T\d{12}Z-[0-9a-f]{8}\.sqlite3\Z")

    def verified_entries(self):
        if self.directory is None or not self.directory.exists():
            return [], 0
        entries, skipped = [], 0
        for path in self.directory.iterdir():
            if not self.pattern.fullmatch(path.name):
                continue
            receipt = path.with_suffix(".json")
            try:
                if path.is_symlink() or receipt.is_symlink() or not path.is_file() or not receipt.is_file():
                    raise ValueError("不完整的备份记录")
                if (self.database.exists() and path.samefile(self.database)) or receipt.stat().st_size > 8192:
                    raise ValueError("无效的备份记录")
                manifest = json.loads(receipt.read_text(encoding="utf-8"))
                created = manifest["created_at"]
                if (manifest.get("format") != 1 or manifest.get("source_id") != self.source_id
                        or manifest.get("file") != path.name or type(created) not in (int, float)
                        or not 0 <= created <= time.time() + 300 or not math.isfinite(created)
                        or path.stat().st_size != manifest.get("bytes") or file_sha256(path) != manifest.get("sha256")):
                    raise ValueError("备份校验失败")
                entries.append((float(created), path, receipt))
            except (OSError, ValueError, TypeError, KeyError):
                skipped += 1
        return sorted(entries, key=lambda entry: (entry[0], entry[1].name)), skipped

    def create(self):
        if self.directory is None:
            raise ValueError("未配置自动备份目录")
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        report = inspect_database(self.database)
        needed = self.settings.min_free_bytes + 2 * report["storage"]["logical_bytes"]
        if shutil.disk_usage(self.directory).free < needed:
            raise OSError("备份目录剩余磁盘空间不足，保留原数据库及已有备份")
        created = time.time()
        stamp = datetime.fromtimestamp(created, timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        name = f"cybermiao-{self.source_id}-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3"
        target = self.directory / name
        receipt = target.with_suffix(".json")
        complete = False
        owned_target = False
        try:
            result = backup_database(self.database, target, timeout=self.settings.timeout_seconds)
            owned_target = True
            manifest = {"format": 1, "source_id": self.source_id, "file": name, "created_at": created,
                        "bytes": result["bytes"], "sha256": result["sha256"],
                        "schema_versions": result["schema_versions"], "row_counts": result["row_counts"],
                        "identity_row_counts": result["identity_row_counts"]}
            with target.open("r+b") as stream:
                os.fsync(stream.fileno())
            with tempfile.TemporaryDirectory(prefix=".cybermiao-receipt-", dir=self.directory) as directory:
                staged = Path(directory) / "receipt.json"
                descriptor = os.open(staged, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(manifest, stream, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(staged, receipt)
            complete = True
            return created
        finally:
            if owned_target and not complete:
                target.unlink(missing_ok=True)

    def prune(self, entries):
        removed = 0
        for _, path, receipt in entries[:-self.settings.retain]:
            # 路径来自本目录的严格命名匹配，删除前再次验证摘要、同源与符号链接。
            current, _ = self.verified_entries()
            if not any(candidate == path for _, candidate, _ in current):
                continue
            path.unlink()
            receipt.unlink()
            removed += 1
        return removed


class DatabaseBackups:
    def __init__(self, database: Path, settings: BackupSettings):
        self.settings = settings
        self.repository = BackupRepository(database, settings)
        self._task = None
        self._lock = asyncio.Lock()
        self.last_success_at = None
        self.last_failure_at = None
        self.last_error_type = None
        self.backup_count = 0
        self.skipped_unverified = 0
        self.pruned = 0

    def status(self):
        return {"enabled": self.settings.directory is not None,
                "running": self._lock.locked(),
                "scheduler_running": bool(self._task and not self._task.done()),
                "last_success_at": self.last_success_at, "last_failure_at": self.last_failure_at,
                "last_error_type": self.last_error_type, "backup_count": self.backup_count,
                "skipped_unverified": self.skipped_unverified, "pruned": self.pruned}

    async def start(self):
        if self.settings.directory is not None and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._loop())

    async def close(self):
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def run_once(self, *, authorize=None):
        if self.settings.directory is None:
            return False
        async with self._lock:
            # 管理请求排队期间可能已撤销；开始读写备份前复核，调度任务沿用原行为。
            if authorize is not None:
                await authorize()
            try:
                entries, skipped = await run_blocking(self.repository.verified_entries)
                self.skipped_unverified = skipped
                self.backup_count = len(entries)
                self.last_success_at = entries[-1][0] if entries else None
                if entries and time.time() - entries[-1][0] < self.settings.interval_seconds:
                    self.pruned = await run_blocking(self.repository.prune, entries)
                    self.backup_count -= self.pruned
                    self.last_error_type = None
                    return False
                self.last_success_at = await run_blocking(self.repository.create)
                entries, skipped = await run_blocking(self.repository.verified_entries)
                self.pruned = await run_blocking(self.repository.prune, entries)
                self.backup_count = len(entries) - self.pruned
                self.skipped_unverified = skipped
                self.last_error_type = None
                return True
            except Exception as exc:
                self.last_failure_at = time.time()
                self.last_error_type = type(exc).__name__
                raise

    async def _loop(self):
        while True:
            try:
                await self.run_once()
                until = self.settings.interval_seconds - (time.time() - (self.last_success_at or time.time()))
                delay = max(.1, until)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # 不转发原始异常正文，避免文件路径或 SQL 内容混进普通服务日志。
                log_event(f"[BACKUP] failed ({type(exc).__name__}); will retry", flush=True)
                delay = min(60, self.settings.interval_seconds)
            await asyncio.sleep(delay)
