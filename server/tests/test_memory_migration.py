"""历史身份迁移的预览、备份、归属和事务保护。"""

from contextlib import closing, redirect_stdout
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest

from identity_store import IdentityStore
from memory_store import MemoryStore
from tools.migrate_memory import main, migrate_memory


class MemoryMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="memory-migration-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "memory.sqlite3"
        self.backup = Path(self.temp.name) / "before.sqlite3"
        self.store = MemoryStore(self.path)
        self.identities = IdentityStore(self.path)
        self.user = self.identities.create_user("alice", "alice-test-password")
        credential = self.identities.register_device("cat-alice", "小喵测试设备")
        pairing = self.identities.request_pairing(credential.device_id, credential.token)
        self.identities.claim_pairing(pairing.code, self.user.id, client_key="local")
        self.source = ("test-user-001", "test-cat-001")
        self.target = (self.user.id, "cat-alice")
        self.conversation = self.store.create_conversation(*self.source)
        self.message = self.store.append_message(self.conversation, "user", "不应输出的历史聊天正文")
        self.store.save_summary(self.conversation, self.message, "不应输出的摘要", ["不应输出的事实"], ["不应输出的话题"])

    def migrate(self, **kwargs):
        return migrate_memory(self.path, *self.source, *self.target, **kwargs)

    def test_default_preview_is_readonly_and_outputs_only_counts(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["--database", str(self.path), "--source-user", self.source[0],
                                   "--source-device", self.source[1], "--target-user", self.target[0],
                                   "--target-device", self.target[1]]), 0)
        self.assertIn('"applied": false', output.getvalue())
        self.assertIn('"messages": 1', output.getvalue())
        self.assertNotIn("不应输出", output.getvalue())
        self.assertEqual(self.store.conversation_owner(self.conversation)["user_id"], self.source[0])
        self.assertFalse(self.backup.exists())

    def test_apply_preserves_ids_messages_summary_progress_and_backup(self):
        report = self.migrate(apply=True, backup=self.backup, offline=True)
        self.assertTrue(report["applied"])
        self.assertEqual(report["row_counts"], {"conversations": 1, "messages": 1, "conversation_summaries": 1, "memories": 1})
        self.assertEqual(self.store.conversation_owner(self.conversation)["user_id"], self.user.id)
        self.assertEqual(self.store.pending_messages(self.conversation), [])
        context = self.store.context_for(*self.target)
        self.assertEqual(context["summaries"][0]["open_topics"], ["不应输出的话题"])
        self.assertEqual(context["memories"][0]["content"], "不应输出的事实")
        original = MemoryStore(self.backup)
        self.assertEqual(original.conversation_owner(self.conversation)["user_id"], self.source[0])
        self.assertEqual(len(report["backup"]["sha256"]), 64)
        with self.assertRaises(ValueError):
            self.migrate(apply=True, backup=Path(self.temp.name) / "second.sqlite3", offline=True)

    def test_requires_offline_backup_and_refuses_existing_output(self):
        for options in ({"apply": True}, {"apply": True, "backup": self.backup}, {"apply": True, "offline": True}):
            with self.assertRaises(ValueError):
                self.migrate(**options)
        self.backup.write_bytes(b"keep original")
        with self.assertRaises(FileExistsError):
            self.migrate(apply=True, backup=self.backup, offline=True)
        self.assertEqual(self.backup.read_bytes(), b"keep original")
        self.assertEqual(self.store.conversation_owner(self.conversation)["user_id"], self.source[0])

    def test_wrong_owner_nonempty_target_and_real_source_are_rejected(self):
        bob = self.identities.create_user("bob", "bob-test-password")
        with self.assertRaises(ValueError):
            migrate_memory(self.path, *self.source, bob.id, "cat-alice")
        with self.assertRaises(ValueError):
            migrate_memory(self.path, bob.id, self.source[1], *self.target)
        self.store.create_conversation(*self.target)
        with self.assertRaises(ValueError):
            self.migrate()

    def test_failure_during_second_update_rolls_back_and_keeps_backup(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("CREATE TRIGGER reject_move BEFORE UPDATE OF user_id ON memories BEGIN SELECT RAISE(ABORT, 'test rollback'); END")
            connection.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.migrate(apply=True, backup=self.backup, offline=True)
        self.assertEqual(self.store.conversation_owner(self.conversation)["user_id"], self.source[0])
        self.assertTrue(self.backup.is_file())
        self.assertEqual(self.migrate()["row_counts"]["messages"], 1)

    def test_missing_database_does_not_create_file(self):
        missing = Path(self.temp.name) / "missing.sqlite3"
        with self.assertRaises(FileNotFoundError):
            migrate_memory(missing, *self.source, *self.target)
        self.assertFalse(missing.exists())

    def test_newer_schema_and_cross_owner_references_are_rejected(self):
        other = self.store.create_conversation("other-test-user", "other-test-cat")
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("UPDATE memories SET source_conversation_id=?", (other,))
            connection.commit()
            with self.assertRaises(ValueError):
                self.migrate()
            connection.execute("UPDATE memories SET source_conversation_id=?", (self.conversation,))
            connection.execute("UPDATE schema_migrations SET version=999 WHERE component='identity'")
            connection.commit()
            with self.assertRaises(ValueError):
                self.migrate()


if __name__ == "__main__":
    unittest.main()
