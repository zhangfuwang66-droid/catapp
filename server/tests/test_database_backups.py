"""自动备份的校验、轮换边界、低磁盘、重试与服务生命周期。"""

import asyncio
from contextlib import closing, redirect_stdout
import io
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from support import server_app
from access_control import AccessSettings
from database_backups import BackupSettings, BackupRepository, DatabaseBackups
from identity_store import IdentityStore
from memory_store import MemoryStore, SummaryResult
from tools.memory_database import backup_status, check_database_ready, inspect_database, main


class BackupRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = MemoryStore(self.root / "memory.sqlite3")
        conversation = self.store.create_conversation("test-owner", "test-cat")
        self.store.append_message(conversation, "user", "不出现在备份清单中的内容")
        self.settings = BackupSettings(self.root / "snapshots", retain=2, min_free_bytes=0)
        self.repo = BackupRepository(self.store.path, self.settings)

    def test_settings_are_opt_in_and_reject_invalid_values(self):
        with patch.dict("os.environ", {"CYBERMIAO_BACKUP_DIR": ""}):
            self.assertIsNone(BackupSettings.from_environment().directory)
        for options in ({"interval_seconds": math.inf}, {"interval_seconds": math.nan}, {"retain": 0},
                        {"min_free_bytes": -1}, {"timeout_seconds": 0}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                BackupSettings(**options)
        with patch.dict("os.environ", {"CYBERMIAO_BACKUP_DIR": str(self.root), "CYBERMIAO_BACKUP_INTERVAL_SECONDS": "2"}):
            with self.assertRaises(ValueError):
                BackupSettings.from_environment()

    def test_verified_backup_is_standalone_and_manifest_contains_only_metadata(self):
        self.repo.create()
        entries, skipped = self.repo.verified_entries()
        self.assertEqual((len(entries), skipped), (1, 0))
        _, path, receipt = entries[0]
        self.assertEqual(inspect_database(path)["row_counts"]["messages"], 1)
        manifest = receipt.read_text(encoding="utf-8")
        self.assertNotIn("不出现在备份清单中的内容", manifest)
        self.assertNotIn("test-owner", manifest)
        self.assertNotIn(str(self.store.path), manifest)
        self.assertEqual(len(json.loads(manifest)["sha256"]), 64)
        self.assertFalse(Path(str(path) + "-wal").exists())

    def test_rotation_preserves_manual_foreign_incomplete_and_corrupt_files(self):
        self.repo.create()
        first = self.repo.verified_entries()[0][0][1]
        first.write_bytes(b"corrupt backup is kept for inspection")
        manual = self.settings.directory / "manual.sqlite3"
        manual.write_bytes(b"keep manual")
        other_store = MemoryStore(self.root / "other.sqlite3")
        other_repo = BackupRepository(other_store.path, self.settings)
        other_repo.create()
        other_file = other_repo.verified_entries()[0][0][1]
        for _ in range(3):
            self.repo.create()
        entries, skipped = self.repo.verified_entries()
        self.assertEqual((len(entries), skipped), (3, 1))
        removed = self.repo.prune(entries)
        self.assertEqual(removed, 1)
        self.assertEqual(len(self.repo.verified_entries()[0]), 2)
        self.assertEqual(first.read_bytes(), b"corrupt backup is kept for inspection")
        self.assertEqual(manual.read_bytes(), b"keep manual")
        self.assertTrue(other_file.is_file())
        self.assertEqual(len(other_repo.verified_entries()[0]), 1)

    def test_low_disk_does_not_delete_old_backup_or_modify_source(self):
        self.repo.create()
        original = self.repo.verified_entries()[0][0][1].read_bytes()
        with patch("database_backups.shutil.disk_usage", return_value=SimpleNamespace(free=0)):
            with self.assertRaises(OSError):
                self.repo.create()
        entries, _ = self.repo.verified_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][1].read_bytes(), original)
        self.assertEqual(inspect_database(self.store.path)["row_counts"]["messages"], 1)

    def test_receipt_failure_cleans_only_new_snapshot(self):
        self.repo.create()
        original = self.repo.verified_entries()[0][0][1]
        with patch("database_backups.os.link", side_effect=OSError("模拟发布失败")):
            with self.assertRaises(OSError):
                self.repo.create()
        self.assertEqual(len(self.repo.verified_entries()[0]), 1)
        self.assertEqual(len(list(self.settings.directory.glob("*.sqlite3"))), 1)
        self.assertTrue(original.exists())
        self.assertFalse(list(self.settings.directory.glob(".cybermiao-receipt-*")))

    def test_existing_filename_on_failed_backup_is_not_deleted(self):
        def collision(_source, target, **kwargs):
            target.write_bytes(b"existing file")
            raise FileExistsError("模拟文件名冲突")
        with patch("database_backups.backup_database", side_effect=collision):
            with self.assertRaises(FileExistsError):
                self.repo.create()
        files = list(self.settings.directory.glob("*.sqlite3"))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_bytes(), b"existing file")

    def test_missing_readiness_database_is_not_created(self):
        missing = self.root / "missing.sqlite3"
        with self.assertRaises(FileNotFoundError):
            check_database_ready(missing)
        self.assertFalse(missing.exists())

    def test_readiness_rejects_missing_required_column(self):
        IdentityStore(self.store.path)
        check_database_ready(self.store.path)
        with closing(sqlite3.connect(self.store.path)) as connection:
            connection.execute("ALTER TABLE auth_sessions RENAME COLUMN token_hash TO obsolete_hash")
            connection.commit()
        with self.assertRaises(ValueError):
            check_database_ready(self.store.path)

    def test_backup_status_survives_missing_source_without_recreating_it(self):
        self.repo.create()
        self.store.path.unlink()
        report = backup_status(self.store.path, self.settings.directory)
        self.assertEqual((report["valid_backups"], report["skipped_unverified"]), (1, 0))
        self.assertEqual(len(report["latest"]["sha256"]), 64)
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["backup-status", "--database", str(self.store.path),
                         "--directory", str(self.settings.directory)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), report)
        self.assertNotIn("不出现在备份清单中的内容", output.getvalue())
        self.assertFalse(self.store.path.exists())

    def test_backup_status_missing_directory_is_readonly(self):
        result = backup_status(self.store.path, self.settings.directory)
        self.assertEqual(result["valid_backups"], 0)
        self.assertIsNone(result["latest"])
        self.assertFalse(self.settings.directory.exists())

    def test_malformed_receipts_are_skipped_and_never_pruned(self):
        self.repo.create()
        _, path, receipt = self.repo.verified_entries()[0][0]
        manifest = json.loads(receipt.read_text(encoding="utf-8"))
        invalid_times = [json.dumps({**manifest, "created_at": created})
                         for created in (True, 10 ** 400, math.nan, math.inf)]
        for value in ("not json", "null", "[]", '{"created_at":"invalid"}', *invalid_times):
            with self.subTest(value=value):
                receipt.write_text(value, encoding="utf-8")
                entries, skipped = self.repo.verified_entries()
                self.assertEqual((len(entries), skipped), (0, 1))
                self.assertEqual(self.repo.prune(entries), 0)
                self.assertTrue(path.exists())
                self.assertEqual(receipt.read_text(encoding="utf-8"), value)


class BackupServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = MemoryStore(self.root / "memory.sqlite3")
        self.settings = BackupSettings(self.root / "backups", min_free_bytes=0)

    async def test_disabled_service_does_not_write_any_backup(self):
        service = DatabaseBackups(self.store.path, BackupSettings())
        await service.start()
        self.assertFalse(await service.run_once())
        self.assertFalse(service.status()["enabled"])
        await service.close()
        self.assertFalse(self.settings.directory.exists())

    async def test_restart_reuses_recent_valid_backup(self):
        first = DatabaseBackups(self.store.path, self.settings)
        self.assertTrue(await first.run_once())
        second = DatabaseBackups(self.store.path, self.settings)
        self.assertFalse(await second.run_once())
        self.assertEqual(second.status()["backup_count"], 1)
        self.assertIsNotNone(second.status()["last_success_at"])

    async def test_restart_applies_lower_retention_without_new_snapshot(self):
        repository = BackupRepository(self.store.path, self.settings)
        for _ in range(3):
            repository.create()
        latest = repository.verified_entries()[0][-1][1]
        service = DatabaseBackups(self.store.path, BackupSettings(self.settings.directory, retain=1, min_free_bytes=0))
        self.assertFalse(await service.run_once())
        self.assertEqual((service.status()["backup_count"], service.status()["pruned"]), (1, 2))
        self.assertEqual(service.repository.verified_entries()[0][0][1], latest)

    async def test_failure_is_reported_and_next_attempt_can_recover(self):
        service = DatabaseBackups(self.store.path, self.settings)
        with patch.object(service.repository, "create", side_effect=OSError("private test path")):
            with self.assertRaises(OSError):
                await service.run_once()
        self.assertEqual(service.status()["last_error_type"], "OSError")
        self.assertNotIn("private test path", json.dumps(service.status()))
        self.assertTrue(await service.run_once())
        self.assertIsNone(service.status()["last_error_type"])

    async def test_close_waits_for_started_backup_thread(self):
        service = DatabaseBackups(self.store.path, self.settings)
        started, release = threading.Event(), threading.Event()
        create = service.repository.create

        def blocked():
            started.set()
            if not release.wait(5):
                raise TimeoutError("测试等待超时")
            return create()

        with patch.object(service.repository, "create", side_effect=blocked):
            await service.start()
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            closing = asyncio.create_task(service.close())
            await asyncio.sleep(.01)
            self.assertFalse(closing.done())
            release.set()
            await closing
        self.assertEqual(len(service.repository.verified_entries()[0]), 1)
        self.assertFalse(service.status()["running"])


async def summarize(messages):
    return SummaryResult(summary="模拟摘要")


class DatabaseReadinessTests(unittest.TestCase):
    def test_public_readiness_hides_details_and_backup_stops_with_service(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = server_app.create_app(store=MemoryStore(root / "memory.sqlite3"), summarizer=summarize,
                                        access=AccessSettings("accounts"),
                                        backup_settings=BackupSettings(root / "backups", min_free_bytes=0))
            with TestClient(app, client=("127.0.0.1", 1000)) as client:
                self.assertEqual(client.get("/api/health").text, "OK")
                self.assertEqual(client.get("/api/ready").text, "OK")
                with patch.object(server_app, "check_database_ready", side_effect=sqlite3.OperationalError("private SQL password")):
                    result = client.get("/api/ready")
                self.assertEqual(result.status_code, 503)
                self.assertEqual(result.text, "NOT READY")
                self.assertIn("no-store", result.headers["cache-control"])
                deadline = time.monotonic() + 3
                while app.state.database_backups.status()["last_success_at"] is None and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertIsNotNone(app.state.database_backups.status()["last_success_at"])
            self.assertFalse(app.state.database_backups.status()["running"])
            self.assertEqual(len(list((root / "backups").glob("*.sqlite3"))), 1)


if __name__ == "__main__":
    unittest.main()
