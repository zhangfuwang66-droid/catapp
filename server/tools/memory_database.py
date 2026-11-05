"""初始化、检查与一致性备份记忆数据库；输出仅包含元数据。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory_store import MemoryStore


TABLES = ("conversations", "messages", "conversation_summaries", "memories")


def open_readonly(database: Path) -> sqlite3.Connection:
    database = database.expanduser().resolve(strict=True)
    if not database.is_file():
        raise ValueError("数据库路径必须是文件")
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=10)
    connection.execute("PRAGMA query_only = ON")
    return connection


def inspect_database(database: Path) -> dict:
    database = database.expanduser().resolve(strict=True)
    connection = open_readonly(database)
    try:
        connection.execute("BEGIN")
        integrity = [row[0] for row in connection.execute("PRAGMA quick_check")]
        if integrity != ["ok"]:
            raise ValueError("数据库完整性检查失败")
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not set(TABLES).issubset(names):
            raise ValueError("数据库缺少聊天记忆所需的数据表")
        counts = {name: connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in TABLES}
        return {"ok": True, "database": str(database), "integrity": "ok", "row_counts": counts}
    finally:
        connection.close()


def initialize_database(database: Path) -> dict:
    database = database.expanduser().resolve()
    database.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    # 独占创建，已有数据库即使为空也不覆盖。
    descriptor = os.open(database, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    MemoryStore(database)
    return {**inspect_database(database), "created": True}


def backup_database(database: Path, output: Path, *, timeout: float = 30) -> dict:
    if timeout <= 0:
        raise ValueError("备份超时必须大于零")
    database = database.expanduser().resolve(strict=True)
    inspect_database(database)
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    source = None
    destination = None
    started = time.monotonic()

    def progress(_status: int, _remaining: int, _total: int) -> None:
        if time.monotonic() - started > timeout:
            raise TimeoutError("数据库备份超时")

    try:
        source = open_readonly(database)
        destination = sqlite3.connect(output)
        source.backup(destination, pages=256, progress=progress, sleep=0.05)
        # 让备份独立于 WAL/SHM 旁文件，可直接拷贝到其他机器恢复。
        destination.execute("PRAGMA journal_mode = DELETE")
        destination.close()
        destination = None
        report = inspect_database(output)
        with output.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest() if sys.version_info >= (3, 11) else None
            if digest is None:
                hasher = hashlib.sha256()
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(block)
                digest = hasher.hexdigest()
        return {**report, "source": str(database), "bytes": output.stat().st_size, "sha256": digest}
    except BaseException:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()
            source = None
        # 只清理本次独占创建的备份文件；原始数据库保持只读。
        output.unlink(missing_ok=True)
        raise
    finally:
        if source is not None:
            source.close()


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "inspect", "backup"):
        command = subparsers.add_parser(name)
        command.add_argument("--database", required=True, type=Path)
        if name == "backup":
            command.add_argument("--output", required=True, type=Path)
    options = parser.parse_args(arguments)
    try:
        if options.command == "init":
            report = initialize_database(options.database)
        elif options.command == "backup":
            report = backup_database(options.database, options.output)
        else:
            report = inspect_database(options.database)
    except (OSError, ValueError, sqlite3.Error, TimeoutError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    if os.name != "nt":
        os.umask(0o077)
    raise SystemExit(main())
