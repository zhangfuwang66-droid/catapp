"""临时库账号 CRUD 回归，不启动后台任务、不读取真实 Key 或连接模型。"""

import asyncio
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import uuid
import zlib

from httpx import ASGITransport, AsyncClient

from access_control import AccessSettings, SESSION_COOKIE
from admin_accounts_store import AdminAccountsStore
from async_helpers import run_blocking
from device_registry import DeviceLock
from identity_store import csrf_for_session
from memory_store import MemoryStore, SummaryResult
from support import server_app


async def summarize(_messages):
    return SummaryResult(summary='模拟摘要')


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


class AdminAccountTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='admin-accounts-')
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'memory.sqlite3'
        self.app = await run_blocking(server_app.create_app, store=MemoryStore(self.path),
                                      summarizer=summarize, access=AccessSettings('accounts', 'https://cat.example'))
        # 使用统一应用的实际注册，禁止在测试中补挂路由掩盖集成遗漏。
        self.ids = self.app.state.identities
        self.admins = self.app.state.admin_store
        self.admin = await self.new_user('account-admin')
        self.member = await self.new_user('account-member')
        self.other = await self.new_user('account-other')
        await run_blocking(self.admins.set_role, self.admin.username, True)
        self.headers = await self.login(self.admin)
        self.member_headers = await self.login(self.member)
        self.client = AsyncClient(transport=ASGITransport(self.app, raise_app_exceptions=False), base_url='https://cat.example')
        self.addAsyncCleanup(self.client.aclose)
        guard = patch.object(self.app.state.providers, 'key', side_effect=AssertionError('账号管理不应读取 Key'))
        guard.start()
        self.addCleanup(guard.stop)
        for method in ('get', 'resolve'):
            guard = patch.object(self.app.state.devices, method, side_effect=AssertionError('账号管理不应创建运行时'))
            guard.start()
            self.addCleanup(guard.stop)

    async def new_user(self, username):
        return await run_blocking(self.ids.create_user, username, 'temporary-test-password')

    async def login(self, user):
        grant = await run_blocking(self.ids.login, user.username, 'temporary-test-password', client_key=uuid.uuid4().hex)
        self.assertIsNotNone(grant)
        return {'Cookie': f'{SESSION_COOKIE}={grant.token}', 'Origin': 'https://cat.example',
                'X-CSRF-Token': csrf_for_session(grant.token)}

    @staticmethod
    def token(headers):
        return headers['Cookie'].split('=', 1)[1]

    async def request(self, method, suffix='', body=None, headers=None):
        response = await self.client.request(method, '/api/admin/users' + suffix,
                                             json=body, headers=self.headers if headers is None else headers)
        self.assertEqual(response.headers.get('cache-control'), 'no-store', response.text)
        return response

    async def detail(self, user=None):
        response = await self.request('GET', '/' + (user or self.member).id)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def disable(self, user=None):
        await run_blocking(self.admins.change_user, self.admin.id, (user or self.member).id, True)

    async def register(self, device_id, owner=None):
        credential = await run_blocking(self.ids.register_device, device_id, '模拟设备')
        pairing = await run_blocking(self.ids.request_pairing, device_id, credential.token)
        await run_blocking(self.ids.claim_pairing, pairing.code, (owner or self.member).id, client_key=device_id)
        return credential

    async def test_integrated_application_exposes_all_account_operations(self):
        paths = self.app.openapi()['paths']
        self.assertIn('post', paths['/api/admin/users'])
        for method in ('get', 'patch', 'delete'):
            self.assertIn(method, paths['/api/admin/users/{user_id}'])

    async def test_create_normalizes_username_without_granting_role_or_session(self):
        password = 'temporary-new-password'
        response = await self.request('POST', body={'username': ' Ｎｅｗ.User ', 'password': password, 'confirm_username': 'new.user'})
        self.assertEqual(response.status_code, 201, response.text)
        user = response.json()['user']
        self.assertEqual(set(user), {'id', 'username', 'created_at', 'disabled', 'is_admin', 'key_configured'})
        self.assertEqual(user['username'], 'new.user')
        for field in ('disabled', 'is_admin', 'key_configured'):
            self.assertIs(user[field], False)
        with self.ids._transaction() as db:
            row = db.execute('SELECT password_hash FROM auth_users WHERE id=?', (user['id'],)).fetchone()
            self.assertNotEqual(row[0], password)
            self.assertTrue(await run_blocking(self.ids.hasher.verify, row[0], password))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM auth_sessions WHERE user_id=?', (user['id'],)).fetchone()[0], 0)
            audit = dict(db.execute("SELECT * FROM admin_audit WHERE action='create-user' ORDER BY id DESC LIMIT 1").fetchone())
        self.assertEqual((audit['result'], audit['target_id']), ('success', user['id']))
        self.assertNotIn(password, str(audit) + response.text)

    async def test_create_rejects_duplicate_confirmation_and_invalid_inputs_privately(self):
        cases = (
            ({'username': self.member.username, 'password': 'valid-secret-password', 'confirm_username': self.member.username}, 409),
            ({'username': 'fresh-user', 'password': 'valid-secret-password', 'confirm_username': 'wrong'}, 409),
            ({'username': '!', 'password': 'valid-secret-password', 'confirm_username': '!'}, 422),
            ({'username': 'fresh-user', 'password': 'short-key', 'confirm_username': 'fresh-user'}, 422),
            ({'username': 'fresh-user', 'password': ' ' * 12, 'confirm_username': 'fresh-user'}, 422),
            ({'username': 'fresh-user', 'password': 'valid-secret-password', 'confirm_username': 'fresh-user', 'is_admin': True}, 422),
            ({'username': 1234, 'password': 'valid-secret-password', 'confirm_username': '1234'}, 422),
        )
        for payload, expected in cases:
            with self.subTest(payload_keys=tuple(payload), expected=expected):
                result = await self.request('POST', body=payload)
                self.assertEqual(result.status_code, expected, result.text)
                self.assertNotIn(payload['password'], result.text)
        self.assertEqual(len(self.ids.list_users()), 3)

    async def test_all_routes_reject_anonymous_members_and_device_credentials(self):
        routes = (('POST', '', {'username': 'fresh-user', 'password': 'temporary-test-password', 'confirm_username': 'fresh-user'}),
                  ('GET', '/' + self.member.id, None),
                  ('PATCH', '/' + self.member.id, {'username': 'renamed-user', 'confirm_username': self.member.username}),
                  ('DELETE', '/' + self.member.id, {'confirm_username': self.member.username}))
        for headers, code in (({}, 401), (self.member_headers, 403), ({'Authorization': 'Bearer ' + 'x' * 40}, 401)):
            for method, path, body in routes:
                with self.subTest(method=method, expected=code):
                    response = await self.request(method, path, body, headers=headers)
                    self.assertEqual(response.status_code, code, response.text)
        self.assertEqual(len(self.ids.list_users()), 3)

    async def test_all_writes_require_same_origin_and_csrf(self):
        for method, suffix, body in (
            ('POST', '', {'username': 'fresh-user', 'password': 'temporary-test-password', 'confirm_username': 'fresh-user'}),
            ('PATCH', '/' + self.member.id, {'username': 'renamed-user', 'confirm_username': self.member.username}),
            ('DELETE', '/' + self.member.id, {'confirm_username': self.member.username}),
        ):
            for headers in ({**self.headers, 'Origin': 'https://untrusted.example'},
                            {key: value for key, value in self.headers.items() if key != 'X-CSRF-Token'}):
                with self.subTest(method=method, headers=tuple(headers)):
                    response = await self.request(method, suffix, body, headers=headers)
                    self.assertEqual(response.status_code, 403, response.text)

    async def test_detail_is_whitelisted_and_scoped_to_target_with_device_loans(self):
        await self.register('cat-owned')
        await self.register('deer-borrowed', self.other)
        await self.register('other-expired', self.other)
        await self.register('deer-revoked')
        await run_blocking(self.ids.revoke_device, 'deer-revoked')
        loan = await run_blocking(self.ids.create_loan, 'deer-borrowed', self.other.id, 1)
        await run_blocking(self.ids.claim_loan, loan['code'], self.member.id, client_key='temporary-loan')
        expired = await run_blocking(self.ids.create_loan, 'other-expired', self.other.id, 1)
        await run_blocking(self.ids.claim_loan, expired['code'], self.member.id, client_key='expired-loan')
        memory = self.app.state.memory_manager.store
        conversation = await run_blocking(memory.create_conversation, self.member.id, 'cat-owned')
        message = await run_blocking(memory.append_message, conversation, 'user', '私密消息🙂')
        await run_blocking(memory.save_summary, conversation, message, '私密摘要', ['私密事实'])
        other_conversation = await run_blocking(memory.create_conversation, self.other.id, 'deer-borrowed')
        await run_blocking(memory.append_message, other_conversation, 'user', '另一个用户' * 40)
        document = '隔离资料原文'.encode('utf-8')
        await run_blocking(self.app.state.knowledge.store.add_document, self.member.id, 'private-name.txt', document)
        await run_blocking(self.app.state.knowledge.store.add_document, self.other.id, 'other.txt', b'other')
        archive = zlib.compress(b'private-archive-marker')
        now = self.ids.clock()
        with self.ids._transaction(write=True) as db:
            db.execute('UPDATE device_loans SET expires_at=? WHERE device_id=?', (now - 1, 'other-expired'))
            db.execute('INSERT INTO memory_message_archives(conversation_id,payload,raw_bytes,message_count,sha256,created_at) VALUES(?,?,?,?,?,?)',
                       (conversation, archive, 22, 3, 'temporary-archive-digest', now))
            db.execute('INSERT INTO account_provider_keys VALUES(?,?,?,?,?)', (self.member.id, b'private-ciphertext', 'private-masked', 'revision', now))
            db.execute('INSERT INTO auth_sessions VALUES(?,?,?,?,?)', ('expired-token-marker', self.member.id, now - 200, now - 1, None))
            db.execute('INSERT INTO auth_sessions VALUES(?,?,?,?,?)', ('revoked-token-marker', self.member.id, now - 200, now + 100, now))
        response = await self.request('GET', '/' + self.member.id)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(set(data), {'user', 'active_sessions', 'devices', 'documents', 'memory', 'deletion'})
        self.assertEqual(data['active_sessions'], 1)
        self.assertIs(data['user']['key_configured'], True)
        self.assertEqual({d['id'] for d in data['devices']['owned']}, {'cat-owned', 'deer-revoked'})
        self.assertEqual([d['id'] for d in data['devices']['borrowed']], ['deer-borrowed'])
        self.assertEqual(data['devices']['borrowed'][0]['device_type'], 'deer')
        self.assertEqual(data['devices']['history_count'], 4)
        self.assertEqual(data['documents'], {'count': 1, 'bytes': len(document), 'statuses': {'queued': 1}, 'chunks': 0})
        self.assertEqual(data['memory'], {'conversations': 1, 'messages': 1, 'summaries': 1, 'memories': 1,
                         'content_bytes': len('私密消息🙂私密摘要私密事实'.encode('utf-8')),
                         'archives': 1, 'archive_bytes': len(archive), 'archived_messages': 3, 'policies': 0})
        self.assertIn('memory', data['deletion']['blockers'])
        self.assertEqual(self.app.state.devices.devices, {})
        for private in ('private-name', '私密消息', '私密摘要', '私密事实', '隔离资料原文', 'private-ciphertext', 'private-masked',
                        'password_hash', 'token_hash', 'invitation_hash', 'ciphertext', loan['code']):
            self.assertNotIn(private, response.text)

    async def test_detail_for_disabled_empty_account_allows_deletion(self):
        await self.disable()
        data = await self.detail()
        self.assertEqual(data['active_sessions'], 0)
        self.assertEqual(data['deletion'], {'allowed': True, 'blockers': []})
        self.assertEqual(data['devices'], {'owned': [], 'borrowed': [], 'history_count': 0})
        self.assertTrue(all(value == 0 for value in data['memory'].values()))

    async def test_read_missing_and_database_failure_are_private(self):
        self.assertEqual((await self.request('GET', '/usr_missing')).status_code, 404)
        with patch.object(AdminAccountsStore, 'detail', side_effect=sqlite3.OperationalError('private-database-path')):
            response = await self.request('GET', '/' + self.member.id)
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn('稍后重试', response.json()['detail'])
        self.assertNotIn('private-database-path', response.text)

    async def test_rename_keeps_identity_data_and_password_but_revokes_sessions_and_runtime(self):
        await self.register('cat-rename')
        memory = self.app.state.memory_manager.store
        conversation = await run_blocking(memory.create_conversation, self.member.id, 'cat-rename')
        await run_blocking(memory.append_message, conversation, 'user', '保留原文')
        runtime = SimpleNamespace(device_id='cat-rename', user_id=self.member.id, close=AsyncMock())
        self.app.state.devices.devices['cat-rename'] = runtime
        with patch.object(self.app.state.account_operations, 'cancel_user', new=AsyncMock()) as requests, \
                patch.object(self.app.state.connections, 'revoke_user', new=AsyncMock()) as connections, \
                patch.object(self.app.state.memory_manager, 'cancel_user_tasks', new=AsyncMock()) as memories, \
                patch.object(self.app.state.knowledge, 'cancel_user_tasks', new=AsyncMock()) as documents:
            response = await self.request('PATCH', '/' + self.member.id, {'username': 'Renamed.Account', 'confirm_username': self.member.username})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['user']['id'], self.member.id)
        self.assertEqual(response.json()['user']['username'], 'renamed.account')
        self.assertIs(response.json()['sessions_revoked'], True)
        for cleanup in (requests, connections, memories, documents):
            cleanup.assert_awaited_once_with(self.member.id)
        runtime.close.assert_awaited_once()
        self.assertNotIn('cat-rename', self.app.state.devices.devices)
        self.assertIsNone(await run_blocking(self.ids.session_user, self.token(self.member_headers)))
        self.assertIsNotNone(await self.login(SimpleNamespace(username='renamed.account')))
        self.assertEqual((await self.detail())['memory']['messages'], 1)
        self.assertEqual(self.ids.list_devices()[0]['owner_user_id'], self.member.id)

    async def test_rename_rejects_self_duplicate_stale_confirmation_and_missing_target(self):
        cases = ((self.admin.id, {'username': 'new-admin', 'confirm_username': self.admin.username}, 409),
                 (self.member.id, {'username': self.other.username, 'confirm_username': self.member.username}, 409),
                 (self.member.id, {'username': 'renamed-user', 'confirm_username': 'stale-name'}, 409),
                 ('usr_missing', {'username': 'renamed-user', 'confirm_username': self.member.username}, 404))
        for user_id, body, code in cases:
            with self.subTest(user_id=user_id, expected=code):
                response = await self.request('PATCH', '/' + user_id, body)
                self.assertEqual(response.status_code, code, response.text)
        self.assertEqual(self.admins.user(self.member.id)['username'], self.member.username)
        self.assertIsNotNone(self.ids.session_user(self.token(self.member_headers)))

    async def test_rename_cleanup_failure_preserves_started_and_retry_uses_current_name(self):
        body = {'username': 'renamed-retry', 'confirm_username': self.member.username}
        with patch.object(self.app.state.account_operations, 'cancel_user', new=AsyncMock(side_effect=RuntimeError('private-cleanup-marker'))):
            response = await self.request('PATCH', '/' + self.member.id, body)
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn('已生效', response.json()['detail'])
        self.assertNotIn('private-cleanup-marker', response.text)
        self.assertEqual(self.admins.user(self.member.id)['username'], 'renamed-retry')
        self.assertIsNone(self.ids.session_user(self.token(self.member_headers)))
        self.assertEqual((await self.request('PATCH', '/' + self.member.id, body)).status_code, 409)
        response = await self.request('PATCH', '/' + self.member.id, {'username': 'renamed-retry', 'confirm_username': 'renamed-retry'})
        self.assertEqual(response.status_code, 200, response.text)
        with self.ids._transaction() as db:
            results = [row[0] for row in db.execute("SELECT result FROM admin_audit WHERE action='rename-user' ORDER BY id")]
        self.assertEqual(results, ['started', 'failed', 'success'])

    async def test_delete_disabled_empty_account_keeps_existing_audit(self):
        await self.disable()
        with self.ids._transaction(write=True) as db:
            db.execute('INSERT INTO admin_roles VALUES(?,?)', (self.member.id, self.ids.clock()))
            db.execute("INSERT INTO admin_audit(actor_id,actor_username,target_type,target_id,action,result,created_at) VALUES(?,?,'user',?,'historical-action','success',?)",
                       (self.member.id, self.member.username, self.member.id, self.ids.clock()))
        response = await self.request('DELETE', '/' + self.member.id, {'confirm_username': self.member.username})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {'ok': True, 'deleted_user_id': self.member.id})
        self.assertIsNone(self.admins.user(self.member.id))
        with self.ids._transaction() as db:
            for table in ('auth_sessions', 'admin_roles'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table + ' WHERE user_id=?', (self.member.id,)).fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM admin_audit WHERE action='historical-action'").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT result FROM admin_audit WHERE action='delete-user'").fetchone()[0], 'success')
        self.assertEqual((await self.request('GET', '/' + self.member.id)).status_code, 404)

    async def test_delete_rejects_self_enabled_missing_and_wrong_confirmation(self):
        for user_id, confirm, expected in ((self.admin.id, self.admin.username, 409),
                                           (self.member.id, self.member.username, 409),
                                           ('usr_missing', 'missing', 404),
                                           (self.member.id, 'wrong-confirmation', 409)):
            with self.subTest(user_id=user_id, expected=expected):
                response = await self.request('DELETE', '/' + user_id, {'confirm_username': confirm})
                self.assertEqual(response.status_code, expected, response.text)
        self.assertIn('last_admin', (await self.detail(self.admin))['deletion']['blockers'])
        self.assertTrue(self.admins.is_admin(self.admin.id))

    async def test_delete_blocks_each_private_data_or_historical_reference_without_erasing_it(self):
        credential = await run_blocking(self.ids.register_device, 'cat-associations', '模拟关联设备')
        now = self.ids.clock()
        checks = (
            ('device_ownership', "UPDATE devices SET owner_user_id=? WHERE id='cat-associations'", lambda uid: (uid,)),
            ('device_history', 'INSERT INTO device_memberships VALUES(?,?,?,?,?,?)', lambda uid: (uuid.uuid4().hex, credential.device_id, uid, 'guest', now-100, now-50)),
            ('device_history', 'INSERT INTO device_pairings VALUES(?,?,?,?,?,?,?)', lambda uid: (uuid.uuid4().hex, credential.device_id, now-100, now-50, uid, now-90, now-40)),
            ('device_loans', 'INSERT INTO device_loans VALUES(?,?,?,?,?,?,?,?,?)', lambda uid: (uuid.uuid4().hex, credential.device_id, uid, None, uuid.uuid4().hex, now-100, now-90, now-80, now-70)),
            ('device_loans', 'INSERT INTO device_loans VALUES(?,?,?,?,?,?,?,?,?)', lambda uid: (uuid.uuid4().hex, credential.device_id, self.other.id, uid, uuid.uuid4().hex, now-100, now-90, now-80, now-70)),
            ('device_preferences', 'INSERT INTO device_preferences VALUES(?,?,?)', lambda uid: (uid, credential.device_id, 0)),
            ('documents', 'INSERT INTO knowledge_documents(id,user_id,filename,size_bytes,sha256,original,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)', lambda uid: (uuid.uuid4().hex, uid, 'private.txt', 7, uuid.uuid4().hex, b'private', 'failed', now, now)),
            ('memory', 'INSERT INTO conversations(user_id,device_id,started_at,last_activity_at) VALUES(?,?,?,?)', lambda uid: (uid, credential.device_id, '2000-01-01', '2000-01-01')),
            ('memory', 'INSERT INTO memories(user_id,device_id,content,created_at,updated_at,active) VALUES(?,?,?,?,?,?)', lambda uid: (uid, credential.device_id, 'private-fact', '2000-01-01', '2000-01-01', 0)),
            ('memory_policies', 'INSERT INTO memory_policies VALUES(?,?,?,?,?)', lambda uid: (uid, credential.device_id, 'long_term', now, None)),
            ('key_configured', 'INSERT INTO account_provider_keys VALUES(?,?,?,?,?)', lambda uid: (uid, b'private-ciphertext', 'private-masked', 'revision', now)),
            ('speech_settings', 'INSERT INTO account_speech_settings VALUES(?,?,?)', lambda uid: (uid, '{"hotwords":["private"]}', now)),
        )
        for index, (blocker, query, arguments) in enumerate(checks):
            with self.subTest(blocker=blocker, index=index):
                target = await self.new_user('blocked-' + str(index))
                await self.disable(target)
                with self.ids._transaction(write=True) as db:
                    db.execute(query, arguments(target.id))
                self.assertIn(blocker, (await self.detail(target))['deletion']['blockers'])
                response = await self.request('DELETE', '/' + target.id, {'confirm_username': target.username})
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn('先处理', response.json()['detail'])
                self.assertIsNotNone(self.admins.user(target.id))
                self.assertIn(blocker, (await self.detail(target))['deletion']['blockers'])
                if blocker == 'device_ownership':
                    with self.ids._transaction(write=True) as db:
                        db.execute('UPDATE devices SET owner_user_id=NULL WHERE id=?', (credential.device_id,))

    async def test_delete_rechecks_data_inserted_during_cleanup(self):
        await self.disable()
        async def add_private_data(_):
            await run_blocking(self.app.state.memory_manager.store.create_conversation, self.member.id, 'cat-concurrent')
        with patch.object(self.app.state.knowledge, 'cancel_user_tasks', new=add_private_data):
            response = await self.request('DELETE', '/' + self.member.id, {'confirm_username': self.member.username})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIsNotNone(self.admins.user(self.member.id))
        self.assertEqual((await self.detail())['memory']['conversations'], 1)

    async def test_delete_cleanup_failure_keeps_account_and_can_retry(self):
        await self.disable()
        with patch.object(self.app.state.connections, 'revoke_user', new=AsyncMock(side_effect=RuntimeError('private-cleanup-marker'))):
            response = await self.request('DELETE', '/' + self.member.id, {'confirm_username': self.member.username})
        self.assertEqual(response.status_code, 503)
        self.assertIsNotNone(self.admins.user(self.member.id))
        self.assertNotIn('private-cleanup-marker', response.text)
        response = await self.request('DELETE', '/' + self.member.id, {'confirm_username': self.member.username})
        self.assertEqual(response.status_code, 200, response.text)

    async def test_audit_start_and_finish_failures_report_truthful_outcome(self):
        body = {'username': 'audit-new-user', 'password': 'temporary-test-password', 'confirm_username': 'audit-new-user'}
        with patch.object(self.admins, 'begin_action', side_effect=sqlite3.OperationalError('private-audit-marker')):
            response = await self.request('POST', body=body)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(self.ids.list_users()), 3)
        with patch.object(self.admins, 'finish_action', side_effect=sqlite3.OperationalError('private-audit-marker')):
            response = await self.request('POST', body=body)
        self.assertEqual(response.status_code, 503)
        self.assertIn('已生效', response.json()['detail'])
        self.assertNotIn('private-audit-marker', response.text)
        self.assertEqual(len(self.ids.list_users()), 4)

    async def test_target_rename_while_queued_rejects_stale_confirmation(self):
        target_lock = ObservedLock()
        self.app.state.model_locks[self.member.id] = target_lock
        await target_lock.acquire()
        request = asyncio.create_task(self.request('PATCH', '/' + self.member.id, {'username': 'desired-name', 'confirm_username': self.member.username}))
        try:
            await asyncio.wait_for(target_lock.waiting.wait(), timeout=2)
            with self.ids._transaction(write=True) as db:
                db.execute('UPDATE auth_users SET username=? WHERE id=?', ('changed-name', self.member.id))
        finally:
            target_lock.release()
        response = await asyncio.wait_for(request, timeout=2)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.admins.user(self.member.id)['username'], 'changed-name')

    async def test_revoked_or_demoted_writer_cannot_run_queued_account_writes(self):
        await run_blocking(self.admins.set_role, self.other.username, True)
        await self.disable()
        for kind in ('create', 'rename', 'delete'):
            for revoke in ('session', 'role'):
                headers = await self.login(self.admin)
                lock = ObservedLock()
                if kind == 'create':
                    self.app.state.admin_mutation_lock = lock
                    method, suffix = 'POST', ''
                    body = {'username': 'blocked-create', 'password': 'temporary-test-password', 'confirm_username': 'blocked-create'}
                else:
                    self.app.state.model_locks[self.member.id] = lock
                    method, suffix = ('PATCH' if kind == 'rename' else 'DELETE'), '/' + self.member.id
                    body = {'confirm_username': self.member.username}
                    if kind == 'rename':
                        body['username'] = 'blocked-rename'
                await lock.acquire()
                task = asyncio.create_task(self.request(method, suffix, body, headers=headers))
                try:
                    await asyncio.wait_for(lock.waiting.wait(), timeout=2)
                    if revoke == 'session':
                        await run_blocking(self.ids.revoke_session, self.token(headers))
                    else:
                        await run_blocking(self.admins.set_role, self.admin.username, False)
                finally:
                    lock.release()
                response = await asyncio.wait_for(task, timeout=2)
                with self.subTest(kind=kind, revoke=revoke):
                    self.assertEqual(response.status_code, 401 if revoke == 'session' else 403, response.text)
                    self.assertEqual(self.admins.user(self.member.id)['username'], self.member.username)
                    self.assertEqual(len(self.ids.list_users()), 3)
                if revoke == 'role':
                    await run_blocking(self.admins.set_role, self.admin.username, True)

    async def test_transaction_rechecks_writer_after_audit_started(self):
        methods = (('POST', '', {'username': 'blocked-transaction', 'password': 'temporary-test-password', 'confirm_username': 'blocked-transaction'}, 'create'),
                   ('PATCH', '/' + self.member.id, {'username': 'blocked-transaction', 'confirm_username': self.member.username}, 'rename'),
                   ('DELETE', '/' + self.member.id, {'confirm_username': self.member.username}, 'delete'))
        await self.disable()
        await run_blocking(self.admins.set_role, self.other.username, True)
        for method, suffix, body, store_method in methods:
            for revoke in ('session', 'role'):
                headers = await self.login(self.admin)
                original = getattr(AdminAccountsStore, store_method)
                def revoke_then_write(store, *args):
                    if revoke == 'session':
                        self.ids.revoke_session(self.token(headers))
                    else:
                        self.admins.set_role(self.admin.username, False)
                    return original(store, *args)
                with patch.object(AdminAccountsStore, store_method, revoke_then_write):
                    response = await self.request(method, suffix, body, headers=headers)
                with self.subTest(method=method, revoke=revoke):
                    self.assertEqual(response.status_code, 401 if revoke == 'session' else 403, response.text)
                    self.assertEqual(len(self.ids.list_users()), 3)
                    self.assertEqual(self.admins.user(self.member.id)['username'], self.member.username)
                if revoke == 'role':
                    await run_blocking(self.admins.set_role, self.admin.username, True)

    async def test_rename_cancels_actual_queued_old_request_without_deleting_private_data(self):
        device_id = 'cat-old-request'
        await self.register(device_id)
        memory = self.app.state.memory_manager.store
        conversation = await run_blocking(memory.create_conversation, self.member.id, device_id)
        await run_blocking(memory.append_message, conversation, 'user', '必须保留的正文')
        device_lock = ObservedDeviceLock()
        self.app.state.devices._locks[device_id] = device_lock
        await device_lock.acquire()
        task = asyncio.create_task(self.client.request('DELETE', '/api/account/memories/' + device_id,
                                   headers=self.member_headers, json={'confirm_device_id': device_id}))
        try:
            await asyncio.wait_for(device_lock.waiting.wait(), timeout=2)
            response = await self.request('PATCH', '/' + self.member.id,
                                          {'username': 'renamed-old-request', 'confirm_username': self.member.username})
            self.assertEqual(response.status_code, 200, response.text)
            old_response = await asyncio.wait_for(task, timeout=2)
            self.assertEqual(old_response.status_code, 401, old_response.text)
            self.assertFalse(self.app.state.account_operations.tasks)
            self.assertEqual(device_lock.waiting_count, 0)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            device_lock.release()
        context = await run_blocking(memory.context_for, self.member.id, device_id)
        self.assertEqual([row['content'] for row in context['messages']], ['必须保留的正文'])

    async def test_cancellation_after_rename_commit_waits_for_cleanup_with_user_lock(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def blocked_cleanup(user_id):
            self.assertEqual(user_id, self.member.id)
            self.assertTrue(self.app.state.model_locks[user_id].locked())
            entered.set()
            await release.wait()
            self.assertTrue(self.app.state.model_locks[user_id].locked())
        with patch.object(self.app.state.account_operations, 'cancel_user', new=blocked_cleanup):
            task = asyncio.create_task(self.request('PATCH', '/' + self.member.id,
                                       {'username': 'renamed-cancelled', 'confirm_username': self.member.username}))
            try:
                await asyncio.wait_for(entered.wait(), timeout=2)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=2)
        self.assertFalse(self.app.state.model_locks[self.member.id].locked())
        self.assertEqual(self.admins.user(self.member.id)['username'], 'renamed-cancelled')
        with self.ids._transaction() as db:
            self.assertEqual(db.execute("SELECT result FROM admin_audit WHERE action='rename-user'").fetchone()[0], 'success')

    async def test_old_username_login_in_password_check_cannot_create_session_after_rename(self):
        verifying, release = threading.Event(), threading.Event()
        verify = self.ids.hasher.verify
        def paused_verify(password_hash, password):
            verifying.set()
            if not release.wait(timeout=5):
                raise TimeoutError('模拟密码校验未释放')
            return verify(password_hash, password)
        paused_hasher = SimpleNamespace(verify=paused_verify, hash=self.ids.hasher.hash,
                                         check_needs_rehash=self.ids.hasher.check_needs_rehash)
        with patch.object(self.ids, 'hasher', paused_hasher):
            login = asyncio.create_task(run_blocking(self.ids.login, self.member.username, 'temporary-test-password', client_key='queued-login'))
            try:
                self.assertTrue(await asyncio.to_thread(verifying.wait, 2))
                response = await self.request('PATCH', '/' + self.member.id,
                                              {'username': 'renamed-during-login', 'confirm_username': self.member.username})
                self.assertEqual(response.status_code, 200, response.text)
            finally:
                release.set()
            grant = await asyncio.wait_for(login, timeout=3)
        self.assertIsNone(grant, '旧用户名登录在改名后仍创建有效会话')
        with self.ids._transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM auth_sessions WHERE user_id=? AND revoked_at IS NULL', (self.member.id,)).fetchone()[0], 0)

    async def test_cancelled_queue_has_no_audit_mutation_or_detached_task(self):
        await self.disable()
        for kind in ('create', 'rename', 'delete'):
            lock = ObservedLock()
            if kind == 'create':
                self.app.state.admin_mutation_lock = lock
                method, suffix = 'POST', ''
                body = {'username': 'cancelled-create', 'password': 'temporary-test-password', 'confirm_username': 'cancelled-create'}
            else:
                self.app.state.model_locks[self.member.id] = lock
                method, suffix = ('PATCH' if kind == 'rename' else 'DELETE'), '/' + self.member.id
                body = {'confirm_username': self.member.username}
                if kind == 'rename':
                    body['username'] = 'cancelled-rename'
            await lock.acquire()
            before = asyncio.all_tasks()
            task = asyncio.create_task(self.request(method, suffix, body))
            try:
                await asyncio.wait_for(lock.waiting.wait(), timeout=2)
                queued = asyncio.all_tasks() - before
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self.assertTrue(task.cancelled())
            finally:
                lock.release()
            with self.subTest(kind=kind):
                self.assertFalse([item for item in queued if not item.done()], '取消队列遗留后台写任务')
                self.assertEqual(len(self.ids.list_users()), 3)
                self.assertEqual(self.admins.user(self.member.id)['username'], self.member.username)
                with self.ids._transaction() as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM admin_audit WHERE action IN ('create-user','rename-user','delete-user')").fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
