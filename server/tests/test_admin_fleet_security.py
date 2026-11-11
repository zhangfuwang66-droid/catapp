"""设备管理安全复核；仅临时数据库、模拟账号和设备，不连接模型或硬件。"""

import asyncio
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from support import server_app
from access_control import AccessSettings, SESSION_COOKIE
from admin_fleet import FleetStore
from identity_store import csrf_for_session
from memory_store import MemoryStore, SummaryResult


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

