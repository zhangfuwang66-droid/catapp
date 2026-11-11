"""设备管理安全复核；仅临时数据库、模拟账号和设备，不连接模型或硬件。"""

import asyncio
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from support import server_app
from access_control import AccessSettings, SESSION_COOKIE
from admin_fleet import FleetStore
from async_helpers import run_blocking
from identity_store import csrf_for_session
from memory_store import MemoryStore, SummaryResult
from test_admin_writer_revocation import ObservedLock


async def summarize(_messages):
    return SummaryResult(summary='模拟摘要')


class AdminFleetSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 2
        self.temp = tempfile.TemporaryDirectory(prefix='admin-fleet-security-')
        self.memory = MemoryStore(Path(self.temp.name) / 'memory.sqlite3')
        self.app = server_app.create_app(store=self.memory, access=AccessSettings('accounts'), summarizer=summarize)
        self.ids, self.admins = self.app.state.identities, self.app.state.admin_store
        self.now = time.time()
        self.ids.clock = lambda: self.now
        self.actor = self.ids.create_user('fictional_fleet_admin', 'fictional-password-123')
        self.second = self.ids.create_user('fictional_fleet_second', 'fictional-password-123')
        self.member = self.ids.create_user('fictional_fleet_member', 'fictional-password-123')
        for user in (self.actor, self.second):
            self.admins.set_role(user.username, True)
        self.headers = self.login(self.actor)
        self.second_headers = self.login(self.second)
        self.store = FleetStore(self.admins)
        self.client = AsyncClient(transport=ASGITransport(self.app), base_url='http://testserver')
        self.sequence = 0

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.app.state.devices.close()
        self.temp.cleanup()

    def login(self, user):
        grant = self.ids.login(user.username, 'fictional-password-123', client_key='fictional')
        return {'Cookie': f'{SESSION_COOKIE}={grant.token}', 'Origin': 'http://testserver',
                'X-CSRF-Token': csrf_for_session(grant.token)}

    def request(self, operation, device_id, *, headers=None, **extra):
        method = 'DELETE' if operation == 'delete' else 'PATCH' if operation == 'rename' else 'POST'
        path = '/api/admin/devices'
        body = {'confirm_device_id': device_id}
        if operation == 'register':
            body.update(id=device_id, display_name='模拟新设备')
        else:
            path += '/' + device_id
            if operation == 'rename':
                body['display_name'] = '模拟新名称'
            elif operation != 'delete':
                path += '/' + ('rotate-credential' if operation == 'rotate' else operation)
        return self.client.request(method, path, headers=headers or self.headers, json={**body, **extra})

    async def registered(self, device_id=None):
        self.sequence += 1
        device_id = device_id or f'cat-fleet-security-{self.sequence}'
        response = await self.request('register', device_id)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['credential']

    def audit(self, device_id):
        return self.admins.audit(device_id, 1, 100)['items']

    def reset_writer(self):
        self.now += 301
        self.admins.set_role(self.actor.username, True)
        self.headers = self.login(self.actor)

    async def prepared(self, operation):
        if operation == 'register':
            self.sequence += 1
            return f'cat-fleet-security-{self.sequence}'
        credential = await self.registered()
        device_id = credential['device_id']
        if operation == 'delete':
            response = await self.request('revoke', device_id)
            self.assertEqual(response.status_code, 200, response.text)
        return device_id

    async def test_queued_old_snapshot_cannot_revoke_a_reregistered_device(self):
        credential = await self.registered()
        device_id = credential['device_id']
        entered, release = asyncio.Event(), threading.Event()
        loop, original = asyncio.get_running_loop(), FleetStore.snapshot
        first = True

        def delayed(store, target):
            nonlocal first
            snapshot = original(store, target)
            if target == device_id and first:
                first = False
                loop.call_soon_threadsafe(entered.set)
                if not release.wait(3):
                    raise TimeoutError('模拟旧快照未释放')
            return snapshot

        with patch.object(FleetStore, 'snapshot', delayed):
            pending = asyncio.create_task(self.request('revoke', device_id))
            try:
                await asyncio.wait_for(entered.wait(), timeout=3)
                for operation in ('revoke', 'delete', 'register'):
                    response = await self.request(operation, device_id, headers=self.second_headers)
                    self.assertEqual(response.status_code, 201 if operation == 'register' else 200, response.text)
                replacement = response.json()['credential']
            finally:
                release.set()
            response = await asyncio.wait_for(pending, timeout=3)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIsNotNone(self.ids.credential_device(device_id, replacement['token']))

    async def test_cli_credential_rotation_invalidates_an_old_fleet_snapshot(self):
        credential = await self.registered()
        device_id = credential['device_id']
        initial = self.store.snapshot(device_id)
        original, replacements = FleetStore.mutate, []

        def rotated(store, *args, **kwargs):
            replacements.append(self.ids.rotate_device_credential(device_id))
            return original(store, *args, **kwargs)

        with patch.object(FleetStore, 'mutate', rotated):
            response = await self.request('revoke', device_id)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.store.snapshot(device_id)['access_epoch'], initial['access_epoch'])
        self.assertIsNotNone(self.ids.credential_device(device_id, replacements[0].token))
        for endpoint in ('devices', 'devices/' + device_id, 'audit', 'dashboard'):
            result = await self.client.get('/api/admin/' + endpoint, headers=self.headers)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertNotIn('_credential_version', result.text)
            self.assertNotIn('token_hash', result.text)
            self.assertNotIn(replacements[0].token, result.text)

    async def test_all_five_writes_recheck_original_session_and_role_in_the_transaction(self):
        for operation in ('register', 'rename', 'rotate', 'revoke', 'delete'):
            for mode in ('session', 'role'):
                with self.subTest(operation=operation, invalidation=mode):
                    self.reset_writer()
                    device_id = await self.prepared(operation)
                    before = self.store.snapshot(device_id)
                    entered, release = asyncio.Event(), threading.Event()
                    loop, original = asyncio.get_running_loop(), FleetStore.mutate

                    def delayed(store, *args, **kwargs):
                        loop.call_soon_threadsafe(entered.set)
                        if not release.wait(5):
                            raise TimeoutError('模拟设备事务未释放')
                        return original(store, *args, **kwargs)

                    with patch.object(FleetStore, 'mutate', delayed):
                        pending = asyncio.create_task(self.request(operation, device_id))
                        try:
                            await asyncio.wait_for(entered.wait(), timeout=3)
                            if mode == 'role':
                                await run_blocking(self.admins.set_role, self.actor.username, False)
                            else:
                                await run_blocking(self.ids.revoke_session, self.headers['Cookie'].split('=', 1)[1])
                        finally:
                            release.set()
                        response = await asyncio.wait_for(pending, timeout=3)
                    self.assertEqual(response.status_code, 403 if mode == 'role' else 401, response.text)
                    self.assertEqual(self.store.snapshot(device_id), before)
                    self.assertEqual(self.audit(device_id)[0]['result'], 'failed')

    async def test_cancelling_all_five_device_lock_queues_leaves_no_write_or_audit(self):
        for operation in ('register', 'rename', 'rotate', 'revoke', 'delete'):
            with self.subTest(operation=operation):
                device_id = await self.prepared(operation)
                before, audit = self.store.snapshot(device_id), self.audit(device_id)
                lock = ObservedLock()
                self.app.state.devices._locks[device_id] = lock
                await lock.acquire()
                baseline = asyncio.all_tasks()
                pending = asyncio.create_task(self.request(operation, device_id))
                try:
                    await lock.wait_for_waiters()
                    pending.cancel()
                    done, _ = await asyncio.wait({pending}, timeout=1)
                finally:
                    lock.release()
                    result = await asyncio.wait_for(asyncio.gather(pending, return_exceptions=True), timeout=3)
                self.assertEqual(done, {pending})
                self.assertIsInstance(result[0], asyncio.CancelledError)
                self.assertEqual(self.store.snapshot(device_id), before)
                self.assertEqual(self.audit(device_id), audit)
                self.assertEqual([task for task in asyncio.all_tasks() - baseline if not task.done()], [])

    async def test_guest_account_lock_precedes_device_lock_and_cancellation_releases_it(self):
        credential = await self.registered()
        device_id = credential['device_id']
        pairing = self.ids.request_pairing(device_id, credential['token'])
        self.ids.claim_pairing(pairing.code, self.member.id, client_key='fictional')
        loan = self.ids.create_loan(device_id, self.member.id, 1)
        self.ids.claim_loan(loan['code'], self.second.id, client_key='fictional')
        guest, device = ObservedLock(), ObservedLock()
        owner, actor = ObservedLock(), ObservedLock()
        self.app.state.model_locks.update({self.second.id: guest, self.member.id: owner, self.actor.id: actor})
        self.app.state.devices._locks[device_id] = device
        before, audit = self.store.snapshot(device_id), self.audit(device_id)
        await guest.acquire()
        await device.acquire()
        pending = asyncio.create_task(self.request('revoke', device_id))
        try:
            await guest.wait_for_waiters()
            self.assertEqual(device.waiting, 0)
            guest.release()
            await device.wait_for_waiters()
            self.assertTrue(guest.locked())
            self.assertFalse(owner.locked())
            self.assertFalse(actor.locked())
            pending.cancel()
            result = await asyncio.wait_for(asyncio.gather(pending, return_exceptions=True), timeout=3)
            self.assertIsInstance(result[0], asyncio.CancelledError)
            self.assertFalse(guest.locked())
        finally:
            device.release()
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            if guest.locked():
                guest.release()
        self.assertEqual(self.store.snapshot(device_id), before)
        self.assertEqual(self.audit(device_id), audit)
        self.assertIsNotNone(self.ids.credential_device(device_id, credential['token']))

    async def test_lost_one_time_credentials_require_explicit_rotation_after_audit_or_cleanup_failure(self):
        for operation, failure in (('register', 'audit'), ('rotate', 'cleanup')):
            with self.subTest(operation=operation, failure=failure):
                device_id = await self.prepared(operation)
                lost = 'fictional-lost-one-time-credential-' + operation
                target = self.admins if failure == 'audit' else self.app.state.connections
                method = 'finish_action' if failure == 'audit' else 'revoke_device'
                with patch('admin_fleet.secrets.token_urlsafe', return_value=lost) as issue, \
                     patch.object(target, method, side_effect=OSError('fictional-private-fleet-failure')):
                    response = await self.request(operation, device_id)
                self.assertEqual(response.status_code, 503, response.text)
                self.assertIn('已生效', response.json()['detail'])
                self.assertNotIn(lost, response.text)
                self.assertNotIn('fictional-private-fleet-failure', response.text)
                self.assertEqual(issue.call_count, 1)
                self.assertIsNotNone(self.ids.credential_device(device_id, lost))
                self.assertEqual(self.audit(device_id)[0]['result'], 'started')
                for endpoint in ('devices', 'devices/' + device_id, 'audit', 'dashboard'):
                    result = await self.client.get('/api/admin/' + endpoint, headers=self.headers)
                    self.assertEqual(result.status_code, 200, result.text)
                    self.assertNotIn(lost, result.text)
                    self.assertNotIn('token_hash', result.text)
                    self.assertNotIn('_credential_version', result.text)
                duplicate = await self.request('register', device_id)
                self.assertEqual(duplicate.status_code, 409, duplicate.text)
                recovery = await self.request('rotate', device_id)
                self.assertEqual(recovery.status_code, 200, recovery.text)
                self.assertIsNone(self.ids.credential_device(device_id, lost))
                replacement = recovery.json()['credential']['token']
                self.assertNotEqual(replacement, lost)
                self.assertIsNotNone(self.ids.credential_device(device_id, replacement))

    async def test_credential_generation_and_start_audit_failures_leave_no_device(self):
        for failure in ('generation', 'start-audit'):
            with self.subTest(failure=failure):
                device_id = await self.prepared('register')
                if failure == 'generation':
                    injection = patch('admin_fleet.secrets.token_urlsafe',
                                      side_effect=OSError('fictional-private-issuance-failure'))
                else:
                    injection = patch.object(self.admins, 'begin_action',
                                             side_effect=sqlite3.OperationalError('fictional-private-start-failure'))
                with injection:
                    response = await self.request('register', device_id)
                self.assertEqual(response.status_code, 503, response.text)
                self.assertNotIn('已生效', response.text)
                self.assertNotIn('fictional-private', response.text)
                self.assertIsNone(self.store.snapshot(device_id))
                if failure == 'generation':
                    self.assertEqual(self.audit(device_id)[0]['result'], 'failed')
                else:
                    self.assertEqual(self.audit(device_id), [])

    async def test_cancelled_committed_revoke_finishes_cleanup_and_preserves_guest_history_and_memory(self):
        credential = await self.registered()
        device_id = credential['device_id']
        pairing = self.ids.request_pairing(device_id, credential['token'])
        self.ids.claim_pairing(pairing.code, self.member.id, client_key='fictional')
        loan = self.ids.create_loan(device_id, self.member.id, 1)
        self.ids.claim_loan(loan['code'], self.second.id, client_key='fictional')
        conversation = self.memory.create_conversation(self.second.id, device_id)
        message = self.memory.append_message(conversation, 'user', '模拟借用者的保留正文')
        self.memory.save_summary(conversation, message, '模拟保留摘要', ['模拟保留事实'])
        self.app.state.memory_retention.set_mode(self.second.id, device_id, 'temporary')
        before = self.memory.context_for(self.second.id, device_id)
        runtime = await self.app.state.devices.resolve(device_id, self.second.id)
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.app.state.connections.revoke_device

        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        with patch.object(self.app.state.connections, 'revoke_device', side_effect=delayed):
            pending = asyncio.create_task(self.request('revoke', device_id))
            try:
                await asyncio.wait_for(entered.wait(), timeout=3)
                self.assertIsNone(self.ids.credential_device(device_id, credential['token']))
                pending.cancel()
                done, _ = await asyncio.wait({pending}, timeout=.05)
                self.assertEqual(done, set())
                self.assertTrue(self.app.state.devices.lock_for(device_id).locked())
            finally:
                release.set()
                result = await asyncio.wait_for(asyncio.gather(pending, return_exceptions=True), timeout=3)
        self.assertIsInstance(result[0], asyncio.CancelledError)
        self.assertTrue(runtime._closed)
        self.assertNotIn(device_id, self.app.state.devices.devices)
        self.assertEqual(self.audit(device_id)[0]['result'], 'success')
        self.assertEqual(self.memory.context_for(self.second.id, device_id), before)
        self.assertEqual(self.app.state.memory_retention.preview(self.second.id, device_id)['mode'], 'temporary')
        with self.ids._transaction() as db:
            memberships = db.execute('SELECT ended_at FROM device_memberships WHERE device_id=?', (device_id,)).fetchall()
            self.assertEqual(len(memberships), 2)
            self.assertTrue(all(row[0] is not None for row in memberships))
            self.assertIsNotNone(db.execute('SELECT ended_at FROM device_loans WHERE device_id=?', (device_id,)).fetchone()[0])
        response = await self.request('delete', device_id)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.memory.context_for(self.second.id, device_id), before)

