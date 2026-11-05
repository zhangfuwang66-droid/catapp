"""账号、可撤销会话与设备身份存储；令牌只保存摘要，迁移以事务执行。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from typing import Callable, Optional
import unicodedata
import uuid

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type


MIGRATIONS = (
    (
        """CREATE TABLE auth_users (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at REAL NOT NULL,
            disabled INTEGER NOT NULL DEFAULT 0 CHECK (disabled IN (0, 1))
        )""",
        """CREATE TABLE auth_sessions (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            revoked_at REAL
        )""",
        "CREATE INDEX idx_auth_sessions_user ON auth_sessions(user_id)",
        """CREATE TABLE auth_rate_limits (
            bucket TEXT PRIMARY KEY,
            window_start REAL NOT NULL,
            hits INTEGER NOT NULL
        )""",
    ),
)


@dataclass(frozen=True)
class User:
    id: str
    username: str


@dataclass(frozen=True)
class SessionGrant:
    user: User
    token: str = field(repr=False)
    expires_at: float


class RateLimited(ValueError):
    def __init__(self, retry_after: int):
        super().__init__("操作过于频繁，请稍后再试")
        self.retry_after = max(1, retry_after)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def csrf_for_session(token: str) -> str:
    return token_digest("cybermiao-csrf:" + token)


class IdentityStore:
    def __init__(self, path: str | Path, *, clock: Callable[[], float] = time.time):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        self.clock = clock
        self._lock = threading.RLock()
        self.hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2, type=Type.ID)
        self._dummy_hash: Optional[str] = None
        self._migrate()

    @contextmanager
    def _transaction(self, *, write: bool = False):
        with self._lock:
            connection = sqlite3.connect(str(self.path), timeout=30)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA busy_timeout = 30000")
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    def _migrate(self):
        with self._transaction(write=True) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                component TEXT PRIMARY KEY, version INTEGER NOT NULL
            )""")
            row = connection.execute("SELECT version FROM schema_migrations WHERE component='identity'").fetchone()
            version = row["version"] if row else 0
            if version > len(MIGRATIONS):
                raise ValueError("身份数据库版本高于当前程序，请使用匹配版本，不能降级写入")
            for index in range(version, len(MIGRATIONS)):
                for statement in MIGRATIONS[index]:
                    connection.execute(statement)
                connection.execute(
                    "INSERT OR REPLACE INTO schema_migrations(component, version) VALUES ('identity', ?)",
                    (index + 1,),
                )

    @staticmethod
    def normalize_username(username: str) -> str:
        if not isinstance(username, str):
            raise ValueError("账号格式不正确")
        normalized = unicodedata.normalize("NFKC", username).strip().casefold()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,31}", normalized):
            raise ValueError("账号需为 3～32 位英文字母、数字、下划线、点或连字符")
        return normalized

    @staticmethod
    def validate_password(password: str):
        if not isinstance(password, str) or not 10 <= len(password) <= 128 or not password.strip():
            raise ValueError("密码需为 10～128 个字符")

    def create_user(self, username: str, password: str) -> User:
        username = self.normalize_username(username)
        self.validate_password(password)
        password_hash = self.hasher.hash(password)
        user = User("usr_" + uuid.uuid4().hex, username)
        with self._transaction(write=True) as connection:
            try:
                connection.execute(
                    "INSERT INTO auth_users(id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
                    (user.id, username, password_hash, self.clock()),
                )
            except sqlite3.IntegrityError:
                raise ValueError("账号已存在") from None
        return user

    def list_users(self) -> list[dict]:
        with self._transaction() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT id, username, created_at, disabled FROM auth_users ORDER BY created_at, id"
            )]

    def change_password(self, username: str, password: str):
        username = self.normalize_username(username)
        self.validate_password(password)
        password_hash = self.hasher.hash(password)
        with self._transaction(write=True) as connection:
            row = connection.execute("SELECT id FROM auth_users WHERE username=?", (username,)).fetchone()
            if row is None:
                raise ValueError("账号不存在")
            connection.execute("UPDATE auth_users SET password_hash=? WHERE id=?", (password_hash, row["id"]))
            connection.execute("UPDATE auth_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                               (self.clock(), row["id"]))

    def consume_rate_limit(self, bucket: str, *, limit: int, window: float):
        now = self.clock()
        with self._transaction(write=True) as connection:
            row = connection.execute("SELECT window_start, hits FROM auth_rate_limits WHERE bucket=?", (bucket,)).fetchone()
            if row and row["window_start"] + window > now:
                if row["hits"] >= limit:
                    raise RateLimited(int(row["window_start"] + window - now) + 1)
                connection.execute("UPDATE auth_rate_limits SET hits=hits+1 WHERE bucket=?", (bucket,))
            else:
                connection.execute("INSERT OR REPLACE INTO auth_rate_limits VALUES (?, ?, 1)", (bucket, now))
            # 仅保存短期计数，不让未知账号或客户端不断累积限流记录。
            connection.execute("DELETE FROM auth_rate_limits WHERE window_start < ?", (now - 86400,))

    def login(self, username: str, password: str, *, client_key: str, ttl: float = 7 * 86400) -> Optional[SessionGrant]:
        try:
            normalized = self.normalize_username(username)
        except ValueError:
            normalized = ""
        self.consume_rate_limit("login-ip:" + token_digest(client_key), limit=30, window=300)
        self.consume_rate_limit("login-user:" + token_digest(normalized), limit=10, window=300)
        if not isinstance(password, str) or len(password) > 128:
            return None
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM auth_users WHERE username=?", (normalized,)).fetchone()
        # 不存在的账号也做相同的密码校验，接口统一返回账号或密码错误。
        if row is None:
            with self._lock:
                if self._dummy_hash is None:
                    self._dummy_hash = self.hasher.hash(secrets.token_urlsafe(32))
            password_hash = self._dummy_hash
        else:
            password_hash = row["password_hash"]
        try:
            self.hasher.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return None
        if row is None or row["disabled"]:
            return None
        if not 60 <= ttl <= 30 * 86400:
            raise ValueError("会话有效期需在 1 分钟至 30 天之间")
        replacement = self.hasher.hash(password) if self.hasher.check_needs_rehash(password_hash) else password_hash
        token = secrets.token_urlsafe(32)
        now = self.clock()
        with self._transaction(write=True) as connection:
            current = connection.execute("SELECT disabled, password_hash FROM auth_users WHERE id=?", (row["id"],)).fetchone()
            if current is None or current["disabled"] or current["password_hash"] != password_hash:
                return None
            if replacement != password_hash:
                connection.execute("UPDATE auth_users SET password_hash=? WHERE id=?", (replacement, row["id"]))
            connection.execute("DELETE FROM auth_sessions WHERE expires_at<=? OR revoked_at IS NOT NULL", (now,))
            connection.execute("INSERT INTO auth_sessions(token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                               (token_digest(token), row["id"], now, now + ttl))
        return SessionGrant(User(row["id"], row["username"]), token, now + ttl)

    def session_user(self, token: str) -> Optional[User]:
        if not isinstance(token, str) or not 32 <= len(token) <= 128:
            return None
        with self._transaction() as connection:
            row = connection.execute("""SELECT u.id, u.username FROM auth_users u
                JOIN auth_sessions s ON s.user_id=u.id
                WHERE s.token_hash=? AND s.expires_at>? AND s.revoked_at IS NULL AND u.disabled=0""",
                (token_digest(token), self.clock()),
            ).fetchone()
        return User(row["id"], row["username"]) if row else None

    def revoke_session(self, token: str):
        with self._transaction(write=True) as connection:
            connection.execute("UPDATE auth_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                               (self.clock(), token_digest(token)))
