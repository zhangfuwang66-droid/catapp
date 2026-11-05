"""离线迁移旧测试身份的聊天记忆；默认只读预览，不输出聊天正文。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from device_identity import validate_device_id
from identity_store import MIGRATIONS
from tools.memory_database import backup_database, open_readonly


def owner_part(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError("源和目标身份必须为 1～128 个字符")
    return value.strip()


def counts(connection, owner):
    conversations = connection.execute(
        "SELECT COUNT(*) FROM conversations WHERE user_id=? AND device_id=?", owner,
    ).fetchone()[0]
    result = {"conversations": conversations}
    for table in ("messages", "conversation_summaries"):
        result[table] = connection.execute(
            f"SELECT COUNT(*) FROM {table} AS m JOIN conversations AS c ON c.id=m.conversation_id "
            "WHERE c.user_id=? AND c.device_id=?", owner,
        ).fetchone()[0]
    result["memories"] = connection.execute(
        "SELECT COUNT(*) FROM memories WHERE user_id=? AND device_id=?", owner,
    ).fetchone()[0]
    return result


def validate_plan(connection, source, target):
    if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise ValueError("数据库完整性检查失败")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("数据库存在外键异常，请先检查数据")
    version = connection.execute("SELECT version FROM schema_migrations WHERE component='identity'").fetchone()
    if version is None or version[0] != len(MIGRATIONS):
        raise ValueError("请先使用匹配版本的身份管理工具完成账号和设备登记")
    user = connection.execute("SELECT disabled FROM auth_users WHERE id=?", (target[0],)).fetchone()
    device = connection.execute("SELECT owner_user_id, revoked_at FROM devices WHERE id=?", (target[1],)).fetchone()
    if user is None or user[0] or device is None or device[0] != target[0] or device[1] is not None:
        raise ValueError("目标必须是有效账号及其已绑定、未停用的设备")
    if connection.execute("SELECT 1 FROM auth_users WHERE id=?", (source[0],)).fetchone():
        raise ValueError("此工具仅迁移旧测试身份，不能转移真实账号的记忆")
    source_device = connection.execute("SELECT owner_user_id FROM devices WHERE id=?", (source[1],)).fetchone()
    if source_device is not None and source_device[0] is not None:
        raise ValueError("源设备已经绑定真实账号，不能作为旧测试设备迁移")
    target_counts = counts(connection, target)
    if any(target_counts.values()):
        raise ValueError("目标身份已有记忆或会话，请人工核对，工具不会合并或覆盖")
    # 摘要提取的事实可能引用会话；拒绝已有跨归属引用，避免扩大历史错误。
    inconsistent = connection.execute(
        """SELECT 1 FROM memories AS m JOIN conversations AS c ON c.id=m.source_conversation_id
           WHERE (m.user_id<>c.user_id OR m.device_id<>c.device_id)
             AND ((m.user_id=? AND m.device_id=?) OR (c.user_id=? AND c.device_id=?)) LIMIT 1""",
        (*source, *source),
    ).fetchone()
    if inconsistent:
        raise ValueError("源记忆存在跨归属引用，请先人工检查")
    return counts(connection, source)


def migrate_memory(database: Path, source_user: str, source_device: str, target_user: str, target_device: str,
                   *, apply: bool = False, backup: Path | None = None, offline: bool = False) -> dict:
    source = (owner_part(source_user), owner_part(source_device))
    target = (owner_part(target_user), validate_device_id(target_device))
    if source == target:
        raise ValueError("源身份和目标身份不能相同")
    if apply and (backup is None or not offline):
        raise ValueError("执行迁移必须指定新备份文件，并通过 --confirm-service-stopped 确认服务已停止")
    if not apply and backup is not None:
        raise ValueError("只读预览不创建备份；执行时再同时指定 --apply 和 --backup")
    database = database.expanduser().resolve(strict=True)
    if apply:
        connection = sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, timeout=10)
        connection.execute("PRAGMA foreign_keys=ON")
    else:
        connection = open_readonly(database)
    try:
        # 执行时先占用写锁，再检查、备份和更新，防止检查与提交之间夹入其他写入。
        connection.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        row_counts = validate_plan(connection, source, target)
        report = {"ok": True, "applied": False, "source": {"user_id": source[0], "device_id": source[1]},
                  "target": {"user_id": target[0], "device_id": target[1]}, "row_counts": row_counts}
        if not apply:
            return report
        if not any(row_counts.values()):
            raise ValueError("源身份没有可迁移数据，未执行任何更改")
        report["backup"] = backup_database(database, backup)
        for table in ("conversations", "memories"):
            connection.execute(f"UPDATE {table} SET user_id=?, device_id=? WHERE user_id=? AND device_id=?",
                               (*target, *source))
        if counts(connection, target) != row_counts or any(counts(connection, source).values()):
            raise ValueError("迁移数量核对失败，事务已回滚，请保留备份并检查")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("迁移后的外键检查失败，事务已回滚")
        connection.commit()
        return {**report, "applied": True}
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    for argument in ("source-user", "source-device", "target-user", "target-device"):
        parser.add_argument("--" + argument, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--confirm-service-stopped", action="store_true")
    options = parser.parse_args(arguments)
    try:
        report = migrate_memory(options.database, options.source_user, options.source_device,
                                options.target_user, options.target_device, apply=options.apply,
                                backup=options.backup, offline=options.confirm_service_stopped)
    except (OSError, ValueError, sqlite3.Error, TimeoutError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    if os.name != "nt":
        os.umask(0o077)
    raise SystemExit(main())
