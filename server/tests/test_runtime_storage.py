"""录制清理仅处理受管理图片，保护正在使用的文件与未知资料。"""

import asyncio
from pathlib import Path
import os
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from runtime_storage import RecordRepository, RecordingSpaceError, RuntimeStorage, StorageSettings


class RecordRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "records"
        self.settings = StorageSettings(retention_days=7, max_bytes=100, min_free_bytes=0)
        self.repository = RecordRepository(self.root, self.settings)

    def image(self, seq, *, days=0, size=10):
        path = self.root / "cat-a" / "20261105-120000" / f"1730800000000_{seq:04d}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
        modified = time.time() - days * 86400
        os.utime(path, (modified, modified))
        return path

    def test_expired_images_removed_but_active_and_unknown_files_kept(self):
        old = self.image(1, days=8)
        active = self.image(2, days=9)
        recent = self.image(3)
        unknown = self.root / "memory.sqlite3"
        unknown.write_bytes(b"private database")
        manual = recent.with_name("manual.jpg")
        manual.write_bytes(b"manual picture")
        report = self.repository.cleanup({active})
        self.assertEqual(report["removed_images"], 1)
        self.assertFalse(old.exists())
        self.assertTrue(all(p.exists() for p in (active, recent, unknown, manual)))

    def test_capacity_reclaims_oldest_unprotected_image(self):
        first = self.image(1, days=2, size=50)
        second = self.image(2, days=1, size=50)
        report = self.repository.cleanup(set(), incoming=20)
        self.assertEqual(report["managed_bytes"], 50)
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())

    def test_full_active_quota_rejects_new_write(self):
        active = self.image(1, size=100)
        target = active.with_name("1730800000000_0002.jpg")
        with self.assertRaises(RecordingSpaceError):
            self.repository.write(target, b"new", {active})
        self.assertFalse(target.exists())
        self.assertTrue(active.exists())

    def test_low_disk_rejects_new_write_and_keeps_existing(self):
        existing = self.image(1)
        target = existing.with_name("1730800000000_0002.jpg")
        with patch("runtime_storage.shutil.disk_usage", return_value=SimpleNamespace(free=0)):
            with self.assertRaises(RecordingSpaceError):
                self.repository.write(target, b"new", set())
        self.assertFalse(target.exists())
        self.assertTrue(existing.exists())

    def test_write_refuses_overwrite_and_outside_root(self):
        existing = self.image(1)
        with self.assertRaises(FileExistsError):
            self.repository.write(existing, b"overwrite", set())
        with self.assertRaises(ValueError):
            self.repository.write(self.root.parent / "outside.jpg", b"outside", set())
        self.assertEqual(existing.read_bytes(), b"x" * 10)

    def test_directory_links_do_not_expand_cleanup_scope(self):
        outside = self.root.parent / "other"
        outside.mkdir()
        real = outside / "1730800000000_0001.jpg"
        real.write_bytes(b"keep outside")
        self.root.mkdir()
        device = self.root / "cat-a"
        device.mkdir()
        linked = device / "20261105-120000"
        try:
            linked.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("当前用户没有符号链接权限")
        self.assertEqual(self.repository.cleanup(set())["removed_images"], 0)
        self.assertEqual(real.read_bytes(), b"keep outside")


class RuntimeStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_pinned_image_stays_until_released(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "records"
            storage = RuntimeStorage(root, StorageSettings(retention_days=1, max_bytes=100, min_free_bytes=0))
            image = root / "cat-a" / "20261105-120000" / "1730800000000_0001.jpg"
            with storage.pin(image):
                await storage.write_image(image, b"image")
                old = time.time() - 2 * 86400
                os.utime(image, (old, old))
                self.assertEqual((await storage.run_once())["removed_images"], 0)
            self.assertEqual((await storage.run_once())["removed_images"], 1)

    async def test_lifecycle_stops_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = RuntimeStorage(Path(directory) / "records", StorageSettings(interval_seconds=.01))
            await storage.start()
            await asyncio.sleep(.02)
            await storage.close()
            self.assertIsNone(storage._task)
            self.assertIsNotNone(storage.last_report)


if __name__ == "__main__":
    unittest.main()
