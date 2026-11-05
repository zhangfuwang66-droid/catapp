"""备份/恢复演练：校验、预览、不覆盖和旧会话失效；不使用真实数据。"""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from identity_store import IdentityStore
from memory_store import MemoryStore
from tools.memory_database import backup_database, file_sha256, inspect_database, restore_database


class DatabaseRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "source.sqlite3"
        self.backup = Path(self.temp.name) / "snapshot.sqlite3"
        self.output = Path(self.temp.name) / "new" / "restored.sqlite3"
        self.store = MemoryStore(self.path)
        self.conversation = self.store.create_conversation("owner", "cat")
        self.store.append_message(self.conversation, "user", "私密模拟聊天")

    def snapshot(self):
        return backup_database(self.path, self.backup)["sha256"]

    def test_restore_default_is_readonly_preview(self):
        digest = self.snapshot()
        before = self.backup.read_bytes()
        report = restore_database(self.backup, self.output, digest)
        self.assertFalse(report["applied"])
        self.assertFalse(self.output.parent.exists())
        self.assertEqual(self.backup.read_bytes(), before)
        self.assertNotIn("私密模拟聊天", json.dumps(report))

    def test_checksum_offline_confirmation_and_existing_targets_are_required(self):
        digest = self.snapshot()
        for options in ({"expected_sha256": "0" * 64}, {"expected_sha256": "short"},
                        {"expected_sha256": digest, "apply": True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                restore_database(self.backup, self.output, **options)
        self.assertFalse(self.output.exists())
        self.output.parent.mkdir()
        self.output.write_bytes(b"keep database")
        with self.assertRaises(FileExistsError):
            restore_database(self.backup, self.output, digest, apply=True, offline=True)
        self.assertEqual(self.output.read_bytes(), b"keep database")

    def test_restored_memory_keeps_binding_but_revokes_sessions_and_pair_codes(self):
        identities = IdentityStore(self.path)
        user = identities.create_user("alice", "test-restore-password")
        session = identities.login("alice", "test-restore-password", client_key="test")
        device = identities.register_device("cat-real", "模拟猫")
        pair = identities.request_pairing(device.device_id, device.token)
        identities.claim_pairing(pair.code, user.id, client_key="test")
        other = identities.register_device("cat-unbound", "待绑定猫")
        unclaimed = identities.request_pairing(other.device_id, other.token)
        conversation = self.store.create_conversation(user.id, device.device_id)
        message = self.store.append_message(conversation, "user", "真实身份的模拟聊天")
        self.store.save_summary(conversation, message, "模拟摘要", ["模拟事实"], ["待续话题"])
        digest = self.snapshot()
        report = restore_database(self.backup, self.output, digest.upper(), apply=True, offline=True)
        self.assertTrue(report["applied"])
        self.assertEqual(report["revoked_sessions"], 1)
        self.assertEqual(report["invalidated_pairings"], 1)
        self.assertEqual(report["sha256"], file_sha256(self.output))
        restored_identity = IdentityStore(self.output)
        self.assertIsNone(restored_identity.session_user(session.token))
        self.assertIsNotNone(restored_identity.owned_device(device.device_id, user.id))
        self.assertIsNotNone(restored_identity.credential_device(device.device_id, device.token))
        with self.assertRaises(ValueError):
            restored_identity.claim_pairing(unclaimed.code, user.id, client_key="test")
        context = MemoryStore(self.output).context_for(user.id, device.device_id)
        self.assertEqual(context["summaries"][0]["open_topics"], ["待续话题"])
        self.assertEqual(context["memories"][0]["content"], "模拟事实")
        self.assertIsNotNone(identities.session_user(session.token))
        self.assertEqual(file_sha256(self.backup), digest)
        for secret in (session.token, device.token, unclaimed.code, "真实身份的模拟聊天"):
            self.assertNotIn(secret, json.dumps(report, ensure_ascii=False))

    def test_orphan_sidecars_are_preserved_and_block_backup_or_restore(self):
        digest = self.snapshot()
        target = Path(self.temp.name) / "unsafe.sqlite3"
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = target.with_name(target.name + suffix)
            sidecar.write_bytes(b"keep sidecar")
            with self.assertRaises(FileExistsError):
                backup_database(self.path, target)
            with self.assertRaises(FileExistsError):
                restore_database(self.backup, target, digest, apply=True, offline=True)
            self.assertEqual(sidecar.read_bytes(), b"keep sidecar")
            self.assertFalse(target.exists())
            sidecar.unlink()

    def test_live_wal_database_is_not_accepted_as_standalone_backup(self):
        keeper = sqlite3.connect(self.path)
        try:
            keeper.execute("PRAGMA journal_mode=WAL")
            self.store.append_message(self.conversation, "assistant", "仍在运行")
            with self.assertRaises(ValueError):
                restore_database(self.path, self.output, file_sha256(self.path))
        finally:
            keeper.close()
        self.assertFalse(self.output.exists())

    def test_incompatible_or_damaged_backup_does_not_create_output(self):
        self.snapshot()
        with closing(sqlite3.connect(self.backup)) as connection:
            connection.execute("UPDATE schema_migrations SET version=999 WHERE component='memory'")
            connection.commit()
        with self.assertRaises(ValueError):
            restore_database(self.backup, self.output, file_sha256(self.backup), apply=True, offline=True)
        self.assertFalse(self.output.exists())
        self.backup.write_bytes(b"not a database")
        with self.assertRaises(sqlite3.Error):
            restore_database(self.backup, self.output, file_sha256(self.backup), apply=True, offline=True)

    def test_failed_publication_leaves_no_partial_database(self):
        digest = self.snapshot()
        with patch("tools.memory_database.os.link", side_effect=OSError("模拟文件系统失败")):
            with self.assertRaises(OSError):
                restore_database(self.backup, self.output, digest, apply=True, offline=True)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.output.parent.glob(".cybermiao-restore-*")))
        self.assertEqual(file_sha256(self.backup), digest)

    def test_timeout_removes_only_new_incomplete_backup(self):
        with patch("tools.memory_database.time.monotonic", side_effect=[0, 99]):
            with self.assertRaises(TimeoutError):
                backup_database(self.path, self.backup, timeout=1)
        self.assertFalse(self.backup.exists())
        self.assertEqual(inspect_database(self.path)["row_counts"]["messages"], 1)


if __name__ == "__main__":
    unittest.main()
