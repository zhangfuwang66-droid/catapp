"""无版本旧库、事务迁移、并发摘要与关系检查；仅使用临时数据库。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from database_schema import MEMORY_SCHEMA_VERSION
from identity_store import IdentityStore
from memory_store import MemoryStore
from tools.memory_database import inspect_database


class DatabaseIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "memory.sqlite3"

    def test_legacy_memory_is_versioned_without_losing_records(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executescript(MemoryStore._SCHEMA)
        store = MemoryStore(self.path)
        conversation = store.create_conversation("old-owner", "old-cat")
        store.append_message(conversation, "user", "旧记忆")
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("DELETE FROM schema_migrations WHERE component='memory'")
            connection.commit()
        reopened = MemoryStore(self.path)
        self.assertEqual(reopened.pending_messages(conversation)[0]["content"], "旧记忆")
        self.assertEqual(inspect_database(self.path)["schema_versions"]["memory"], MEMORY_SCHEMA_VERSION)

    def test_future_component_versions_block_both_writers(self):
        MemoryStore(self.path)
        IdentityStore(self.path)
        for component in ("memory", "identity"):
            with closing(sqlite3.connect(self.path)) as connection:
                old = connection.execute("SELECT version FROM schema_migrations WHERE component=?", (component,)).fetchone()[0]
                connection.execute("UPDATE schema_migrations SET version=999 WHERE component=?", (component,))
                connection.commit()
            for writer in (MemoryStore, IdentityStore):
                with self.subTest(component=component, writer=writer.__name__), self.assertRaises(ValueError):
                    writer(self.path)
            with closing(sqlite3.connect(self.path)) as connection:
                self.assertEqual(connection.execute("SELECT version FROM schema_migrations WHERE component=?", (component,)).fetchone()[0], 999)
                connection.execute("UPDATE schema_migrations SET version=? WHERE component=?", (old, component))
                connection.commit()

    def test_failed_initial_schema_rolls_back_all_tables(self):
        with patch.object(MemoryStore, "_SCHEMA", MemoryStore._SCHEMA + "; INVALID SQL;"):
            with self.assertRaises(sqlite3.Error):
                MemoryStore(self.path)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [])

    def test_partial_legacy_schema_is_not_silently_repaired(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("CREATE TABLE conversations(id INTEGER PRIMARY KEY)")
            connection.commit()
        with self.assertRaises(ValueError):
            MemoryStore(self.path)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM sqlite_master WHERE name='messages'").fetchone())

    def test_parallel_duplicate_summaries_commit_once_and_stale_result_is_ignored(self):
        store = MemoryStore(self.path)
        conversation = store.create_conversation("owner", "cat")
        first = store.append_message(conversation, "user", "第一条")
        second = store.append_message(conversation, "assistant", "第二条")
        other = MemoryStore(self.path)
        barrier = threading.Barrier(2)

        def save(writer):
            barrier.wait(timeout=5)
            writer.save_summary(conversation, second, "同一批摘要", ["同一条记忆"])

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(save, writer) for writer in (store, other)]
            for future in futures:
                future.result(timeout=10)
        store.save_summary(conversation, first, "迟到的摘要", ["不应写入的事实"])
        context = store.context_for("owner", "cat")
        self.assertEqual(len(context["summaries"]), 1)
        self.assertEqual([item["content"] for item in context["memories"]], ["同一条记忆"])
        self.assertEqual(store.pending_messages(conversation), [])

    def test_summary_boundary_must_belong_to_same_conversation(self):
        store = MemoryStore(self.path)
        first = store.create_conversation("owner-a", "cat-a")
        store.append_message(first, "user", "A 的消息")
        second = store.create_conversation("owner-b", "cat-b")
        wrong = store.append_message(second, "user", "B 的消息")
        with self.assertRaises(ValueError):
            store.save_summary(first, wrong, "错误边界")
        self.assertEqual(inspect_database(self.path)["row_counts"]["conversation_summaries"], 0)

    def test_inspection_reports_identity_pending_and_storage_without_private_values(self):
        store = MemoryStore(self.path)
        identities = IdentityStore(self.path)
        user = identities.create_user("alice", "test-only-password")
        credential = identities.register_device("cat-a", "测试猫")
        pair = identities.request_pairing("cat-a", credential.token)
        identities.claim_pairing(pair.code, user.id, client_key="local")
        conversation = store.create_conversation(user.id, "cat-a")
        store.append_message(conversation, "user", "不该出现在报告中的内容")
        report = inspect_database(self.path)
        self.assertEqual(report["identity_row_counts"]["auth_users"], 1)
        self.assertEqual(report["identity_row_counts"]["devices"], 1)
        self.assertEqual(report["pending_conversations"], 1)
        self.assertGreater(report["storage"]["logical_bytes"], 0)
        for secret in (credential.token, pair.code, "test-only-password", "不该出现在报告中的内容", user.id):
            self.assertNotIn(secret, str(report))

    def test_orphan_and_cross_owner_records_are_rejected(self):
        store = MemoryStore(self.path)
        conversation = store.create_conversation("a", "cat-a")
        message = store.append_message(conversation, "user", "内容")
        store.save_summary(conversation, message, "摘要", ["事实"])
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("UPDATE memories SET user_id='b'")
            connection.commit()
        with self.assertRaises(ValueError):
            inspect_database(self.path)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("UPDATE memories SET user_id='a'")
            connection.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES (999,'user','orphan','now')")
            connection.commit()
        with self.assertRaises(ValueError):
            inspect_database(self.path)

    def test_invalid_summary_watermark_is_rejected(self):
        store = MemoryStore(self.path)
        conversation = store.create_conversation("owner", "cat")
        store.append_message(conversation, "user", "内容")
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("UPDATE conversations SET summarized_through_message_id=999")
            connection.commit()
        with self.assertRaises(ValueError):
            inspect_database(self.path)


if __name__ == "__main__":
    unittest.main()
