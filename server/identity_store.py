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
from device_identity import validate_device_id
from database_schema import validate_versions
from device_sharing import DeviceSharing


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
    (
        """CREATE TABLE devices (
            id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            owner_user_id TEXT REFERENCES auth_users(id),
            created_at REAL NOT NULL,
            bound_at REAL,
            revoked_at REAL
        )""",
        "CREATE INDEX idx_devices_owner ON devices(owner_user_id)",
        """CREATE TABLE device_credentials (
            token_hash TEXT PRIMARY KEY,
            device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
            created_at REAL NOT NULL,
            revoked_at REAL
        )""",
        "CREATE INDEX idx_device_credentials_device ON device_credentials(device_id)",
        """CREATE TABLE device_pairings (
            code_hash TEXT PRIMARY KEY,
            device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            claimed_by TEXT REFERENCES auth_users(id),
            claimed_at REAL,
            invalidated_at REAL
        )""",
        "CREATE INDEX idx_device_pairings_device ON device_pairings(device_id)",
    ),
    (
        "ALTER TABLE devices ADD COLUMN access_epoch INTEGER NOT NULL DEFAULT 0",
        """CREATE TABLE device_memberships (
            id TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES devices(id),
            user_id TEXT NOT NULL REFERENCES auth_users(id), kind TEXT NOT NULL CHECK(kind IN ('owner','guest')),
            started_at REAL NOT NULL, ended_at REAL)""",
        "CREATE INDEX idx_device_memberships_owner ON device_memberships(user_id,device_id)",
        """INSERT INTO device_memberships SELECT lower(hex(randomblob(16))),id,owner_user_id,'owner',
            COALESCE(bound_at,created_at),NULL FROM devices WHERE owner_user_id IS NOT NULL""",
        """CREATE TABLE device_loans (
            id TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES devices(id),
            owner_user_id TEXT NOT NULL REFERENCES auth_users(id), guest_user_id TEXT REFERENCES auth_users(id),
            invitation_hash TEXT NOT NULL UNIQUE, created_at REAL NOT NULL, claim_until REAL NOT NULL,
            expires_at REAL NOT NULL, ended_at REAL)""",
        "CREATE INDEX idx_device_loans_active ON device_loans(device_id,ended_at,expires_at)",
        """CREATE TABLE device_preferences (
            user_id TEXT NOT NULL REFERENCES auth_users(id), device_id TEXT NOT NULL REFERENCES devices(id),
            voice_paused INTEGER NOT NULL DEFAULT 0 CHECK(voice_paused IN (0,1)), PRIMARY KEY(user_id,device_id))""",
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


@dataclass(frozen=True)
class DeviceCredential:
    device_id: str
    token: str = field(repr=False)


@dataclass(frozen=True)
class PairingGrant:
    device_id: str
    code: str = field(repr=False)
    expires_at: float


class RateLimited(ValueError):
    def __init__(self, retry_after: int):
        super().__init__("操作过于频繁，请稍后再试")
        self.retry_after = max(1, retry_after)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def csrf_for_session(token: str) -> str:
    return token_digest("cybermiao-csrf:" + token)


class IdentityStore(DeviceSharing):
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
            version = validate_versions(connection).get("identity", 0)
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
            current = connection.execute("SELECT username, disabled, password_hash FROM auth_users WHERE id=?", (row["id"],)).fetchone()
            # 密码校验期间可能由管理员改名；旧账号名不能在改名后补发有效会话。
            if current is None or current["disabled"] or current["username"] != row["username"] or current["password_hash"] != password_hash:
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

    def user_enabled(self, user_id: str) -> bool:
        with self._transaction() as connection:
            return bool(connection.execute("SELECT 1 FROM auth_users WHERE id=? AND disabled=0", (user_id,)).fetchone())

    def register_device(self, device_id: str, display_name: str) -> DeviceCredential:
        validate_device_id(device_id)
        display_name = display_name.strip()
        if not 1 <= len(display_name) <= 80 or any(ord(c) < 32 for c in display_name):
            raise ValueError("设备名称需为 1～80 个可显示字符")
        grant = DeviceCredential(device_id, secrets.token_urlsafe(32))
        now = self.clock()
        with self._transaction(write=True) as connection:
            try:
                connection.execute("INSERT INTO devices(id, display_name, created_at) VALUES (?, ?, ?)",
                                   (device_id, display_name, now))
            except sqlite3.IntegrityError:
                raise ValueError("设备已登记，请核对编号或使用凭证轮换") from None
            connection.execute("INSERT INTO device_credentials(token_hash, device_id, created_at) VALUES (?, ?, ?)",
                               (token_digest(grant.token), device_id, now))
        return grant

    def rotate_device_credential(self, device_id: str) -> DeviceCredential:
        validate_device_id(device_id)
        grant = DeviceCredential(device_id, secrets.token_urlsafe(32))
        now = self.clock()
        with self._transaction(write=True) as connection:
            if connection.execute("SELECT id FROM devices WHERE id=?", (device_id,)).fetchone() is None:
                raise ValueError("设备未登记")
            connection.execute("UPDATE device_credentials SET revoked_at=? WHERE device_id=? AND revoked_at IS NULL", (now, device_id))
            connection.execute("UPDATE device_pairings SET invalidated_at=? WHERE device_id=? AND claimed_at IS NULL", (now, device_id))
            connection.execute("INSERT INTO device_credentials(token_hash, device_id, created_at) VALUES (?, ?, ?)",
                               (token_digest(grant.token), device_id, now))
            connection.execute("UPDATE devices SET revoked_at=NULL WHERE id=?", (device_id,))
        return grant

    def credential_device(self, device_id: str, token: str) -> Optional[dict]:
        try:
            validate_device_id(device_id)
        except ValueError:
            return None
        if not isinstance(token, str) or not 32 <= len(token) <= 128:
            return None
        with self._transaction() as connection:
            row = connection.execute("""SELECT d.* FROM devices d
                JOIN device_credentials c ON c.device_id=d.id
                WHERE d.id=? AND c.token_hash=? AND c.revoked_at IS NULL AND d.revoked_at IS NULL""",
                (device_id, token_digest(token)),
            ).fetchone()
            if row is None:
                return None
            device = self.decorate_device(connection, row)
            if device['active_user_id'] and not connection.execute(
                    "SELECT 1 FROM auth_users WHERE id=? AND disabled=0", (device['active_user_id'],)).fetchone():
                return None
            return device

    def list_devices(self, user_id: Optional[str] = None) -> list[dict]:
        query = "SELECT id, display_name, owner_user_id, created_at, bound_at, revoked_at FROM devices"
        arguments = ()
        if user_id is not None:
            query += " WHERE owner_user_id=?"
            arguments = (user_id,)
        query += " ORDER BY created_at, id"
        with self._transaction() as connection:
            return [dict(row) for row in connection.execute(query, arguments)]

    def owned_device(self, device_id: str, user_id: str) -> Optional[dict]:
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM devices WHERE id=? AND owner_user_id=? AND revoked_at IS NULL",
                                     (device_id, user_id)).fetchone()
        return dict(row) if row else None

    def revoke_device(self, device_id: str, *, owner_user_id: Optional[str] = None):
        now = self.clock()
        with self._transaction(write=True) as connection:
            row = connection.execute("SELECT owner_user_id FROM devices WHERE id=?", (device_id,)).fetchone()
            if row is None or (owner_user_id is not None and row["owner_user_id"] != owner_user_id):
                raise ValueError("设备不存在或无权操作")
            connection.execute("UPDATE devices SET revoked_at=? WHERE id=?", (now, device_id))
            self._end_loans(connection, device_id)
            connection.execute("UPDATE devices SET access_epoch=access_epoch+1 WHERE id=?", (device_id,))
            connection.execute("UPDATE device_credentials SET revoked_at=? WHERE device_id=? AND revoked_at IS NULL", (now, device_id))
            connection.execute("UPDATE device_pairings SET invalidated_at=? WHERE device_id=? AND claimed_at IS NULL", (now, device_id))

    def request_pairing(self, device_id: str, token: str) -> PairingGrant:
        if self.credential_device(device_id, token) is None:
            raise ValueError("设备凭证无效")
        self.consume_rate_limit("pair-request:" + device_id, limit=1, window=30)
        now = self.clock()
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        raw_code = "".join(secrets.choice(alphabet) for _ in range(8))
        with self._transaction(write=True) as connection:
            row = connection.execute("""SELECT d.owner_user_id FROM devices d
                JOIN device_credentials c ON c.device_id=d.id
                WHERE d.id=? AND c.token_hash=? AND c.revoked_at IS NULL AND d.revoked_at IS NULL""",
                (device_id, token_digest(token)),
            ).fetchone()
            if row is None:
                raise ValueError("设备凭证无效")
            if row["owner_user_id"] is not None:
                raise ValueError("设备已绑定账号")
            connection.execute("UPDATE device_pairings SET invalidated_at=? WHERE device_id=? AND claimed_at IS NULL AND invalidated_at IS NULL",
                               (now, device_id))
            connection.execute("DELETE FROM device_pairings WHERE expires_at<? AND claimed_at IS NULL", (now - 86400,))
            # 极低概率的随机码碰撞仍由唯一约束检测，重新生成最多五次。
            for attempt in range(5):
                try:
                    connection.execute("INSERT INTO device_pairings(code_hash, device_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                                       (token_digest(raw_code), device_id, now, now + 300))
                    break
                except sqlite3.IntegrityError:
                    if attempt == 4:
                        raise RuntimeError("配对码生成失败，请稍后重试") from None
                    raw_code = "".join(secrets.choice(alphabet) for _ in range(8))
        return PairingGrant(device_id, raw_code[:4] + "-" + raw_code[4:], now + 300)

    def claim_pairing(self, code: str, user_id: str, *, client_key: str) -> dict:
        self.consume_rate_limit("pair-claim-user:" + user_id, limit=10, window=300)
        self.consume_rate_limit("pair-claim-ip:" + token_digest(client_key), limit=30, window=300)
        normalized = re.sub(r"[\s-]", "", code.upper())
        if not re.fullmatch(r"[A-HJ-NP-Z2-9]{8}", normalized):
            raise ValueError("配对码无效或已过期")
        now = self.clock()
        with self._transaction(write=True) as connection:
            user = connection.execute("SELECT id FROM auth_users WHERE id=? AND disabled=0", (user_id,)).fetchone()
            if user is None:
                raise ValueError("账号不可用，请重新登录")
            pairing = connection.execute("""SELECT p.code_hash, p.device_id FROM device_pairings p
                JOIN devices d ON d.id=p.device_id
                WHERE p.code_hash=? AND p.expires_at>? AND p.claimed_at IS NULL AND p.invalidated_at IS NULL
                    AND d.owner_user_id IS NULL AND d.revoked_at IS NULL""", (token_digest(normalized), now)).fetchone()
            if pairing is None:
                raise ValueError("配对码无效或已过期")
            connection.execute("UPDATE devices SET owner_user_id=?, bound_at=?,access_epoch=access_epoch+1 WHERE id=? AND owner_user_id IS NULL",
                               (user_id, now, pairing["device_id"]))
            connection.execute("INSERT INTO device_memberships VALUES(?,?,?,?,?,NULL)",
                               (uuid.uuid4().hex,pairing["device_id"],user_id,'owner',now))
            connection.execute("UPDATE device_pairings SET claimed_by=?, claimed_at=? WHERE code_hash=?",
                               (user_id, now, pairing["code_hash"]))
            connection.execute("UPDATE device_pairings SET invalidated_at=? WHERE device_id=? AND claimed_at IS NULL AND invalidated_at IS NULL",
                               (now, pairing["device_id"]))
            return dict(connection.execute("SELECT * FROM devices WHERE id=?", (pairing["device_id"],)).fetchone())
