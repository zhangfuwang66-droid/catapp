"""初始化、检查与一致性备份记忆数据库；输出仅包含元数据。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import shutil
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory_store import MemoryStore
from database_schema import MEMORY_COLUMNS, IDENTITY_COLUMNS, KNOWLEDGE_COLUMNS, validate_columns, validate_versions


TABLES = ("conversations", "messages", "conversation_summaries", "memories")


def file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def database_sidecars(path: Path):
    return [path.with_name(path.name + suffix) for suffix in ("-wal", "-shm", "-journal")]


def ensure_new_database_path(path: Path):
    if any(os.path.lexists(candidate) for candidate in [path, *database_sidecars(path)]):
        raise FileExistsError("目标数据库或其 WAL/SHM/journal 文件已存在，拒绝覆盖")


def open_readonly(database: Path) -> sqlite3.Connection:
    database = database.expanduser().resolve(strict=True)
    if not database.is_file():
        raise ValueError("数据库路径必须是文件")
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=10)
    connection.execute("PRAGMA query_only = ON")
    return connection


def check_database_ready(database: Path):
    """就绪探针只检查可读和必需表，不全表扫描，不把详情暴露给公网。"""
    connection = open_readonly(database)
    try:
        connection.execute("BEGIN")
        versions = validate_versions(connection)
        validate_columns(connection, {**MEMORY_COLUMNS, **IDENTITY_COLUMNS})
        if versions.get("knowledge"):
            validate_columns(connection, KNOWLEDGE_COLUMNS)
        for table in (*MEMORY_COLUMNS, *IDENTITY_COLUMNS):
            connection.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone()
    finally:
        connection.close()


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
        versions = validate_versions(connection)
        validate_columns(connection, MEMORY_COLUMNS)
        identity_version = versions.get("identity", 0)
        identity_tables = set(IDENTITY_COLUMNS).intersection(names)
        if identity_tables or identity_version:
            required = {name: columns for name, columns in IDENTITY_COLUMNS.items()
                        if identity_version >= 2 or name.startswith("auth_")}
            if not identity_version or identity_tables != set(required):
                raise ValueError("身份数据库表结构与版本记录不一致")
            validate_columns(connection, required)
        knowledge_tables = set(KNOWLEDGE_COLUMNS).intersection(names)
        if knowledge_tables or versions.get("knowledge"):
            if not versions.get("knowledge") or knowledge_tables != set(KNOWLEDGE_COLUMNS):
                raise ValueError("资料库表结构与版本记录不一致")
            validate_columns(connection, KNOWLEDGE_COLUMNS)
            if connection.execute("""SELECT 1 FROM knowledge_documents d WHERE d.size_bytes<>length(d.original)
                OR d.chunk_count<>(SELECT count(*) FROM knowledge_chunks c WHERE c.document_id=d.id AND c.user_id=d.user_id)
                LIMIT 1""").fetchone():
                raise ValueError("资料库原文或片段计数不一致")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("数据库存在外键关联异常")
        for query in (
            """SELECT 1 FROM memories m JOIN conversations c ON c.id=m.source_conversation_id
               WHERE m.user_id<>c.user_id OR m.device_id<>c.device_id LIMIT 1""",
            """SELECT 1 FROM conversation_summaries s WHERE NOT EXISTS
               (SELECT 1 FROM messages m WHERE m.id=s.through_message_id AND m.conversation_id=s.conversation_id) LIMIT 1""",
            """SELECT 1 FROM conversations c WHERE c.summarized_through_message_id<>0 AND NOT EXISTS
               (SELECT 1 FROM messages m WHERE m.id=c.summarized_through_message_id AND m.conversation_id=c.id) LIMIT 1""",
        ):
            if connection.execute(query).fetchone() is not None:
                raise ValueError("数据库存在记忆归属或总结进度关联异常")
        if identity_version >= 2:
            for table in ("conversations", "memories"):
                if connection.execute(f"""SELECT 1 FROM {table} c JOIN auth_users u ON u.id=c.user_id
                    LEFT JOIN devices d ON d.id=c.device_id
                    WHERE d.id IS NULL OR d.owner_user_id IS NULL OR d.owner_user_id<>c.user_id LIMIT 1""").fetchone():
                    raise ValueError("数据库存在账号与设备归属不一致的记忆")
        counts = {name: connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in TABLES}
        identity_counts = {name: connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                           for name in sorted(identity_tables)}
        knowledge_counts = {name: connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                            for name in sorted(knowledge_tables)}
        pending = connection.execute("""SELECT COUNT(*) FROM conversations c WHERE EXISTS
            (SELECT 1 FROM messages m WHERE m.conversation_id=c.id AND m.id>c.summarized_through_message_id)""").fetchone()[0]
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        page_count = connection.execute("PRAGMA page_count").fetchone()[0]
        free_pages = connection.execute("PRAGMA freelist_count").fetchone()[0]
        wal = database.with_name(database.name + "-wal")
        try:
            wal_bytes = wal.stat().st_size
        except FileNotFoundError:
            wal_bytes = 0
        return {"ok": True, "database": str(database), "integrity": "ok", "foreign_keys": "ok",
                "relations": "ok", "schema_versions": versions, "row_counts": counts,
                "identity_row_counts": identity_counts, "knowledge_row_counts": knowledge_counts,
                "pending_conversations": pending,
                "storage": {"database_bytes": database.stat().st_size, "wal_bytes": wal_bytes,
                            "logical_bytes": page_count * page_size, "reusable_bytes": free_pages * page_size,
                            "disk_free_bytes": shutil.disk_usage(database.parent).free}}
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
    ensure_new_database_path(output)
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
        return {**report, "source": str(database), "bytes": output.stat().st_size, "sha256": file_sha256(output)}
    except BaseException:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()
            source = None
        # 只清理本次独占创建的备份文件；原始数据库保持只读。
        output.unlink(missing_ok=True)
        for sidecar in database_sidecars(output):
            sidecar.unlink(missing_ok=True)
        raise
    finally:
        if source is not None:
            source.close()


def restore_database(backup: Path, output: Path, expected_sha256: str, *, apply: bool = False,
                     offline: bool = False) -> dict:
    """只恢复到新路径。默认预览，实际执行必须确认停服并核对独立备份的校验值。"""
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256 or ""):
        raise ValueError("请提供备份时另行保存的完整 SHA-256")
    if apply and not offline:
        raise ValueError("实际恢复需要 --confirm-service-stopped，请先停止后端并保存当前数据库备份")
    backup = backup.expanduser().resolve(strict=True)
    output = output.expanduser().resolve()
    ensure_new_database_path(output)
    digest = file_sha256(backup)
    if digest != expected_sha256.lower():
        raise ValueError("备份 SHA-256 不匹配，未创建恢复文件")
    connection = open_readonly(backup)
    try:
        journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
        if journal.lower() != "delete" or any(path.exists() for path in database_sidecars(backup)):
            raise ValueError("恢复源必须是独立备份文件，请先通过 backup 命令生成，不能使用运行中的数据库")
    finally:
        connection.close()
    source_report = inspect_database(backup)
    plan = {"ok": True, "applied": False, "backup": str(backup), "output": str(output),
            "source_sha256": digest, "row_counts": source_report["row_counts"],
            "identity_row_counts": source_report["identity_row_counts"],
            "knowledge_row_counts": source_report["knowledge_row_counts"],
            "schema_versions": source_report["schema_versions"],
            "sessions_will_be_revoked": True, "pairing_codes_will_be_invalidated": True,
            "device_credentials": "恢复到备份时的状态；之后的撤销记录不会包含在旧备份中，请核对后再上线"}
    if not apply:
        return plan
    output.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    # 先在同一文件系统的私密临时目录完成备份、会话撤销和验证，最后无覆盖发布。
    with tempfile.TemporaryDirectory(prefix=".cybermiao-restore-", dir=output.parent) as directory:
        staged = Path(directory) / "restored.sqlite3"
        backup_database(backup, staged)
        connection = sqlite3.connect(staged)
        try:
            now = time.time()
            session_count = 0
            pairing_count = 0
            tables = source_report["identity_row_counts"]
            if "auth_sessions" in tables:
                session_count = connection.execute("UPDATE auth_sessions SET revoked_at=? WHERE revoked_at IS NULL", (now,)).rowcount
            if "device_pairings" in tables:
                pairing_count = connection.execute("""UPDATE device_pairings SET invalidated_at=?
                    WHERE claimed_at IS NULL AND invalidated_at IS NULL""", (now,)).rowcount
            connection.commit()
        finally:
            connection.close()
        inspect_database(staged)
        if file_sha256(backup) != digest:
            raise ValueError("恢复过程中源备份发生变化，未发布恢复文件")
        with staged.open("r+b") as stream:
            os.fsync(stream.fileno())
        ensure_new_database_path(output)
        os.link(staged, output)  # 同盘硬链接发布，不允许覆盖并发创建的同名目标。
        try:
            report = inspect_database(output)
            return {**plan, "applied": True, "restored": report, "sha256": file_sha256(output),
                    "revoked_sessions": session_count, "invalidated_pairings": pairing_count}
        except BaseException:
            output.unlink()
            raise


def backup_status(database: Path, directory: Path):
    # 延迟导入，避免自动备份服务与运维工具的模块导入互相依赖。
    from database_backups import BackupRepository, BackupSettings
    repository = BackupRepository(database, BackupSettings(directory))
    entries, skipped = repository.verified_entries()
    latest = None
    if entries:
        created, path, _ = entries[-1]
        latest = {"file": path.name, "created_at": created, "sha256": file_sha256(path)}
    return {"ok": True, "valid_backups": len(entries), "skipped_unverified": skipped, "latest": latest}


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "inspect", "backup"):
        command = subparsers.add_parser(name)
        command.add_argument("--database", required=True, type=Path)
        if name == "backup":
            command.add_argument("--output", required=True, type=Path)
    restore = subparsers.add_parser("restore", help="默认只读预览；仅恢复到不存在的新路径")
    restore.add_argument("--backup", required=True, type=Path)
    restore.add_argument("--output", required=True, type=Path)
    restore.add_argument("--sha256", required=True)
    restore.add_argument("--apply", action="store_true")
    restore.add_argument("--confirm-service-stopped", action="store_true")
    status = subparsers.add_parser("backup-status", help="只读检查自动备份数量与最新校验值")
    status.add_argument("--database", required=True, type=Path)
    status.add_argument("--directory", required=True, type=Path)
    options = parser.parse_args(arguments)
    try:
        if options.command == "backup-status":
            report = backup_status(options.database, options.directory)
        elif options.command == "restore":
            report = restore_database(options.backup, options.output, options.sha256,
                                      apply=options.apply, offline=options.confirm_service_stopped)
        elif options.command == "init":
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
