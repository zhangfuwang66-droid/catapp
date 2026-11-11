"""网络认证资源预算、限流规范化与中断收尾；只使用临时账号和数据库。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from argon2.exceptions import VerifyMismatchError

from httpx import ASGITransport, AsyncClient

from support import server_app
from access_control import AccessSettings, SESSION_COOKIE
from async_helpers import run_blocking
from identity_store import IdentityStore, RateLimited
from memory_store import MemoryStore, SummaryResult


PASSWORD = 'temporary-network-test-password'


class LoginStoreResourceSecurityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='auth-network-store-')
        self.addCleanup(directory.cleanup)
        self.now = 1800000000.0
        self.path = Path(directory.name) / 'identity.sqlite3'
        self.ids = IdentityStore(self.path, clock=lambda: self.now)
        self.user = self.ids.create_user('network-user', PASSWORD)

    def test_password_checks_reject_overload_without_starting_third_argon_job(self):
        entered, release = threading.Event(), threading.Event()
        guard = threading.Lock()
        active = 0
        peak = 0

        def verify(_hash, _password):
            nonlocal active, peak
            with guard:
                active += 1
                peak = max(peak, active)
                if active == 2:
                    entered.set()
            try:
                if not release.wait(3):
                    raise TimeoutError('verification not released')
            finally:
                with guard:
                    active -= 1
            return True

        hasher = SimpleNamespace(verify=verify, check_needs_rehash=lambda _: False)
        with patch.object(self.ids, 'hasher', hasher), ThreadPoolExecutor(max_workers=3) as executor:
            first = executor.submit(self.ids.login, self.user.username, PASSWORD, client_key='192.0.2.1')
            second = executor.submit(self.ids.login, self.user.username, PASSWORD, client_key='192.0.2.2')
            try:
                self.assertTrue(entered.wait(2))
                extra = executor.submit(self.ids.login, self.user.username, PASSWORD, client_key='192.0.2.3')
                with self.assertRaises(RateLimited):
                    extra.result(timeout=.5)
                self.assertEqual(peak, 2)
            finally:
                release.set()
            self.assertIsNotNone(first.result(timeout=2))
            self.assertIsNotNone(second.result(timeout=2))

    def test_equivalent_ip_literals_cannot_reset_login_budget(self):
        # IPv4 映射 IPv6 与 IPv6 压缩/展开形式仍表示同一来源。
        hasher = SimpleNamespace(verify=lambda *_: True, hash=self.ids.hasher.hash, check_needs_rehash=lambda _: False)
        with patch.object(self.ids, 'hasher', hasher):
            for first, alias in (('192.0.2.44', '::ffff:192.0.2.44'),
                                 ('2001:db8::44', '2001:0db8:0000:0000:0000:0000:0000:0044'),
                                 ('fe80::44%ethernet', 'fe80::44%7')):
                with self.subTest(first=first, alias=alias):
                    for index in range(30):
                        self.ids.login(f'unknown-{index}', PASSWORD, client_key=first)
                    with self.assertRaises(RateLimited):
                        self.ids.login('unknown-final', PASSWORD, client_key=alias)

    def test_normalized_account_limit_persists_across_ips_and_reopening(self):
        verify = Mock(side_effect=VerifyMismatchError('simulated mismatch'))
        with patch.object(self.ids, 'hasher', SimpleNamespace(verify=verify)):
            for index in range(10):
                self.assertIsNone(self.ids.login(self.user.username, PASSWORD, client_key=f'198.51.100.{index + 1}'))
        self.assertEqual(verify.call_count, 10)
        reopened = IdentityStore(self.path, clock=lambda: self.now)
        with patch.object(reopened, 'hasher', SimpleNamespace(verify=Mock(side_effect=AssertionError('rate limit must run before hash')))):
            with self.assertRaises(RateLimited):
                reopened.login(' ＮＥＴＷＯＲＫ-ＵＳＥＲ ', PASSWORD, client_key='203.0.113.1')
        self.now += 301
        self.assertIsNotNone(reopened.login(self.user.username, PASSWORD, client_key='203.0.113.1'))

    def test_invalid_credentials_are_bounded_before_password_work(self):
        verify = Mock(side_effect=AssertionError('invalid format must not reach Argon2'))
        hasher = SimpleNamespace(verify=verify, hash=Mock(side_effect=AssertionError('invalid format must not create dummy hash')))
        with patch.object(self.ids, 'hasher', hasher):
            for username in ('../', '\ud800', 'x' * 10000):
                self.assertIsNone(self.ids.login(username, PASSWORD, client_key='192.0.2.1'))
            for password in ('', 'x' * 10000, '\ud800'):
                self.assertIsNone(self.ids.login(self.user.username, password, client_key='192.0.2.1'))
        verify.assert_not_called()
        hasher.hash.assert_not_called()
        with self.ids._transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM auth_sessions').fetchone()[0], 0)

    def test_password_budget_is_released_on_verifier_failure(self):
        with patch.object(self.ids, 'hasher', SimpleNamespace(verify=Mock(side_effect=RuntimeError('simulated backend failure')))):
            for _ in range(3):
                with self.assertRaisesRegex(RuntimeError, 'simulated backend failure'):
                    self.ids.login(self.user.username, PASSWORD, client_key='192.0.2.1')
        self.assertIsNotNone(self.ids.login(self.user.username, PASSWORD, client_key='192.0.2.1'))

    def test_unknown_account_dummy_hash_does_not_lock_session_reads(self):
        grant = self.ids.login(self.user.username, PASSWORD, client_key='fixture')
        entered, release = threading.Event(), threading.Event()

        def create_dummy(_password):
            entered.set()
            if not release.wait(3):
                raise TimeoutError('dummy hash not released')
            return 'simulated-dummy-hash'

        hasher = SimpleNamespace(hash=create_dummy, verify=lambda *_: True)
        with patch.object(self.ids, 'hasher', hasher), ThreadPoolExecutor(max_workers=2) as executor:
            login = executor.submit(self.ids.login, 'unknown-user', PASSWORD, client_key='192.0.2.1')
            try:
                self.assertTrue(entered.wait(2))
                read = executor.submit(self.ids.session_user, grant.token)
                self.assertEqual(read.result(timeout=.5), self.user)
            finally:
                release.set()
            self.assertIsNone(login.result(timeout=2))


async def summarize(_messages):
    return SummaryResult(summary='network-security-test')


class LoginNetworkRoutesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='auth-network-http-')))
        self.app = server_app.create_app(store=MemoryStore(directory / 'identity.sqlite3'),
                                         summarizer=summarize,
                                         access=AccessSettings('accounts', 'https://network.example'))
        self.ids = self.app.state.identities
        self.user = await run_blocking(self.ids.create_user, 'network-admin', PASSWORD)
        await run_blocking(self.app.state.admin_store.set_role, self.user.username, True)
        self.client = AsyncClient(transport=ASGITransport(self.app, raise_app_exceptions=False),
                                  base_url='https://network.example')
        self.addAsyncCleanup(self.client.aclose)
        for target, method in ((self.app.state.devices, 'get'), (self.app.state.devices, 'resolve'),
                               (self.app.state.providers, 'key')):
            self.stack.enter_context(patch.object(target, method, side_effect=AssertionError('认证不得访问模型或运行时')))

    async def login(self, path='/api/auth/login', **kwargs):
        return await self.client.post(path, headers={'Origin': 'https://network.example'},
                                      json=kwargs.pop('json', {'username': self.user.username, 'password': PASSWORD}), **kwargs)

    async def test_cancelled_regular_login_does_not_leave_undelivered_session(self):
        entered, release = threading.Event(), threading.Event()
        original = self.ids.hasher

        def verify(password_hash, password):
            entered.set()
            if not release.wait(3):
                raise TimeoutError('verification not released')
            return original.verify(password_hash, password)

        hasher = SimpleNamespace(verify=verify, hash=original.hash, check_needs_rehash=original.check_needs_rehash)
        with patch.object(self.ids, 'hasher', hasher):
            task = asyncio.create_task(self.login())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
        with self.ids._transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM auth_sessions WHERE revoked_at IS NULL').fetchone()[0], 0)

    async def test_both_endpoints_reject_before_queuing_on_exhausted_executor(self):
        class RecordingExecutor(ThreadPoolExecutor):
            submissions = 0
            queued = threading.Event()

            def submit(self, *args, **kwargs):
                self.submissions += 1
                if self.submissions == 3:
                    self.queued.set()
                return super().submit(*args, **kwargs)

        loop = asyncio.get_running_loop()
        previous = loop._default_executor
        executor = RecordingExecutor(max_workers=1)
        release = threading.Event()
        loop.set_default_executor(executor)
        blocker = loop.run_in_executor(None, release.wait)
        tasks = []
        try:
            tasks = [asyncio.create_task(self.login(path)) for path in ('/api/auth/login', '/api/admin/auth/login')]
            for _ in range(200):
                if executor.queued.is_set():
                    break
                await asyncio.sleep(.005)
            self.assertTrue(executor.queued.is_set())
            extra = asyncio.create_task(self.login())
            tasks.append(extra)
            done, _ = await asyncio.wait({extra}, timeout=.3)
            self.assertIn(extra, done, 'overload must respond without waiting for executor capacity')
            response = extra.result()
            self.assertEqual(response.status_code, 429, response.text)
            self.assertGreaterEqual(int(response.headers['retry-after']), 1)
            self.assertNotIn('set-cookie', response.headers)
            self.assertEqual(executor.submissions, 3, 'rejected login must not enqueue additional work')
        finally:
            release.set()
            await asyncio.wait_for(asyncio.gather(blocker, *tasks, return_exceptions=True), 5)
            loop.set_default_executor(previous)
            executor.shutdown(wait=True)

    async def test_admission_is_held_until_cancelled_cleanup_finishes(self):
        old = await run_blocking(self.ids.login, self.user.username, PASSWORD, client_key='fixture')
        self.client.cookies.set(SESSION_COOKIE, old.token)
        entered, release = asyncio.Event(), asyncio.Event()
        count = 0

        async def cleanup(_token):
            nonlocal count
            count += 1
            if count == 2:
                entered.set()
            await release.wait()

        hasher = SimpleNamespace(verify=lambda *_: True, check_needs_rehash=lambda _: False)
        with patch.object(self.ids, 'hasher', hasher), patch.object(self.app.state.connections, 'revoke_session', new=cleanup):
            tasks = [asyncio.create_task(self.login(path)) for path in ('/api/auth/login', '/api/admin/auth/login')]
            try:
                await asyncio.wait_for(entered.wait(), 3)
                for task in tasks:
                    task.cancel()
                await asyncio.sleep(0)
                self.assertTrue(all(not task.done() for task in tasks))
                response = await asyncio.wait_for(self.login(), 1)
                self.assertEqual(response.status_code, 429, response.text)
            finally:
                release.set()
                results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 3)
            self.assertTrue(all(isinstance(result, asyncio.CancelledError) for result in results))
        with self.ids._transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM auth_sessions WHERE revoked_at IS NULL').fetchone()[0], 0)
        self.assertEqual((await self.login()).status_code, 200)

    async def test_regular_post_login_failure_revokes_undelivered_session_and_releases_budget(self):
        old = await run_blocking(self.ids.login, self.user.username, PASSWORD, client_key='fixture')
        self.client.cookies.set(SESSION_COOKIE, old.token)
        with patch.object(self.app.state.admin_store, 'is_admin', side_effect=OSError('private-auth-storage-marker')):
            for _ in range(3):
                response = await self.login()
                self.assertEqual(response.status_code, 503, response.text)
                self.assertNotIn('private-auth-storage-marker', response.text)
                self.assertNotIn('set-cookie', response.headers)
                self.assertEqual(response.headers['cache-control'], 'no-store')
        with self.ids._transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM auth_sessions WHERE revoked_at IS NULL').fetchone()[0], 1)
        self.assertIsNotNone(self.ids.session_user(old.token))
        self.assertEqual((await self.login()).status_code, 200)

    async def test_unpaired_surrogate_password_is_rejected_without_hash_or_session(self):
        verify = Mock(side_effect=AssertionError('invalid UTF-8 must not reach password backend'))
        with patch.object(self.ids, 'hasher', SimpleNamespace(verify=verify)):
            for path in ('/api/auth/login', '/api/admin/auth/login'):
                response = await self.client.post(path, content=b'{"username":"network-admin","password":"\\ud800"}',
                    headers={'Origin': 'https://network.example', 'Content-Type': 'application/json'})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertNotIn('set-cookie', response.headers)
                self.assertEqual(response.headers['cache-control'], 'no-store')
        verify.assert_not_called()

    async def test_forged_forwarding_headers_and_endpoint_switching_do_not_reset_ip_budget(self):
        verify = Mock(return_value=True)
        hasher = SimpleNamespace(verify=verify, hash=self.ids.hasher.hash)
        with patch.object(self.ids, 'hasher', hasher):
            for index in range(31):
                path = '/api/auth/login' if index % 2 else '/api/admin/auth/login'
                response = await self.client.post(path,
                    json={'username': f'network-missing-{index}', 'password': PASSWORD},
                    headers={'Origin': 'https://network.example', 'X-Forwarded-For': f'198.51.100.{index + 1}',
                             'X-Real-IP': f'203.0.113.{index + 1}', 'Forwarded': f'for=192.0.2.{index + 1}'})
                self.assertEqual(response.status_code, 401 if index < 30 else 429, response.text)
            self.assertEqual(verify.call_count, 30)
            self.assertIn('retry-after', response.headers)


if __name__ == '__main__':
    unittest.main()
