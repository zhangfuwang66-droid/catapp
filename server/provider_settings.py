"""账号模型凭证：数据库仅存密文，主密钥与数据库分开保存。"""

from __future__ import annotations

import base64
from contextlib import contextmanager
import os
from pathlib import Path
import secrets
import sqlite3
import time
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from database_schema import validate_versions


class MissingProviderKey(ValueError):
    def __init__(self):
        super().__init__("请先在控制台的「模型设置」中保存自己的百炼 API Key")


class VaultUnavailable(ValueError):
    def __init__(self):
        super().__init__("模型凭证暂不可读取，请联系管理员检查独立主密钥")


def validate_api_key(value):
    value = value.strip()
    if not 10 <= len(value) <= 512 or any(c.isspace() or not 33 <= ord(c) <= 126 for c in value):
        raise ValueError("API Key 格式不正确，请粘贴完整 Key，不能包含空格或换行")
    return value


class ProviderSettings:
    def __init__(self, path, *, key_file=None, clock=time.time):
        self.path = Path(path)
        self.key_file = Path(key_file or os.getenv("CYBERMIAO_SECRET_KEY_FILE") or self.path.with_name("provider.key"))
        self.clock = clock
        with self.connect() as db:
            version = validate_versions(db).get("provider", 0)
            if version == 0:
                db.execute("""CREATE TABLE account_provider_keys (
                    user_id TEXT PRIMARY KEY REFERENCES auth_users(id) ON DELETE CASCADE,
                    ciphertext BLOB NOT NULL, masked TEXT NOT NULL, revision TEXT NOT NULL,
                    updated_at REAL NOT NULL)""")
                db.execute("INSERT INTO schema_migrations(component, version) VALUES ('provider', 1)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                yield db
        finally:
            db.close()

    def _master(self, db):
        try:
            if self.key_file.is_symlink():
                raise VaultUnavailable()
            if not self.key_file.exists():
                if db.execute("SELECT 1 FROM account_provider_keys LIMIT 1").fetchone():
                    raise VaultUnavailable()
                self.key_file.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                try:
                    fd = os.open(self.key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    pass
                else:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(base64.urlsafe_b64encode(secrets.token_bytes(32)))
                        stream.flush()
                        os.fsync(stream.fileno())
            raw = base64.b64decode(self.key_file.read_bytes().strip(), altchars=b"-_", validate=True)
            if len(raw) != 32:
                raise VaultUnavailable()
            return AESGCM(raw)
        except (OSError, ValueError):
            raise VaultUnavailable() from None

    def status(self, user_id):
        with self.connect() as db:
            row = db.execute("SELECT masked, revision, updated_at FROM account_provider_keys WHERE user_id=?", (user_id,)).fetchone()
        return {"configured": row is not None, **(dict(row) if row else {"masked": "", "revision": None, "updated_at": None})}

    def get(self, user_id):
        with self.connect() as db:
            row = db.execute("SELECT ciphertext FROM account_provider_keys WHERE user_id=?", (user_id,)).fetchone()
            if row is None:
                raise MissingProviderKey()
            try:
                data = bytes(row[0])
                return self._master(db).decrypt(data[:12], data[12:], user_id.encode()).decode("utf-8")
            except Exception:
                raise VaultUnavailable() from None

    def save(self, user_id, api_key):
        api_key = validate_api_key(api_key)
        with self.connect() as db:
            nonce = secrets.token_bytes(12)
            data = nonce + self._master(db).encrypt(nonce, api_key.encode(), user_id.encode())
            db.execute("""INSERT INTO account_provider_keys VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET ciphertext=excluded.ciphertext, masked=excluded.masked,
                revision=excluded.revision, updated_at=excluded.updated_at""",
                (user_id, data, "••••••••" + api_key[-4:], uuid.uuid4().hex, self.clock()))
        return self.status(user_id)

    def delete(self, user_id):
        with self.connect() as db:
            db.execute("DELETE FROM account_provider_keys WHERE user_id=?", (user_id,))
        return self.status(user_id)


class ModelCredentials:
    def __init__(self, store, *, account_mode=True, local_key=None):
        self.store = store
        self.account_mode = account_mode
        self.local_key = local_key or (lambda: os.getenv("DASHSCOPE_API_KEY", ""))

    def key(self, user_id):
        # 账号模式绝不回落到平台 Key，包括后台任务。
        if self.account_mode:
            return self.store.get(user_id)
        key = self.local_key()
        if not key:
            raise MissingProviderKey()
        return key

    def available(self, user_id):
        try:
            return bool(self.key(user_id))
        except (MissingProviderKey, VaultUnavailable):
            return False
