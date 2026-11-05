"""验证备份包含 WAL 数据、可恢复，且不会覆盖源库或输出聊天内容。"""

from contextlib import redirect_stdout
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest

from memory_store import MemoryStore
from tools.memory_database import backup_database, initialize_database, inspect_database, main


class MemoryDatabaseTests(unittest.TestCase):
    def test_initialize_refuses_existing_database(self):
        with tempfile.TemporaryDirectory(prefix="小猫数据库 ") as directory:
            path = Path(directory) / "memory.sqlite3"
            self.assertTrue(initialize_database(path)["created"])
            store = MemoryStore(path)
            conversation = store.create_conversation("owner", "cat")
            store.append_message(conversation, "user", "已有聊天")
            with self.assertRaises(FileExistsError):
                initialize_database(path)
            self.assertEqual(inspect_database(path)["row_counts"]["messages"], 1)

    def test_backup_includes_committed_wal_and_is_standalone(self):
        with tempfile.TemporaryDirectory(prefix="小猫备份 ") as directory:
            path = Path(directory) / "memory.sqlite3"
            initialize_database(path)
            keeper = sqlite3.connect(path)
            try:
                keeper.execute("PRAGMA journal_mode = WAL")
                keeper.execute("PRAGMA wal_autocheckpoint = 0")
                store = MemoryStore(path)
                conversation = store.create_conversation("owner", "cat")
                store.append_message(conversation, "user", "这条消息保存在 WAL")
                self.assertGreater(Path(str(path) + "-wal").stat().st_size, 0)
                output = Path(directory) / "snapshot.sqlite3"
                report = backup_database(path, output)
                self.assertEqual(report["row_counts"]["messages"], 1)
                self.assertEqual(len(report["sha256"]), 64)
                self.assertFalse(Path(str(output) + "-wal").exists())
                restored = MemoryStore(output)
                messages = restored.context_for("owner", "cat")["messages"]
                self.assertEqual(messages[0]["content"], "这条消息保存在 WAL")
                self.assertEqual(store.pending_messages(conversation)[0]["content"], messages[0]["content"])
            finally:
                keeper.close()

    def test_backup_preserves_existing_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "memory.sqlite3"
            initialize_database(source)
            output = Path(directory) / "existing.sqlite3"
            output.write_bytes(b"keep this file")
            with self.assertRaises(FileExistsError):
                backup_database(source, output)
            self.assertEqual(output.read_bytes(), b"keep this file")

    def test_missing_source_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.sqlite3"
            with self.assertRaises(FileNotFoundError):
                inspect_database(missing)
            self.assertFalse(missing.exists())

    def test_inspect_output_does_not_contain_chat(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.sqlite3"
            initialize_database(path)
            store = MemoryStore(path)
            conversation = store.create_conversation("owner", "cat")
            secret_text = "不应显示的聊天内容"
            store.append_message(conversation, "user", secret_text)
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["inspect", "--database", str(path)]), 0)
            self.assertNotIn(secret_text, output.getvalue())
            self.assertIn('"messages": 1', output.getvalue())


if __name__ == "__main__":
    unittest.main()
