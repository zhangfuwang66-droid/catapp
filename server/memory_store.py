"""持久化聊天记录与长期记忆；不依赖 FastAPI 或具体模型实现。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Awaitable, Callable, Dict, Iterable, List, Optional, Set
from async_helpers import run_blocking
from database_schema import MEMORY_COLUMNS, MEMORY_SCHEMA_VERSION, validate_columns, validate_versions


ROLES = {"user", "assistant", "system"}


@dataclass(frozen=True)
class SummaryResult:
    """一次空闲总结的结构；空摘要不会被写入数据库。"""

    summary: str
    memories: tuple[str, ...] = ()
    open_topics: tuple[str, ...] = ()


class MemoryStore:
    """使用 SQLite 保存会话、消息、摘要和长期记忆。

    每个操作独立建立连接，配合 WAL 和进程内锁，既能被 FastAPI 事件循环
    通过 ``run_blocking`` 调用，也不会把 sqlite 连接跨线程复用。
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        device_id TEXT NOT NULL,
        started_at TEXT NOT NULL,
        last_activity_at TEXT NOT NULL,
        summarized_through_message_id INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_conversations_owner_activity
        ON conversations (user_id, device_id, last_activity_at DESC);

    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
        content TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_messages_conversation_id
        ON messages (conversation_id, id);

    CREATE TABLE IF NOT EXISTS conversation_summaries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        through_message_id INTEGER NOT NULL,
        summary TEXT NOT NULL,
        open_topics_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_summaries_conversation_created
        ON conversation_summaries (conversation_id, created_at DESC);

    CREATE TABLE IF NOT EXISTS memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        device_id TEXT NOT NULL,
        content TEXT NOT NULL,
        source_conversation_id INTEGER REFERENCES conversations(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        UNIQUE (user_id, device_id, content)
    );
    CREATE INDEX IF NOT EXISTS idx_memories_owner_updated
        ON memories (user_id, device_id, active, updated_at DESC);
    """

    def __init__(self, path: Path | str):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        self._lock = threading.RLock()
        self._initialize()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                    component TEXT PRIMARY KEY, version INTEGER NOT NULL
                )""")
                version = validate_versions(connection).get("memory", 0)
                if version == 0:
                    # 将无版本的旧表登记为第一版；已存在但不完整的表不静默修补。
                    names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    existing = names.intersection(MEMORY_COLUMNS)
                    if existing:
                        validate_columns(connection, MEMORY_COLUMNS)
                    for statement in self._SCHEMA.split(";"):
                        if statement.strip():
                            connection.execute(statement)
                    connection.execute("INSERT OR REPLACE INTO schema_migrations(component, version) VALUES ('memory', ?)",
                                       (MEMORY_SCHEMA_VERSION,))
                validate_columns(connection, MEMORY_COLUMNS)
                connection.commit()
                connection.execute("PRAGMA journal_mode = WAL")
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    @staticmethod
    def _row_dict(row: sqlite3.Row) -> Dict[str, object]:
        return dict(row)

    def create_conversation(self, user_id: str, device_id: str) -> int:
        user_id = self._required_owner_part(user_id, "user_id")
        device_id = self._required_owner_part(device_id, "device_id")
        now = self._now()
        with self._lock:
            connection = self._connect()
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO conversations
                        (user_id, device_id, started_at, last_activity_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (user_id, device_id, now, now),
                )
                connection.commit()
                return int(cursor.lastrowid)
            finally:
                connection.close()

    def append_message(self, conversation_id: int, role: str, content: str) -> int:
        if role not in ROLES:
            raise ValueError(f"不支持的消息角色：{role}")
        content = (content or "").strip()
        if not content:
            raise ValueError("消息内容不能为空")
        now = self._now()
        with self._lock:
            connection = self._connect()
            try:
                conversation = connection.execute(
                    "SELECT id FROM conversations WHERE id = ?", (conversation_id,)
                ).fetchone()
                if conversation is None:
                    raise ValueError(f"会话不存在：{conversation_id}")
                cursor = connection.execute(
                    """
                    INSERT INTO messages (conversation_id, role, content, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (conversation_id, role, content, now),
                )
                connection.execute(
                    "UPDATE conversations SET last_activity_at = ? WHERE id = ?",
                    (now, conversation_id),
                )
                if getattr(self, "retention_enabled", False):
                    connection.execute("""UPDATE memory_policies SET archived_at=NULL WHERE archived_at IS NOT NULL
                        AND (user_id,device_id) IN (SELECT user_id,device_id FROM conversations WHERE id=?)""", (conversation_id,))
                connection.commit()
                return int(cursor.lastrowid)
            finally:
                connection.close()

    def conversation_owner(self, conversation_id: int) -> Optional[Dict[str, str]]:
        with self._lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    "SELECT user_id, device_id FROM conversations WHERE id = ?",
                    (conversation_id,),
                ).fetchone()
                return None if row is None else {"user_id": row[0], "device_id": row[1]}
            finally:
                connection.close()

    def pending_messages(self, conversation_id: int, limit: Optional[int] = None) -> List[Dict[str, object]]:
        with self._lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    """
                    SELECT summarized_through_message_id
                    FROM conversations WHERE id = ?
                    """,
                    (conversation_id,),
                ).fetchone()
                if row is None:
                    return []
                messages = connection.execute(
                    """
                    SELECT id, conversation_id, role, content, created_at
                    FROM messages
                    WHERE conversation_id = ? AND id > ?
                    ORDER BY id LIMIT ?
                    """,
                    (conversation_id, row[0], -1 if limit is None else max(1, int(limit))),
                ).fetchall()
                return [self._row_dict(item) for item in messages]
            finally:
                connection.close()

    def due_conversation_ids(self, idle_seconds: float) -> List[int]:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max(0.0, idle_seconds))
        cutoff_text = cutoff.isoformat(timespec="seconds")
        with self._lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    """
                    SELECT c.id
                    FROM conversations AS c
                    WHERE c.last_activity_at <= ?
                      AND EXISTS (
                          SELECT 1 FROM messages AS m
                          WHERE m.conversation_id = c.id
                            AND m.id > c.summarized_through_message_id
                      )
                    ORDER BY c.id
                    """,
                    (cutoff_text,),
                ).fetchall()
                return [int(row[0]) for row in rows]
            finally:
                connection.close()

    def pending_conversation_ids(self) -> List[int]:
        """返回所有仍有未总结消息的会话，供进程启动恢复使用。"""
        with self._lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    """
                    SELECT c.id
                    FROM conversations AS c
                    WHERE EXISTS (
                        SELECT 1 FROM messages AS m
                        WHERE m.conversation_id = c.id
                          AND m.id > c.summarized_through_message_id
                    )
                    ORDER BY c.id
                    """
                ).fetchall()
                return [int(row[0]) for row in rows]
            finally:
                connection.close()

    def save_summary(
        self,
        conversation_id: int,
        through_message_id: int,
        summary: str,
        memories: Iterable[str] = (),
        open_topics: Iterable[str] = (),
    ) -> None:
        summary = (summary or "").strip()
        memories = self._clean_items(memories)
        open_topics = self._clean_items(open_topics)
        if not summary and not memories and not open_topics:
            raise ValueError("不能保存空摘要")
        if not isinstance(through_message_id, int) or isinstance(through_message_id, bool) or through_message_id <= 0:
            raise ValueError("摘要消息边界必须是有效消息编号")
        now = self._now()
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                owner = connection.execute(
                    """
                    SELECT user_id, device_id, summarized_through_message_id
                    FROM conversations WHERE id = ?
                    """,
                    (conversation_id,),
                ).fetchone()
                if owner is None:
                    raise ValueError(f"会话不存在：{conversation_id}")
                last_message = connection.execute(
                    """
                    SELECT id FROM messages
                    WHERE conversation_id = ? AND id = ?
                    """,
                    (conversation_id, through_message_id),
                ).fetchone()
                if last_message is None:
                    raise ValueError("摘要没有对应的消息")
                if through_message_id <= int(owner[2]):
                    return  # 迟到或重复的模型结果不覆盖已提交的摘要、事实与进度。
                through = through_message_id
                connection.execute(
                    """
                    INSERT INTO conversation_summaries
                        (conversation_id, through_message_id, summary,
                         open_topics_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (conversation_id, through, summary, json.dumps(open_topics, ensure_ascii=False), now),
                )
                connection.execute(
                    """
                    UPDATE conversations
                    SET summarized_through_message_id = ?
                    WHERE id = ?
                    """,
                    (through, conversation_id),
                )
                for content in memories:
                    connection.execute(
                        """
                        INSERT INTO memories
                            (user_id, device_id, content, source_conversation_id,
                             created_at, updated_at, active)
                        VALUES (?, ?, ?, ?, ?, ?, 1)
                        ON CONFLICT(user_id, device_id, content) DO UPDATE SET
                            source_conversation_id = excluded.source_conversation_id,
                            updated_at = excluded.updated_at,
                            active = 1
                        """,
                        (owner[0], owner[1], content, conversation_id, now, now),
                    )
                connection.commit()
            finally:
                connection.close()

    def context_for(
        self,
        user_id: str,
        device_id: str,
        *,
        message_limit: int = 12,
        summary_limit: int = 4,
        memory_limit: int = 20,
    ) -> Dict[str, List[Dict[str, object]]]:
        user_id = self._required_owner_part(user_id, "user_id")
        device_id = self._required_owner_part(device_id, "device_id")
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN")
                messages = connection.execute(
                    """
                    SELECT m.id, m.conversation_id, m.role, m.content, m.created_at
                    FROM messages AS m
                    JOIN conversations AS c ON c.id = m.conversation_id
                    WHERE c.user_id = ? AND c.device_id = ?
                      AND m.id > c.summarized_through_message_id
                    ORDER BY m.id DESC LIMIT ?
                    """,
                    (user_id, device_id, max(1, int(message_limit))),
                ).fetchall()
                summaries = connection.execute(
                    """
                    SELECT s.id, s.conversation_id, s.summary,
                           s.open_topics_json, s.created_at
                    FROM conversation_summaries AS s
                    JOIN conversations AS c ON c.id = s.conversation_id
                    WHERE c.user_id = ? AND c.device_id = ?
                    ORDER BY s.id DESC LIMIT ?
                    """,
                    (user_id, device_id, max(1, int(summary_limit))),
                ).fetchall()
                memories = connection.execute(
                    """
                    SELECT id, content, source_conversation_id, created_at, updated_at
                    FROM memories
                    WHERE user_id = ? AND device_id = ? AND active = 1
                    ORDER BY updated_at DESC, id DESC LIMIT ?
                    """,
                    (user_id, device_id, max(1, int(memory_limit))),
                ).fetchall()
                return {
                    "messages": [self._row_dict(item) for item in reversed(messages)],
                    "summaries": [self._summary_dict(item) for item in reversed(summaries)],
                    "memories": [self._row_dict(item) for item in memories],
                }
            finally:
                connection.close()

    def list_memories(self, user_id: str, device_id: str, limit: int = 100) -> List[Dict[str, object]]:
        return self.context_for(user_id, device_id, message_limit=1, summary_limit=1, memory_limit=limit)["memories"]

    def owner_conversation_ids(self, user_id: str, device_id: str) -> List[int]:
        with self._lock:
            connection = self._connect()
            try:
                return [row[0] for row in connection.execute(
                    "SELECT id FROM conversations WHERE user_id=? AND device_id=?", (user_id, device_id),
                )]
            finally:
                connection.close()

    def clear_owner(self, user_id: str, device_id: str) -> None:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute(
                    "DELETE FROM conversations WHERE user_id = ? AND device_id = ?",
                    (user_id, device_id),
                )
                connection.execute(
                    "DELETE FROM memories WHERE user_id = ? AND device_id = ?",
                    (user_id, device_id),
                )
                connection.commit()
            finally:
                connection.close()

    def _summary_dict(self, row: sqlite3.Row) -> Dict[str, object]:
        item = self._row_dict(row)
        try:
            item["open_topics"] = json.loads(item.pop("open_topics_json"))
        except (TypeError, ValueError):
            item["open_topics"] = []
        return item

    @staticmethod
    def _required_owner_part(value: str, name: str) -> str:
        value = (value or "").strip()
        if not value or len(value) > 128:
            raise ValueError(f"{name} 不能为空且不能超过 128 个字符")
        return value

    @staticmethod
    def _clean_items(items: Iterable[str]) -> List[str]:
        result: List[str] = []
        seen: Set[str] = set()
        for item in items:
            item = (item or "").strip()
            if item and len(item) <= 500 and item not in seen:
                result.append(item)
                seen.add(item)
        return result


SummaryCallback = Callable[[List[Dict[str, object]]], Awaitable[SummaryResult]]


class MemoryManager:
    """在消息写入后安排空闲总结，并负责服务重启后的补偿扫描。"""

    def __init__(
        self,
        store: MemoryStore,
        summarize: SummaryCallback,
        *,
        idle_seconds: float = 300.0,
        poll_seconds: float = 15.0,
        on_error: Optional[Callable[[Exception], None]] = None,
        clock=time.monotonic,
    ):
        self.store = store
        self.summarize = summarize
        self.idle_seconds = max(0.0, float(idle_seconds))
        self.poll_seconds = max(0.1, float(poll_seconds))
        self.on_error = on_error
        self.clock = clock
        self._retry_after = {}
        self._retry_attempts = {}
        self._user_summary_locks = {}
        self._summary_tasks: Dict[int, asyncio.Task] = {}
        self._summarizing: Set[int] = set()
        self._active_summaries: Dict[int, asyncio.Task] = {}
        self._maintenance_task: Optional[asyncio.Task] = None
        self._closed = False

    async def start(self) -> None:
        if self._maintenance_task is None or self._maintenance_task.done():
            self._closed = False
            self._maintenance_task = asyncio.create_task(self._maintenance_loop())
            await self.recover_pending()

    async def recover_pending(self) -> None:
        """服务重新启动后立即安排所有未总结会话，不依赖上次空闲计时器。"""
        conversation_ids = await run_blocking(self.store.pending_conversation_ids)
        for conversation_id in conversation_ids:
            self._schedule_summary(conversation_id, 0)

    async def finish_conversation(self, conversation_id: int) -> None:
        """连接断开或页面关闭时立即安排一次总结。"""
        self._schedule_summary(conversation_id, 0)

    async def flush_pending(self, timeout: float = 4.0) -> None:
        """在正常关机的短窗口内冲刷未总结消息；超时则留给下次启动恢复。"""
        conversation_ids = await run_blocking(self.store.pending_conversation_ids)
        if not conversation_ids:
            return
        to_cancel = []
        for conversation_id in conversation_ids:
            task = self._summary_tasks.pop(conversation_id, None)
            if task is not None and not task.done():
                task.cancel()
                to_cancel.append(task)
        if to_cancel:
            await asyncio.gather(*to_cancel, return_exceptions=True)
        tasks = [asyncio.create_task(self.summarize_now(conversation_id)) for conversation_id in conversation_ids]
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=max(0.1, timeout),
            )
            for result in results:
                if isinstance(result, Exception) and self.on_error is not None:
                    self.on_error(result)
        except asyncio.TimeoutError:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self.on_error is not None:
                self.on_error(TimeoutError("关机前记忆总结超时，将在下次启动时重试"))

    async def close(self) -> None:
        self._closed = True
        tasks = list(set(self._summary_tasks.values()) | set(self._active_summaries.values()))
        if self._maintenance_task is not None:
            self._maintenance_task.cancel()
            tasks.append(self._maintenance_task)
            self._maintenance_task = None
        self._summary_tasks.clear()
        tasks = [task for task in tasks if task is not asyncio.current_task()]
        for task in tasks:
            if task is not asyncio.current_task() and not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def start_conversation(self, user_id: str, device_id: str) -> int:
        return await run_blocking(self.store.create_conversation, user_id, device_id)

    async def context_for(self, user_id: str, device_id: str) -> Dict[str, List[Dict[str, object]]]:
        return await run_blocking(self.store.context_for, user_id, device_id)

    async def record_message(self, conversation_id: int, role: str, content: str) -> int:
        message_id = await run_blocking(self.store.append_message, conversation_id, role, content)
        self._schedule_summary(conversation_id, self.idle_seconds)
        return message_id

    async def summarize_now(self, conversation_id: int) -> bool:
        owner = await run_blocking(self.store.conversation_owner, conversation_id)
        if owner is None:
            return False
        user_id = owner['user_id']
        lock = self._user_summary_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            revision = getattr(self, 'retry_revision', None)
            key = (user_id, await run_blocking(revision, user_id) if revision else None)
            if self.clock() < self._retry_after.get(key, 0):
                return False
            try:
                result = await self._summarize_conversation(conversation_id)
                if result:
                    self._retry_after.pop(key, None)
                    self._retry_attempts.pop(key, None)
                elif await run_blocking(self.store.pending_messages, conversation_id, 1):
                    self._defer_summary(key, 300)
                return result
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # 同一账号的所有待总结会话共用退避，避免每 15 秒乘以会话数打上游。
                status = getattr(exc, 'status_code', None)
                base = 3600 if status in (401, 403) else 120 if status == 429 else 60
                self._defer_summary(key, base)
                if self.on_error is not None:
                    self.on_error(exc)
                return False

    def _defer_summary(self, key, base):
        attempts = min(8, self._retry_attempts.get(key, 0) + 1)
        self._retry_attempts[key] = attempts
        self._retry_after[key] = self.clock() + min(21600, base * 2 ** (attempts - 1))

    async def _summarize_conversation(self, conversation_id: int) -> bool:
        enabled = getattr(self, 'user_enabled', None)
        owner = await run_blocking(self.store.conversation_owner, conversation_id) if enabled else None
        if enabled and (not owner or not await run_blocking(enabled, owner['user_id'])):
            return False
        if conversation_id in self._summarizing:
            return False
        self._summarizing.add(conversation_id)
        self._active_summaries[conversation_id] = asyncio.current_task()
        summarized = False
        try:
            while True:
                # 从最早的未总结消息开始，只有实际交给模型的批次才会标记完成。
                pending = await run_blocking(self.store.pending_messages, conversation_id, 40)
                if not pending:
                    return summarized
                through_message_id = int(pending[-1]["id"])
                result = await self.summarize(pending)
                if enabled and not await run_blocking(enabled, owner['user_id']):
                    return summarized
                if not isinstance(result, SummaryResult):
                    raise TypeError("总结回调必须返回 SummaryResult")
                if not result.summary.strip() and not result.memories and not result.open_topics:
                    return summarized
                await run_blocking(
                    self.store.save_summary, conversation_id, through_message_id,
                    result.summary, result.memories, result.open_topics,
                )
                summarized = True
        finally:
            self._summarizing.discard(conversation_id)
            self._active_summaries.pop(conversation_id, None)

    async def cancel_user_tasks(self, user_id: str):
        tasks = set()
        for conversation_id in set(self._summary_tasks) | set(self._active_summaries):
            owner = await run_blocking(self.store.conversation_owner,conversation_id)
            if owner and owner['user_id'] == user_id:
                for mapping in (self._summary_tasks,self._active_summaries):
                    task = mapping.pop(conversation_id,None)
                    if task is not None:
                        tasks.add(task)
        tasks.discard(asyncio.current_task())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)

    async def clear_owner(self, user_id: str, device_id: str):
        """调用方先结束设备会话，取消总结后删除；旧回调不能恢复已删除的会话。"""
        conversation_ids = await run_blocking(self.store.owner_conversation_ids, user_id, device_id)
        tasks = set()
        for conversation_id in conversation_ids:
            task = self._summary_tasks.pop(conversation_id, None)
            if task is not None:
                tasks.add(task)
            task = self._active_summaries.get(conversation_id)
            if task is not None:
                tasks.add(task)
        tasks.discard(asyncio.current_task())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await run_blocking(self.store.clear_owner, user_id, device_id)

    def _schedule_summary(self, conversation_id: int, delay: float) -> None:
        if self._closed:
            return
        old_task = self._summary_tasks.get(conversation_id)
        if old_task is not None and not old_task.done():
            old_task.cancel()
        task = asyncio.create_task(self._run_summary_after_delay(conversation_id, delay))
        self._summary_tasks[conversation_id] = task

    async def _run_summary_after_delay(self, conversation_id: int, delay: float) -> None:
        current = asyncio.current_task()
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            await self.summarize_now(conversation_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.on_error is not None:
                self.on_error(exc)
        finally:
            if self._summary_tasks.get(conversation_id) is current:
                self._summary_tasks.pop(conversation_id, None)

    async def _maintenance_loop(self) -> None:
        try:
            while not self._closed:
                due = await run_blocking(self.store.due_conversation_ids, self.idle_seconds)
                for conversation_id in due:
                    if conversation_id not in self._summary_tasks or self._summary_tasks[conversation_id].done():
                        self._schedule_summary(conversation_id, 0)
                await asyncio.sleep(self.poll_seconds)
        except asyncio.CancelledError:
            raise
