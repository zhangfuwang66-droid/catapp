"""备份管理的锁队列、部分完成与审计故障，使用临时文件和事件。"""

import asyncio
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from support import server_app
from access_control import AccessSettings, SESSION_COOKIE
from async_helpers import run_blocking
from database_backups import BackupSettings
from identity_store import csrf_for_session
from memory_store import MemoryStore, SummaryResult
from test_admin_writer_revocation import ObservedLock


async def summarize(_messages):
    return SummaryResult(summary='模拟摘要')


class AdminMaintenanceSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 2
        self.temp = tempfile.TemporaryDirectory(prefix='admin-maintenance-security-')
        self.root = Path(self.temp.name)
        self.app = server_app.create_app(
            store=MemoryStore(self.root / 'memory.sqlite3'), access=AccessSettings('accounts'),
            summarizer=summarize, backup_settings=BackupSettings(self.root / 'backups', retain=2, min_free_bytes=0))
        self.ids, self.admins = self.app.state.identities, self.app.state.admin_store
        self.user = self.ids.create_user('fictional_backup_admin', 'fictional-password-123')
        second = self.ids.create_user('fictional_backup_second', 'fictional-password-123')
        self.admins.set_role(second.username, True)
        self.reset_writer()
        self.service = self.app.state.database_backups
        self.client = AsyncClient(transport=ASGITransport(self.app), base_url='http://testserver')

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.service.close()
        await self.app.state.devices.close()
        self.temp.cleanup()

    def check(self):
        return self.client.post('/api/admin/backups/run', headers=self.headers, json={'confirm': '检查并备份'})

    def audit(self):
        return self.admins.audit('check-backup', 1, 20)['items']

    def reset_writer(self):
        self.admins.set_role(self.user.username, True)
        self.grant = self.ids.login(self.user.username, 'fictional-password-123', client_key='fictional')
        self.headers = {'Cookie': f'{SESSION_COOKIE}={self.grant.token}', 'Origin': 'http://testserver',
                        'X-CSRF-Token': csrf_for_session(self.grant.token)}

    async def invalidate(self, mode):
        if mode == 'role':
            await run_blocking(self.admins.set_role, self.user.username, False)
        else:
            await run_blocking(self.ids.revoke_session, self.grant.token)

    async def test_cancelling_backup_lock_queue_does_not_run_a_later_backup(self):
        lock = ObservedLock()
        self.service._lock = lock
        await lock.acquire()
        baseline = asyncio.all_tasks()
        with patch.object(self.service.repository, 'create', wraps=self.service.repository.create) as create:
            pending = asyncio.create_task(self.check())
            try:
                await lock.wait_for_waiters()
                pending.cancel()
                done, _ = await asyncio.wait({pending}, timeout=1)
            finally:
                lock.release()
                result = await asyncio.gather(pending, return_exceptions=True)
        self.assertEqual(done, {pending}, '等待备份锁的已取消请求仍继续排队')
        self.assertIsInstance(result[0], asyncio.CancelledError)
        create.assert_not_called()
        self.assertEqual(self.audit(), [])
        self.assertEqual([task for task in asyncio.all_tasks() - baseline if not task.done()], [])

    async def test_created_backup_with_failed_retention_keeps_incomplete_audit(self):
        with patch.object(self.service.repository, 'prune', side_effect=OSError('fictional-private-retention-path')):
            response = await self.check()
        self.assertEqual(response.status_code, 503, response.text)
        entries, skipped = self.service.repository.verified_entries()
        self.assertEqual((len(entries), skipped), (1, 0))
        self.assertIn('已生效', response.json()['detail'])
        self.assertNotIn('fictional-private-retention-path', response.text)
        self.assertEqual(self.audit()[0]['result'], 'started')
        retry = await self.check()
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertFalse(retry.json()['created'])
        self.assertEqual(len(self.service.repository.verified_entries()[0]), 1)

    async def test_backup_lock_queue_rechecks_session_and_role_before_file_io(self):
        for mode in ('session', 'role'):
            with self.subTest(invalidation=mode):
                self.reset_writer()
                lock = ObservedLock()
                self.service._lock = lock
                await lock.acquire()
                with patch.object(self.service.repository, 'verified_entries',
                                  wraps=self.service.repository.verified_entries) as verify:
                    pending = asyncio.create_task(self.check())
                    try:
                        await lock.wait_for_waiters()
                        await self.invalidate(mode)
                    finally:
                        lock.release()
                    response = await asyncio.wait_for(pending, timeout=3)
                self.assertEqual(response.status_code, 403 if mode == 'role' else 401, response.text)
                verify.assert_not_called()
                self.assertFalse(self.service.repository.directory.exists())
                self.assertEqual(self.audit(), [])

    async def test_authorization_transaction_rechecks_after_admission(self):
        for mode in ('session', 'role'):
            with self.subTest(invalidation=mode):
                self.reset_writer()
                entered, release = asyncio.Event(), asyncio.Event()

                async def delayed(function, *args, **kwargs):
                    entered.set()
                    await release.wait()
                    return await run_blocking(function, *args, **kwargs)

                with patch('admin_maintenance.run_blocking', side_effect=delayed), \
                     patch.object(self.service.repository, 'verified_entries',
                                  wraps=self.service.repository.verified_entries) as verify:
                    pending = asyncio.create_task(self.check())
                    try:
                        await asyncio.wait_for(entered.wait(), timeout=3)
                        await self.invalidate(mode)
                    finally:
                        release.set()
                    response = await asyncio.wait_for(pending, timeout=3)
                self.assertEqual(response.status_code, 403 if mode == 'role' else 401, response.text)
                verify.assert_not_called()
                self.assertFalse(self.service.repository.directory.exists())
                self.assertEqual(self.audit()[0]['result'], 'failed')

    async def test_started_backup_finishes_cleanup_and_audit_before_cancellation_exits(self):
        entered, release = asyncio.Event(), threading.Event()
        loop, original = asyncio.get_running_loop(), self.service.repository.prune
        baseline = asyncio.all_tasks()

        def delayed(*args, **kwargs):
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(5):
                raise TimeoutError('模拟备份清理未释放')
            return original(*args, **kwargs)

        with patch.object(self.service.repository, 'prune', side_effect=delayed):
            pending = asyncio.create_task(self.check())
            try:
                await asyncio.wait_for(entered.wait(), timeout=3)
                self.assertEqual(len(self.service.repository.verified_entries()[0]), 1)
                status = await self.client.get('/api/admin/backups', headers=self.headers)
                self.assertTrue(status.json()['running'])
                self.assertFalse(status.json()['scheduler_running'])
                pending.cancel()
                done, _ = await asyncio.wait({pending}, timeout=.05)
                self.assertEqual(done, set(), '已创建的备份在清理前脱离所属请求')
                self.assertTrue(self.service._lock.locked())
            finally:
                release.set()
                result = await asyncio.wait_for(asyncio.gather(pending, return_exceptions=True), timeout=3)
        self.assertIsInstance(result[0], asyncio.CancelledError)
        self.assertEqual(self.audit()[0]['result'], 'success')
        self.assertFalse(self.service.status()['running'])
        self.assertEqual([task for task in asyncio.all_tasks() - baseline if not task.done()], [])

    async def test_cancelled_request_waits_for_started_result_audit(self):
        entered, release = asyncio.Event(), threading.Event()
        loop, original = asyncio.get_running_loop(), self.admins.finish_action

        def delayed(*args):
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(5):
                raise TimeoutError('模拟备份结果审计未释放')
            return original(*args)

        with patch.object(self.admins, 'finish_action', side_effect=delayed):
            pending = asyncio.create_task(self.check())
            try:
                await asyncio.wait_for(entered.wait(), timeout=3)
                pending.cancel()
                done, _ = await asyncio.wait({pending}, timeout=.05)
                self.assertEqual(done, set())
                self.assertTrue(self.service._lock.locked())
                self.assertEqual(len(self.service.repository.verified_entries()[0]), 1)
            finally:
                release.set()
                result = await asyncio.wait_for(asyncio.gather(pending, return_exceptions=True), timeout=3)
        self.assertIsInstance(result[0], asyncio.CancelledError)
        self.assertEqual(self.audit()[0]['result'], 'success')
        self.assertFalse(self.service.status()['running'])

    async def test_created_backup_with_failed_result_audit_is_retained_and_not_duplicated(self):
        with patch.object(self.admins, 'finish_action',
                          side_effect=sqlite3.OperationalError('fictional-private-audit-text')):
            response = await self.check()
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn('已生效', response.json()['detail'])
        self.assertNotIn('fictional-private-audit-text', response.text)
        self.assertEqual(self.audit()[0]['result'], 'started')
        self.assertEqual(len(self.service.repository.verified_entries()[0]), 1)
        retry = await self.check()
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertFalse(retry.json()['created'])
        self.assertFalse(retry.json()['status']['running'])
        self.assertFalse(retry.json()['status']['scheduler_running'])
        self.assertEqual(len(self.service.repository.verified_entries()[0]), 1)

    async def test_partial_retention_of_recent_backups_keeps_incomplete_audit(self):
        for _ in range(self.service.settings.retain + 1):
            self.service.repository.create()
        entries, _ = self.service.repository.verified_entries()
        oldest, receipt = entries[0][1:]
        original = Path.unlink

        def failed_receipt(path, *args, **kwargs):
            if path == receipt:
                raise OSError('fictional-private-receipt-path')
            return original(path, *args, **kwargs)

        with patch.object(self.service.repository, 'create', wraps=self.service.repository.create) as create, \
             patch.object(Path, 'unlink', failed_receipt):
            response = await self.check()
        self.assertEqual(response.status_code, 503, response.text)
        create.assert_not_called()
        self.assertFalse(oldest.exists())
        self.assertTrue(receipt.exists())
        self.assertIn('已生效', response.json()['detail'])
        self.assertNotIn('fictional-private-receipt-path', response.text)
        self.assertEqual(self.audit()[0]['result'], 'started')
        retry = await self.check()
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertFalse(retry.json()['created'])
        self.assertEqual(len(self.service.repository.verified_entries()[0]), self.service.settings.retain)

