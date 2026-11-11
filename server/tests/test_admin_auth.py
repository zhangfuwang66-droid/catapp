"""管理登录事务、原会话保持、入口与退出回归，全部使用临时数据库和临时资源。"""

import asyncio
from contextlib import ExitStack
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import quote

from httpx import ASGITransport, AsyncClient

from support import server_app
from access_control import AccessSettings, SESSION_COOKIE
from async_helpers import run_blocking
import admin_pages
from identity_store import csrf_for_session
from memory_store import MemoryStore, SummaryResult


async def summarize(_messages):
    return SummaryResult(summary='模拟摘要')


class AdminAuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='admin-auth-')))
        self.assets = directory / 'assets'
        admin = self.assets / 'web' / 'admin'
        admin.mkdir(parents=True)
        for name, content in (('login.html', '<html>独立管理登录测试页</html>'),
                              ('login.css', '/* public login style */'),
                              ('login.js', '/* public login script */'),
                              ('index.html', 'protected-admin-marker'),
                              ('admin.js', 'protected-script-marker'),
                              ('admin.css', 'protected-style-marker')):
            (admin / name).write_text(content, encoding='utf-8')
        self.stack.enter_context(patch.object(admin_pages, 'SERVER_ROOT', self.assets))
        self.stack.enter_context(patch.object(server_app, 'WEB_ROOT', self.assets / 'web'))
        self.path = directory / 'memory.sqlite3'
        self.app = server_app.create_app(store=MemoryStore(self.path), summarizer=summarize,
                                         access=AccessSettings('accounts', 'https://cat.example'))
        self.ids = self.app.state.identities
        self.admins = self.app.state.admin_store
        self.admin = await run_blocking(self.ids.create_user, 'auth-admin', 'temporary-test-password')
        self.second = await run_blocking(self.ids.create_user, 'auth-second', 'temporary-test-password')
        self.member = await run_blocking(self.ids.create_user, 'auth-member', 'temporary-test-password')
        for user in (self.admin, self.second):
            await run_blocking(self.admins.set_role, user.username, True)
        self.client = AsyncClient(transport=ASGITransport(self.app, raise_app_exceptions=False),
                                   base_url='https://cat.example', follow_redirects=False)
        self.addAsyncCleanup(self.client.aclose)
        self.member_grant = await run_blocking(self.ids.login, self.member.username, 'temporary-test-password', client_key='fixture')
        self.client.cookies.set(SESSION_COOKIE, self.member_grant.token)
        for target, method in ((self.app.state.devices, 'get'), (self.app.state.devices, 'resolve'),
                               (self.app.state.providers, 'key')):
            self.stack.enter_context(patch.object(target, method, side_effect=AssertionError('登录不应创建运行时或读取 Key')))

    def session_rows(self):
        with self.ids._transaction() as db:
            return [tuple(row) for row in db.execute('SELECT token_hash,user_id,expires_at,revoked_at FROM auth_sessions ORDER BY token_hash')]

    async def login(self, **kwargs):
        return await self.client.post('/api/admin/auth/login',
                                      json=kwargs.pop('json', {'username': self.admin.username, 'password': 'temporary-test-password'}),
                                      headers=kwargs.pop('headers', {'Origin': 'https://cat.example'}), **kwargs)

    def assert_private(self, response):
        self.assertEqual(response.headers.get('cache-control'), 'no-store', response.text)
        for marker in ('temporary-test-password', 'password_hash', 'token_hash', 'private-failure-marker'):
            self.assertNotIn(marker, response.text)

    async def test_admin_login_rotates_cookie_and_returns_only_session_contract(self):
        with patch.object(self.app.state.connections, 'revoke_session', new=AsyncMock()) as revoke:
            response = await self.login(json={'username': ' ＡＵＴＨ-ADMIN ', 'password': 'temporary-test-password'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_private(response)
        self.assertEqual(set(response.json()), {'ok', 'user', 'csrf_token'})
        self.assertEqual(response.json()['user'], {'id': self.admin.id, 'username': self.admin.username})
        cookie = response.headers['set-cookie']
        for value in ('HttpOnly', 'Secure', 'SameSite=strict', 'Path=/', 'Max-Age=604800'):
            self.assertIn(value, cookie)
        token = response.cookies[SESSION_COOKIE]
        self.assertNotIn(token, response.text)
        self.assertEqual(response.json()['csrf_token'], csrf_for_session(token))
        self.assertIsNone(self.ids.session_user(self.member_grant.token))
        self.assertEqual(self.ids.session_user(token).id, self.admin.id)
        revoke.assert_awaited_once_with(self.member_grant.token)

    async def test_member_rejection_keeps_original_session_and_creates_no_new_session(self):
        before = self.session_rows()
        response = await self.login(json={'username': self.member.username, 'password': 'temporary-test-password'})
        self.assertEqual(response.status_code, 403, response.text)
        self.assert_private(response)
        self.assertNotIn('set-cookie', response.headers)
        self.assertEqual(self.session_rows(), before)
        self.assertEqual(self.ids.session_user(self.member_grant.token).id, self.member.id)

    async def test_http_login_cookie_omits_secure(self):
        self.app.state.access = AccessSettings('accounts', 'http://cat.example')
        async with AsyncClient(transport=ASGITransport(self.app), base_url='http://cat.example') as client:
            response = await client.post('/api/admin/auth/login', headers={'Origin': 'http://cat.example'},
                                        json={'username': self.admin.username, 'password': 'temporary-test-password'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_private(response)
        cookie = response.headers['set-cookie']
        self.assertNotIn('Secure', cookie)
        for value in ('HttpOnly', 'SameSite=strict', 'Path=/', 'Max-Age=604800'):
            self.assertIn(value, cookie)

    async def test_invalid_disabled_or_unknown_login_never_mutates_sessions(self):
        await run_blocking(self.admins.change_user, self.admin.id, self.second.id, True)
        before = self.session_rows()
        for username, password in ((self.admin.username, 'wrong-test-password'),
                                    (self.second.username, 'temporary-test-password'),
                                    ('missing-user', 'temporary-test-password')):
            with self.subTest(username=username):
                response = await self.login(json={'username': username, 'password': password})
                self.assertEqual(response.status_code, 401, response.text)
                self.assert_private(response)
                self.assertNotIn('set-cookie', response.headers)
                self.assertEqual(self.session_rows(), before)

    async def test_origin_and_validation_rejections_do_not_echo_password_or_issue_cookie(self):
        before = self.session_rows()
        for headers in ({}, {'Origin': 'https://untrusted.example'},
                        {'Origin': 'https://cat.example', 'Sec-Fetch-Site': 'cross-site'}):
            response = await self.login(headers=headers)
            self.assertEqual(response.status_code, 403, response.text)
            self.assert_private(response)
        for body in ({'username': self.admin.username, 'password': 'private-failure-marker' * 20},
                     {'username': self.admin.username, 'password': 'temporary-test-password', 'is_admin': True},
                     {'username': self.admin.username, 'password': ['private-failure-marker']},
                     {'username': 123, 'password': 'temporary-test-password'}):
            response = await self.login(json=body)
            self.assertEqual(response.status_code, 422, response.text)
            self.assert_private(response)
            self.assertNotIn('set-cookie', response.headers)
        self.assertEqual(self.session_rows(), before)

    async def test_page_and_api_reject_all_redirect_inputs(self):
        before = self.session_rows()
        for target in ('/admin', '//untrusted.example', 'https://untrusted.example/admin', r'\admin',
                       '/admin/../', '%2f%2funtrusted.example', '/%61dmin', '/admin%00'):
            query = 'next=' + quote(target, safe='')
            for url, method in (('/admin/login?', 'GET'), ('/api/admin/auth/login?', 'POST')):
                with self.subTest(target=target, method=method):
                    response = await self.client.request(method, url + query, headers={'Origin': 'https://cat.example'},
                               json={'username': self.admin.username, 'password': 'temporary-test-password'} if method == 'POST' else None)
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertNotIn('location', response.headers)
        for key in ('next', 'redirect', 'return_to'):
            response = await self.login(json={'username': self.admin.username, 'password': 'temporary-test-password', key: '/admin'})
            self.assertEqual(response.status_code, 422, response.text)
            response = await self.client.get('/admin/login', params={key: '/admin'})
            self.assertEqual(response.status_code, 422, response.text)
        for query in ('notice=expired&notice=logout', 'notice=untrusted', 'redirect=%2f%2funtrusted.example'):
            response = await self.client.get('/admin/login?' + query)
            self.assertEqual(response.status_code, 422)
        self.assertEqual(self.session_rows(), before)

    async def test_admin_page_redirects_expired_session_to_own_login_and_member_stays_forbidden(self):
        self.assertEqual((await self.client.get('/admin')).status_code, 403)
        self.client.cookies.clear()
        for token in (None, self.member_grant.token):
            if token:
                self.ids.revoke_session(token)
                self.client.cookies.set(SESSION_COOKIE, token)
            response = await self.client.get('/admin')
            self.assertEqual(response.status_code, 303, response.text)
            self.assertEqual(response.headers['location'], '/admin/login')
            self.assert_private(response)
        for notice in ('expired', 'login', 'logout'):
            response = await self.client.get('/admin/login', params={'notice': notice})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn('独立管理登录测试页', response.text)
            self.assertIn("frame-ancestors 'none'", response.headers['content-security-policy'])
        await self.login()
        response = await self.client.get('/admin/login')
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['location'], '/admin')

    async def test_only_exact_login_css_and_js_are_public(self):
        self.client.cookies.clear()
        for name, mime in (('login.css', 'text/css'), ('login.js', 'javascript')):
            response = await self.client.get('/web/admin/' + name)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn(mime, response.headers['content-type'])
            self.assertEqual(response.headers['cache-control'], 'no-store')
        for path in ('/web/ADMIN/login.css', '/web/admin%5clogin.js', '/web/%61dmin/login.css',
                     '/web/admin/%6cogin.js', '/web/admin/login.CSS', '/web/admin./login.css',
                     '/web/admin/login.js.', '/web/admin/login.js/extra'):
            with self.subTest(path=path):
                self.assertEqual((await self.client.get(path)).status_code, 404)
        for name in ('index.html', 'admin.js', 'admin.css', 'login.html'):
            response = await self.client.get('/web/admin/' + name)
            self.assertIn(response.status_code, (401, 403, 404))
            self.assertNotIn('protected-', response.text)
            self.assertNotIn('独立管理登录测试页', response.text)

    async def test_missing_login_page_returns_sanitized_unavailable(self):
        (self.assets / 'web' / 'admin' / 'login.html').unlink()
        response = await self.client.get('/admin/login')
        self.assertEqual(response.status_code, 503, response.text)
        self.assert_private(response)
        self.assertNotIn(str(self.assets), response.text)

    async def test_non_accounts_mode_hides_login_api_page_and_public_assets(self):
        self.app.state.access = AccessSettings('local')
        for method, path, body in (('GET', '/admin/login', None), ('GET', '/admin', None),
                                   ('GET', '/web/admin/login.css', None), ('GET', '/web/admin/login.js', None),
                                   ('POST', '/api/admin/auth/login', {'username': self.admin.username, 'password': 'temporary-test-password'}),
                                   ('POST', '/api/admin/auth/logout', None)):
            response = await self.client.request(method, path, json=body, headers={'Origin': 'https://cat.example'})
            self.assertEqual(response.status_code, 404, response.text)

    async def test_logout_requires_origin_and_csrf_but_allows_demoted_admin(self):
        response = await self.login()
        token = response.cookies[SESSION_COOKIE]
        self.client.cookies.clear()
        self.client.cookies.set(SESSION_COOKIE, token)
        csrf = response.json()['csrf_token']
        for headers in ({'Origin': 'https://cat.example'}, {'Origin': 'https://untrusted.example', 'X-CSRF-Token': csrf},
                        {'Origin': 'https://cat.example', 'X-CSRF-Token': 'wrong-token'}):
            denied = await self.client.post('/api/admin/auth/logout', headers=headers)
            self.assertEqual(denied.status_code, 403)
            self.assertIsNotNone(self.ids.session_user(token))
        await run_blocking(self.admins.set_role, self.admin.username, False)
        self.assertEqual((await self.client.get('/api/admin/session')).status_code, 403)
        with patch.object(self.app.state.connections, 'revoke_session', new=AsyncMock()) as revoke:
            result = await self.client.post('/api/admin/auth/logout', headers={'Origin': 'https://cat.example', 'X-CSRF-Token': csrf})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json(), {'ok': True})
        self.assertIn('Max-Age=0', result.headers['set-cookie'])
        self.assertIsNone(self.ids.session_user(token))
        revoke.assert_awaited_once_with(token)
        self.assertEqual((await self.client.get('/api/admin/session')).status_code, 401)

    async def test_ordinary_session_reports_current_admin_flag_without_client_override(self):
        response = await self.client.get('/api/auth/session?is_admin=true')
        self.assertIs(response.json()['is_admin'], False)
        for granted in (True, False):
            await run_blocking(self.admins.set_role, self.member.username, granted)
            response = await self.client.get('/api/auth/session')
            self.assertIs(response.json()['is_admin'], granted)
        response = await self.client.post('/api/auth/login', headers={'Origin': 'https://cat.example'},
                                          json={'username': self.admin.username, 'password': 'temporary-test-password'})
        self.assertIs(response.json()['is_admin'], True)
        self.assertEqual(response.status_code, 200, response.text)

    async def test_original_pages_offer_hidden_admin_entry_and_public_login_entry(self):
        response = await self.client.get('/')
        self.assertIn('href="/admin/login"', response.text)
        self.assertIn('id="adminEntry" href="/admin" hidden', response.text)
        javascript = (Path(__file__).resolve().parents[1] / 'web' / 'portal.js').read_text(encoding='utf-8')
        self.assertIn("grant.is_admin !== true", javascript)
        credential = await run_blocking(self.ids.register_device, 'cat-auth-entry', '临时设备')
        pairing = await run_blocking(self.ids.request_pairing, credential.device_id, credential.token)
        await run_blocking(self.ids.claim_pairing, pairing.code, self.member.id, client_key='temporary-pair')
        response = await self.client.get('/console/cat-auth-entry')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('id="consoleAdminEntry" href="/admin" hidden', response.text)

    async def test_both_login_endpoints_share_rate_limits(self):
        for _ in range(10):
            response = await self.client.post('/api/auth/login', headers={'Origin': 'https://cat.example'},
                        json={'username': self.admin.username, 'password': 'wrong-password'})
            self.assertEqual(response.status_code, 401)
        before = self.session_rows()
        response = await self.login()
        self.assertEqual(response.status_code, 429, response.text)
        self.assertGreaterEqual(int(response.headers['retry-after']), 1)
        self.assertNotIn('set-cookie', response.headers)
        self.assertEqual(self.session_rows(), before)

    async def test_rejection_or_storage_failure_does_not_revoke_original_cookie(self):
        before = self.session_rows()
        with patch.object(self.ids, 'login', side_effect=OSError('private-failure-marker')):
            response = await self.login()
        self.assertEqual(response.status_code, 503, response.text)
        self.assert_private(response)
        self.assertNotIn('set-cookie', response.headers)
        self.assertEqual(self.session_rows(), before)
        self.assertEqual(self.ids.session_user(self.member_grant.token).id, self.member.id)

    async def test_failed_post_login_cleanup_revokes_undelivered_new_session(self):
        with patch.object(self.app.state.connections, 'revoke_session',
                          new=AsyncMock(side_effect=RuntimeError('private-failure-marker'))):
            response = await self.login()
        self.assertEqual(response.status_code, 503, response.text)
        self.assert_private(response)
        self.assertNotIn('set-cookie', response.headers)
        with self.ids._transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM auth_sessions WHERE revoked_at IS NULL').fetchone()[0], 0)

    async def test_login_cleanup_storage_failure_stays_sanitized(self):
        with patch.object(self.ids, 'revoke_session', side_effect=OSError('private-failure-marker')) as revoke:
            response = await self.login()
        self.assertEqual(response.status_code, 503, response.text)
        self.assert_private(response)
        self.assertNotIn('set-cookie', response.headers)
        self.assertEqual(revoke.call_count, 2)
        self.assertIsNotNone(self.ids.session_user(self.member_grant.token))

    async def test_logout_failure_reports_unavailable_without_false_success(self):
        for stage in ('storage', 'connections'):
            grant = await self.login()
            token = grant.cookies[SESSION_COOKIE]
            self.client.cookies.clear()
            self.client.cookies.set(SESSION_COOKIE, token)
            headers = {'Origin': 'https://cat.example', 'X-CSRF-Token': grant.json()['csrf_token']}
            if stage == 'storage':
                failure = patch.object(self.ids, 'revoke_session', side_effect=OSError('private-failure-marker'))
            else:
                failure = patch.object(self.app.state.connections, 'revoke_session',
                                       new=AsyncMock(side_effect=RuntimeError('private-failure-marker')))
            with failure:
                response = await self.client.post('/api/admin/auth/logout', headers=headers)
            self.assertEqual(response.status_code, 503, response.text)
            self.assert_private(response)
            self.assertNotIn('set-cookie', response.headers)
            self.assertEqual(self.ids.session_user(token) is not None, stage == 'storage')

    async def test_role_disable_and_name_change_during_password_check_never_issue_session(self):
        for change in ('role', 'disabled', 'username'):
            if change != 'role':
                await run_blocking(self.admins.set_role, self.admin.username, True)
            before = self.session_rows()
            entered, release = threading.Event(), threading.Event()
            hasher = self.ids.hasher
            def verify(password_hash, password):
                entered.set()
                if not release.wait(timeout=5):
                    raise TimeoutError('password verification not released')
                return hasher.verify(password_hash, password)
            paused = SimpleNamespace(verify=verify, hash=hasher.hash, check_needs_rehash=hasher.check_needs_rehash)
            with patch.object(self.ids, 'hasher', paused):
                task = asyncio.create_task(self.login())
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                    if change == 'role':
                        await run_blocking(self.admins.set_role, self.admin.username, False)
                    elif change == 'disabled':
                        await run_blocking(self.admins.change_user, self.second.id, self.admin.id, True)
                    else:
                        with self.ids._transaction(write=True) as db:
                            db.execute('UPDATE auth_users SET username=? WHERE id=?', ('renamed-auth-admin', self.admin.id))
                finally:
                    release.set()
                response = await asyncio.wait_for(task, timeout=3)
            self.assertEqual(response.status_code, 403 if change == 'role' else 401, response.text)
            self.assertNotIn('set-cookie', response.headers)
            self.assertEqual(self.session_rows(), before)
            if change == 'disabled':
                await run_blocking(self.admins.change_user, self.second.id, self.admin.id, False)

    async def test_cancelled_success_during_cleanup_revokes_new_unreturned_session(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def cleanup(_token):
            entered.set()
            await release.wait()
        with patch.object(self.app.state.connections, 'revoke_session', new=cleanup):
            task = asyncio.create_task(self.login())
            try:
                await asyncio.wait_for(entered.wait(), timeout=3)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=3)
        with self.ids._transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM auth_sessions WHERE revoked_at IS NULL').fetchone()[0], 0)

    async def test_cancelled_logout_waits_for_connection_cleanup(self):
        grant = await self.login()
        token = grant.cookies[SESSION_COOKIE]
        self.client.cookies.clear()
        self.client.cookies.set(SESSION_COOKIE, token)
        entered, release = asyncio.Event(), asyncio.Event()
        async def cleanup(_token):
            entered.set()
            await release.wait()
        with patch.object(self.app.state.connections, 'revoke_session', new=cleanup):
            task = asyncio.create_task(self.client.post('/api/admin/auth/logout',
                                      headers={'Origin': 'https://cat.example', 'X-CSRF-Token': grant.json()['csrf_token']}))
            try:
                await asyncio.wait_for(entered.wait(), timeout=3)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=3)
        self.assertIsNone(self.ids.session_user(token))


if __name__ == '__main__':
    unittest.main()
