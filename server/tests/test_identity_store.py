"""账号哈希、可撤销会话、持久限流和事务迁移验证。"""

from contextlib import closing, redirect_stderr, redirect_stdout
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import identity_store
from identity_store import IdentityStore, RateLimited, csrf_for_session, token_digest
from memory_store import MemoryStore
from tools.identity_admin import main as admin_main
from tools.memory_database import backup_database


class IdentityStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="identity-tests-")
        self.path = Path(self.temp.name) / "memory.sqlite3"
        self.now = 1800000000.0
        self.store = IdentityStore(self.path, clock=lambda: self.now)

    def tearDown(self):
        self.temp.cleanup()

    def user(self, name="alice"):
        return self.store.create_user(name, "sample-password-for-tests")

    def test_password_is_argon2id_and_session_tokens_are_not_stored(self):
        user = self.user()
        session = self.store.login("ALICE", "sample-password-for-tests", client_key="127.0.0.1")
        self.assertEqual(self.store.session_user(session.token), user)
        with closing(sqlite3.connect(self.path)) as connection:
            password_hash = connection.execute("SELECT password_hash FROM auth_users").fetchone()[0]
            stored = connection.execute("SELECT token_hash FROM auth_sessions").fetchone()[0]
        self.assertTrue(password_hash.startswith("$argon2id$"))
        self.assertEqual(stored, token_digest(session.token))
        self.assertNotIn(session.token, repr(session))
        self.assertNotEqual(csrf_for_session(session.token), stored)
        self.assertNotIn(b"sample-password-for-tests", self.path.read_bytes())
        self.assertNotIn(session.token.encode(), self.path.read_bytes())

    def test_wrong_password_logout_expiry_and_password_reset(self):
        self.user()
        self.assertIsNone(self.store.login("alice", "wrong-password", client_key="local"))
        self.assertIsNone(self.store.login("missing", "wrong-password", client_key="local"))
        first = self.store.login("alice", "sample-password-for-tests", client_key="local", ttl=60)
        self.store.revoke_session(first.token)
        self.assertIsNone(self.store.session_user(first.token))
        second = self.store.login("alice", "sample-password-for-tests", client_key="local", ttl=60)
        self.now += 61
        self.assertIsNone(self.store.session_user(second.token))
        third = self.store.login("alice", "sample-password-for-tests", client_key="local")
        self.store.change_password("alice", "a-new-password-for-tests")
        self.assertIsNone(self.store.session_user(third.token))
        self.assertIsNone(self.store.login("alice", "sample-password-for-tests", client_key="local"))
        self.assertIsNotNone(self.store.login("alice", "a-new-password-for-tests", client_key="local"))

    def test_username_normalization_unique_and_password_limits(self):
        self.user(" ALICE ")
        with self.assertRaises(ValueError):
            self.user("alice")
        for name, password in (("../", "long-password"), ("ab", "long-password"), ("bob", "123"), ("bob", " " * 10)):
            with self.assertRaises(ValueError):
                self.store.create_user(name, password)

    def test_rate_limit_survives_reopening_and_expires(self):
        self.store.consume_rate_limit("test", limit=1, window=60)
        reopened = IdentityStore(self.path, clock=lambda: self.now)
        with self.assertRaises(RateLimited) as error:
            reopened.consume_rate_limit("test", limit=1, window=60)
        self.assertGreater(error.exception.retry_after, 0)
        self.now += 61
        reopened.consume_rate_limit("test", limit=1, window=60)

    def test_identity_migration_preserves_memory_and_backup_contains_accounts(self):
        memory = MemoryStore(self.path)
        conversation = memory.create_conversation("existing-owner", "existing-cat")
        memory.append_message(conversation, "user", "已有的模拟记忆")
        IdentityStore(self.path)
        user = self.user()
        self.assertEqual(memory.pending_messages(conversation)[0]["content"], "已有的模拟记忆")
        backup = Path(self.temp.name) / "backup.sqlite3"
        backup_database(self.path, backup)
        self.assertEqual(IdentityStore(backup).list_users()[0]["id"], user.id)

    def test_future_schema_is_rejected_and_failed_migration_rolls_back(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("UPDATE schema_migrations SET version=999 WHERE component='identity'")
            connection.commit()
        with self.assertRaises(ValueError):
            IdentityStore(self.path)
        fresh = Path(self.temp.name) / "failed.sqlite3"
        with patch.object(identity_store, "MIGRATIONS", (("CREATE TABLE temporary_table(id INTEGER)", "INVALID SQL"),)):
            with self.assertRaises(sqlite3.Error):
                IdentityStore(fresh)
        with closing(sqlite3.connect(fresh)) as connection:
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='temporary_table'").fetchone())
        self.assertEqual(IdentityStore(fresh).list_users(), [])

    def test_admin_password_prompt_never_prints_password(self):
        output = io.StringIO()
        with patch("getpass.getpass", return_value="private-test-password"), redirect_stdout(output):
            result = admin_main(["--database", str(self.path), "user-add", "--username", "local-owner"])
        self.assertEqual(result, 0)
        self.assertNotIn("private-test-password", output.getvalue())
        self.assertIn("local-owner", output.getvalue())
        missing = Path(self.temp.name) / "missing.sqlite3"
        with redirect_stderr(io.StringIO()):
            self.assertEqual(admin_main(["--database", str(missing), "users"]), 1)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
