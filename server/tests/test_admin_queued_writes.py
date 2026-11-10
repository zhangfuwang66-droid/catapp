"""通过真实 ASGI 请求和锁事件复现撤销会话时的排队写入竞争。"""

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from support import server_app
from access_control import AccessSettings, SESSION_COOKIE, require_session_write
from async_helpers import run_blocking
from device_registry import DeviceLock, DeviceRegistry
from identity_store import csrf_for_session
from memory_store import MemoryStore


class ObservedLock(asyncio.Lock):
    def __init__(self):
        super().__init__()
        self.waiting = asyncio.Event()

    async def acquire(self):
        if self.locked():
            self.waiting.set()
        return await super().acquire()


class ObservedDeviceLock(DeviceLock):
    def __init__(self):
        super().__init__()
        self.waiting = asyncio.Event()

    async def wait_available(self):
        if not self.available:
            self.waiting.set()
        await super().wait_available()


class DeviceAccountLockTests(unittest.IsolatedAsyncioTestCase):
    async def wait_for_count(self, lock, count):
        async def queued():
            while lock.waiting_count < count:
                await asyncio.sleep(0)
        await asyncio.wait_for(queued(), timeout=2)

    async def test_multiple_account_waiters_make_progress_after_device_release(self):
        registry = DeviceRegistry(None)
        device_lock = registry.lock_for('cat-queued')
        account_lock = asyncio.Lock()
        completed = []

        async def write(number):
            async with registry.locked_for_account('cat-queued', account_lock):
                completed.append(number)

        await device_lock.acquire()
        requests = [asyncio.create_task(write(number)) for number in range(3)]
        try:
            await self.wait_for_count(device_lock, 3)
            device_lock.release()
            await asyncio.wait_for(asyncio.gather(*requests), timeout=2)
            self.assertCountEqual(completed, range(3))
        finally:
            for request in requests:
                request.cancel()
            await asyncio.gather(*requests, return_exceptions=True)
            if device_lock.locked():
                device_lock.release()

    async def test_waiting_for_device_leaves_account_available_and_can_cancel(self):
        registry = DeviceRegistry(None)
        device_lock, account_lock = registry.lock_for('cat-queued'), asyncio.Lock()

        async def write():
            async with registry.locked_for_account('cat-queued', account_lock):
                self.fail('尚未释放设备锁时开始写入')

        await device_lock.acquire()
        request = asyncio.create_task(write())
        try:
            await self.wait_for_count(device_lock, 1)
            await asyncio.wait_for(account_lock.acquire(), timeout=2)
            account_lock.release()
            request.cancel()
            result = await asyncio.gather(request, return_exceptions=True)
            self.assertIsInstance(result[0], asyncio.CancelledError)
            self.assertEqual(device_lock.waiting_count, 0)
        finally:
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
            device_lock.release()

    async def test_cancelled_regular_waiter_does_not_strand_account_write(self):
        registry = DeviceRegistry(None)
        device_lock, account_lock = registry.lock_for('cat-queued'), asyncio.Lock()

        async def write():
            async with registry.locked_for_account('cat-queued', account_lock):
                return 'finished'

        await device_lock.acquire()
        ordinary = asyncio.create_task(device_lock.acquire())
        request = asyncio.create_task(write())
        try:
            await self.wait_for_count(device_lock, 2)
            device_lock.release()
            ordinary.cancel()  # 在 release 已唤醒、acquire 尚未恢复的窗口取消。
            await asyncio.gather(ordinary, return_exceptions=True)
            self.assertEqual(await asyncio.wait_for(request, timeout=2), 'finished')
        finally:
            ordinary.cancel()
            request.cancel()
            await asyncio.gather(ordinary, request, return_exceptions=True)
            if device_lock.locked():
                device_lock.release()


class AdminQueuedWriteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='admin-queued-write-')
        self.store = MemoryStore(Path(self.temp.name) / 'memory.sqlite3')
        self.app = server_app.create_app(store=self.store, access=AccessSettings('accounts'))
        self.ids = self.app.state.identities
        self.admin = self.ids.create_user('admin-test', 'temporary-password')
        self.member = self.ids.create_user('owner', 'temporary-password')
        self.app.state.admin_store.set_role('admin-test', True)
        self.headers = self.login('owner')
        self.admin_headers = self.login('admin-test')
        credential = self.ids.register_device('cat-queued', '排队写入测试')
        pairing = self.ids.request_pairing(credential.device_id, credential.token)
        self.ids.claim_pairing(pairing.code, self.member.id, client_key='fixture')
        self.conversation = self.store.create_conversation(self.member.id, 'cat-queued')
        self.store.append_message(self.conversation, 'user', '撤销后必须保留的记忆')
        self.app.state.memory_retention.set_mode(self.member.id, 'cat-queued', 'temporary')
        self.user_lock, self.device_lock = ObservedLock(), ObservedDeviceLock()
        self.app.state.model_locks[self.member.id] = self.user_lock
        self.app.state.devices._locks['cat-queued'] = self.device_lock
        self.client = AsyncClient(transport=ASGITransport(self.app), base_url='http://testserver')

    def login(self, username):
        grant = self.ids.login(username, 'temporary-password', client_key='fixture')
        return {'Cookie': f'{SESSION_COOKIE}={grant.token}', 'Origin': 'http://testserver',
                'X-CSRF-Token': csrf_for_session(grant.token)}

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.app.state.devices.close()
        self.temp.cleanup()

    def write(self, kind):
        if kind == 'speech':
            return self.client.put('/api/account/speech', headers=self.headers,
                                   json={'mode': 'accurate', 'hotwords': ['排队写入']})
        return self.client.request('DELETE', '/api/account/memories/cat-queued', headers=self.headers,
                                   json={'confirm_device_id': 'cat-queued'})

    async def wait_for_queue(self):
        waits = [asyncio.create_task(lock.waiting.wait()) for lock in (self.user_lock, self.device_lock)]
        try:
            done, _ = await asyncio.wait(waits, timeout=2, return_when=asyncio.FIRST_COMPLETED)
            self.assertTrue(done, '请求未进入锁队列')
        finally:
            for task in waits:
                task.cancel()
            await asyncio.gather(*waits, return_exceptions=True)

    async def revoke_queued(self, kind, disabled, *, cancel=True):
        await self.user_lock.acquire()
        await self.device_lock.acquire()
        baseline = asyncio.all_tasks()
        request = asyncio.create_task(self.write(kind))
        queued = set()
        try:
            await self.wait_for_queue()
            queued = asyncio.all_tasks() - baseline
            # 与管理员真实流程一致：持有用户锁，先提交会话撤销，再取消旧 HTTP 请求。
            await run_blocking(self.app.state.admin_store.change_user, self.admin.id, self.member.id, disabled)
            if cancel:
                await asyncio.wait_for(self.app.state.account_operations.cancel_user(self.member.id), timeout=2)
                response = await asyncio.wait_for(request, timeout=2)
                detached = [task for task in queued if not task.done()]
        finally:
            self.device_lock.release()
            self.user_lock.release()
        try:
            if not cancel:
                response = await asyncio.wait_for(request, timeout=2)
                detached = []
            # 旧实现的 shield 子任务会在释放锁后继续写；等待它结束再核对真实数据库。
            await asyncio.wait_for(asyncio.gather(*queued, return_exceptions=True), timeout=2)
            self.assertEqual(response.status_code, 401, response.text)
            self.assertEqual(self.app.state.speech.settings.get(self.member.id)['mode'], 'fast')
            self.assertEqual([message['content'] for message in self.store.context_for(self.member.id, 'cat-queued')['messages']],
                             ['撤销后必须保留的记忆'])
            self.assertEqual(detached, [], '被撤销的请求遗留了等待锁的后台写任务')
        finally:
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)

    async def test_disabled_account_cannot_save_queued_speech(self):
        await self.revoke_queued('speech', True)

    async def test_revoked_session_cannot_save_queued_speech(self):
        await self.revoke_queued('speech', None)

    async def test_disabled_account_cannot_clear_queued_memory(self):
        await self.revoke_queued('memory', True)

    async def test_revoked_session_cannot_clear_queued_memory(self):
        await self.revoke_queued('memory', None)

    async def test_speech_rechecks_revocation_after_wait_without_request_cancellation(self):
        await self.revoke_queued('speech', None, cancel=False)

    async def test_memory_rechecks_revocation_after_wait_without_request_cancellation(self):
        await self.revoke_queued('memory', None, cancel=False)

    async def assert_rejected_queued(self, method, path, store, mutation_name, **kwargs):
        self.headers = self.login('owner')
        self.user_lock.waiting.clear()
        await self.user_lock.acquire()
        with patch.object(store, mutation_name, wraps=getattr(store, mutation_name)) as mutation:
            request = asyncio.create_task(self.client.request(method, path, headers=self.headers, **kwargs))
            try:
                await asyncio.wait_for(self.user_lock.waiting.wait(), timeout=2)
                await run_blocking(self.app.state.admin_store.change_user, self.admin.id, self.member.id)
            finally:
                self.user_lock.release()
            response = await asyncio.wait_for(request, timeout=2)
            self.assertEqual(response.status_code, 401, response.text)
            mutation.assert_not_called()

    async def test_model_writes_and_model_test_recheck_after_user_lock(self):
        for method, path, store, mutation, kwargs in (
            ('PUT', '/api/account/model', self.app.state.provider_settings, 'save', {'json': {'api_key': 'sk-simulated-new'}}),
            ('DELETE', '/api/account/model', self.app.state.provider_settings, 'delete', {}),
            ('POST', '/api/account/model/test', self.app.state.providers, 'key', {}),
        ):
            with self.subTest(method=method, path=path):
                await self.assert_rejected_queued(method, path, store, mutation, **kwargs)

    async def test_device_mutations_recheck_after_user_lock(self):
        for path, mutation, payload in (
            ('/api/devices/pair', 'claim_pairing', {'code': 'ABCD-EFGH'}),
            ('/api/devices/borrow', 'claim_loan', {'code': 'ABCD-EFGH'}),
            ('/api/devices/cat-queued/loan', 'create_loan', {'confirm_device_id': 'cat-queued'}),
            ('/api/devices/cat-queued/loan/end', 'end_loan', {'confirm_device_id': 'cat-queued'}),
            ('/api/devices/cat-queued/unbind', 'unbind_device', {'confirm_device_id': 'cat-queued'}),
            ('/api/devices/cat-queued/revoke', 'revoke_device', {}),
        ):
            with self.subTest(path=path):
                await self.assert_rejected_queued('POST', path, self.ids, mutation, json=payload)

    async def test_retention_and_knowledge_mutations_recheck_after_user_lock(self):
        retention, knowledge = self.app.state.memory_retention, self.app.state.knowledge.store
        for method, path, store, mutation, kwargs in (
            ('PUT', '/api/account/memories/cat-queued/retention', retention, 'set_mode', {'json': {'mode': 'long_term'}}),
            ('POST', '/api/account/memories/cat-queued/restore', retention, 'restore', {}),
            ('POST', '/api/knowledge/documents', knowledge, 'add_document', {'content': b'fictional text'}),
            ('DELETE', '/api/knowledge/documents/fictional', knowledge, 'delete', {}),
            ('POST', '/api/knowledge/documents/fictional/reindex', knowledge, 'retry', {}),
            ('PUT', '/api/knowledge/documents/fictional/profile', knowledge, 'save_profile', {'json': {}}),
        ):
            with self.subTest(method=method, path=path):
                self.client.headers['X-File-Name'] = 'fictional.txt'
                await self.assert_rejected_queued(method, path, store, mutation, **kwargs)

    async def test_runtime_writes_recheck_session_after_device_lock(self):
        runtime = await self.app.state.devices.resolve('cat-queued', self.member.id)

        async def resolved(request: Request):
            await require_session_write(request)
            return runtime

        self.app.dependency_overrides[server_app.get_device_runtime] = resolved
        try:
            for method, suffix, payload in (
                ('DELETE', 'memory', {'confirm_device_id': 'cat-queued'}),
                ('POST', 'voice', {'paused': True}),
            ):
                with self.subTest(suffix=suffix):
                    self.headers = self.login('owner')
                    self.device_lock.waiting.clear()
                    await self.device_lock.acquire()
                    request = asyncio.create_task(self.client.request(
                        method, '/api/devices/cat-queued/' + suffix, headers=self.headers, json=payload))
                    try:
                        await asyncio.wait_for(self.device_lock.waiting.wait(), timeout=2)
                        # 普通退出也会撤销会话，锁后必须重新检查已解析的运行时授权。
                        token = self.headers['Cookie'].split('=', 1)[1]
                        await run_blocking(self.ids.revoke_session, token)
                    finally:
                        self.device_lock.release()
                    response = await asyncio.wait_for(request, timeout=2)
                    self.assertEqual(response.status_code, 401, response.text)
            self.assertFalse(self.ids.voice_preference(self.member.id, 'cat-queued'))
            self.assertTrue(self.store.context_for(self.member.id, 'cat-queued')['messages'])
        finally:
            self.app.dependency_overrides.clear()

    async def test_cancelled_device_queue_leaves_no_background_mutation(self):
        await self.device_lock.acquire()
        baseline = asyncio.all_tasks()
        queued = set()
        with patch.object(self.ids, 'unbind_device', wraps=self.ids.unbind_device) as mutation:
            request = asyncio.create_task(self.client.post('/api/devices/cat-queued/unbind', headers=self.headers,
                                                         json={'confirm_device_id': 'cat-queued'}))
            try:
                await asyncio.wait_for(self.device_lock.waiting.wait(), timeout=2)
                queued = asyncio.all_tasks() - baseline
                request.cancel()
                result = await asyncio.wait_for(asyncio.gather(request, return_exceptions=True), timeout=2)
                detached = [task for task in queued if not task.done()]
            finally:
                self.device_lock.release()
            await asyncio.wait_for(asyncio.gather(*queued, return_exceptions=True), timeout=2)
            self.assertIsInstance(result[0], asyncio.CancelledError)
            mutation.assert_not_called()
            self.assertEqual(detached, [])

    async def test_started_write_finishes_cleanup_before_admin_revocation(self):
        entered, finish = asyncio.Event(), asyncio.Event()

        async def retire(app, user_id):
            entered.set()
            await finish.wait()

        with patch('speech_routes.retire_user_models', side_effect=retire):
            request = asyncio.create_task(self.write('speech'))
            admin_request = None
            try:
                await asyncio.wait_for(entered.wait(), timeout=2)
                request.cancel()
                admin_request = asyncio.create_task(self.client.post(
                    f'/api/admin/users/{self.member.id}/revoke-sessions', headers=self.admin_headers,
                    json={'confirm_username': 'owner'}))
                await asyncio.wait_for(self.user_lock.waiting.wait(), timeout=2)
                self.assertFalse(request.done(), '已开始的写入在收尾前脱离了所属请求')
            finally:
                finish.set()
                result = await asyncio.gather(request, return_exceptions=True)
                if admin_request is not None:
                    response = await asyncio.wait_for(admin_request, timeout=2)
            self.assertIsInstance(result[0], asyncio.CancelledError)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.app.state.speech.settings.get(self.member.id)['mode'], 'accurate')
            self.assertEqual(self.app.state.account_operations.tasks, {})


if __name__ == '__main__':
    unittest.main()
