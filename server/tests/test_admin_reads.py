"""后台只读接口回归：临时身份库，不连接设备或模型。"""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketState

from access_control import AccessSettings, SESSION_COOKIE
from memory_store import MemoryStore, SummaryResult
from support import server_app


async def summarize(_messages):
    return SummaryResult(summary='模拟摘要')


class AdminReadFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='admin-reads-')
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'memory.sqlite3'
        self.app = server_app.create_app(
            store=MemoryStore(self.path), summarizer=summarize,
            access=AccessSettings('accounts', 'https://cat.example'))
        self.ids = self.app.state.identities
        self.store = self.app.state.admin_store
        self.now = 1794297600.0
        self.ids.clock = lambda: self.now
        self.admin = self.ids.create_user('read-admin', 'temporary-password')
        self.owner = self.ids.create_user('read-owner', 'temporary-password')
        self.guest = self.ids.create_user('read-guest', 'temporary-password')
        self.store.set_role(self.admin.username, True)
        grant = self.ids.login(self.admin.username, 'temporary-password', client_key='read-test')
        # 只测 HTTP 读接口，不启动借用清理或其他后台任务。
        self.client = TestClient(self.app, base_url='https://cat.example',
                                 client=('127.0.0.1', 12345), raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.client.cookies.set(SESSION_COOKIE, grant.token)

    def register(self, device_id, *, bound=True, name='读取测试设备', owner=None):
        credential = self.ids.register_device(device_id, name)
        if bound:
            pairing = self.ids.request_pairing(device_id, credential.token)
            self.ids.claim_pairing(pairing.code, (owner or self.owner).id, client_key=device_id)
            self.ids.save_voice_preference((owner or self.owner).id, device_id, False)
        return device_id

    def lend(self, device_id, *, claim=True):
        loan = self.ids.create_loan(device_id, self.owner.id, 1)
        if claim:
            self.ids.claim_loan(loan['code'], self.guest.id, client_key=device_id)
        return loan

    def get(self, path, **params):
        response = self.client.get('/api/admin/' + path, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers.get('cache-control'), 'no-store')
        return response.json()

    def mixed_devices(self):
        expected = {}
        for prefix, kind in (('cat', 'cat'), ('deer', 'deer'), ('other', 'unknown')):
            for state in ('bound', 'unbound', 'loaned', 'revoked'):
                device_id = self.register(f'{prefix}-read-{state}', bound=state != 'unbound',
                                          name='小鹿' if kind == 'cat' else '小猫')
                if state == 'loaned':
                    self.lend(device_id)
                    self.ids.save_voice_preference(self.guest.id, device_id, True)
                if state == 'revoked':
                    self.ids.revoke_device(device_id)
                expected[device_id] = (kind, state)
        return expected

    @contextmanager
    def trace_queries(self):
        statements = []
        transaction = self.ids._transaction

        @contextmanager
        def traced(**kwargs):
            with transaction(**kwargs) as db:
                db.set_trace_callback(statements.append)
                yield db

        with patch.object(self.ids, '_transaction', traced):
            yield statements

    def runtime(self, device_id, *, user=None, audio=False, camera=None, closed=False):
        return SimpleNamespace(device_id=device_id, user_id=(user or self.owner).id,
                               _closed=closed, voice_paused=True,
                               esp32_audio_connected=lambda: audio,
                               esp32_camera_ws=(SimpleNamespace(client_state=camera)
                                                if camera is not None else None))


class AdminDeviceMetadataTests(AdminReadFixture):
    def test_type_requires_an_exact_registered_prefix(self):
        for device_id in ('cat', 'deer', 'catfish-one', 'deers-one', 'Deer-one'):
            self.register(device_id, bound=False, name='赛博小鹿')
        for row in self.get('devices')['items']:
            self.assertEqual(row['device_type'], 'unknown', row['id'])

    def test_cat_deer_unknown_and_binding_states_across_pages(self):
        expected = self.mixed_devices()
        rows = []
        for page in range(1, 7):
            result = self.get('devices', page=page, page_size=2)
            self.assertEqual((result['total'], result['page'], result['page_size']), (12, page, 2))
            rows.extend(result['items'])
        self.assertEqual([row['id'] for row in rows], sorted(expected))
        self.assertEqual(self.get('devices', page=7, page_size=2)['items'], [])
        for row in rows:
            kind, state = expected[row['id']]
            with self.subTest(device=row['id']):
                self.assertEqual(row['device_type'], kind)
                self.assertEqual(row['revoked'], state == 'revoked')
                self.assertEqual(row['loan_active'], state == 'loaned')
                self.assertFalse(row['loan_pending'])
                self.assertFalse(row['online'])
                if state == 'unbound':
                    for field in ('owner_user_id', 'owner_username', 'active_user_id',
                                  'active_username', 'voice_paused'):
                        self.assertIsNone(row[field], field)
                else:
                    self.assertEqual(row['owner_user_id'], self.owner.id)
                    self.assertEqual(row['owner_username'], self.owner.username)
                    active = self.guest if state == 'loaned' else self.owner
                    self.assertEqual(row['active_user_id'], active.id)
                    self.assertEqual(row['active_username'], active.username)
                    self.assertEqual(row['voice_paused'], state == 'loaned')

    def test_bound_unbound_search_and_totals_share_pagination(self):
        expected = self.mixed_devices()
        for status, total in (('bound', 9), ('unbound', 3), ('online', 0), ('offline', 12)):
            with self.subTest(status=status):
                result = self.get('devices', status=status, page=2, page_size=2)
                self.assertEqual(result['total'], total)
                self.assertEqual(len(result['items']), min(2, max(0, total - 2)))
        deer = self.get('devices', q='DEER-READ', page=2, page_size=2)
        self.assertEqual(deer['total'], 4)
        self.assertEqual([row['id'] for row in deer['items']],
                         sorted(key for key in expected if key.startswith('deer-'))[2:])
        self.assertEqual(self.get('devices', q=self.owner.username)['total'], 9)

    def test_pending_ended_and_expired_loans_keep_correct_active_user(self):
        pending = self.register('deer-pending')
        ended = self.register('cat-ended')
        expired = self.register('other-expired')
        self.lend(pending, claim=False)
        self.lend(ended)
        self.ids.end_loan(ended, self.guest.id)
        self.lend(expired)
        rows = {row['id']: row for row in self.get('devices')['items']}
        self.assertTrue(rows[pending]['loan_pending'])
        self.assertFalse(rows[pending]['loan_active'])
        self.assertEqual(rows[expired]['active_user_id'], self.guest.id)
        self.now += 3601
        for row in self.get('devices')['items']:
            self.assertEqual(row['active_user_id'], self.owner.id)
            self.assertFalse(row['loan_active'])
            self.assertFalse(row['loan_pending'])
            self.assertIsNone(row['loan_expires_at'])

    def test_mixed_device_reads_never_create_runtime_or_call_model(self):
        self.mixed_devices()
        registry = self.app.state.devices
        with patch.object(registry, 'get', side_effect=AssertionError('read creates runtime')), \
                patch.object(registry, 'resolve', side_effect=AssertionError('read resolves runtime')), \
                patch.object(self.app.state.providers, 'key', side_effect=AssertionError('read calls model')):
            for path in ('session', 'overview', 'users', 'devices', 'storage', 'backups', 'audit'):
                self.get(path)
            self.get('devices', status='online')
            self.get('devices', status='offline')
        self.assertEqual(registry.devices, {})


class AdminOverviewTests(AdminReadFixture):
    def test_offline_fleet_needs_no_per_device_metadata_queries(self):
        for index in range(25):
            self.register(f'deer-offline-{index}', bound=False)
        with patch.object(self.ids, 'decorate_device', wraps=self.ids.decorate_device) as decorate:
            result = self.get('overview')
        self.assertEqual(result['devices'], {'total': 25, 'bound': 0, 'online': 0})
        self.assertEqual(decorate.call_count, 0)

    def test_overview_only_decorates_registered_runtime_candidates(self):
        for index in range(25):
            self.register(f'other-offline-{index}', bound=False)
        audio = self.register('cat-audio')
        idle = self.register('deer-idle')
        registry = self.app.state.devices
        runtimes = {audio: self.runtime(audio, audio=True), idle: self.runtime(idle),
                    'cat-not-registered': self.runtime('cat-not-registered', audio=True)}
        with patch.dict(registry.devices, runtimes), \
                patch.object(registry, 'get', side_effect=AssertionError('read creates runtime')), \
                patch.object(self.ids, 'decorate_device', wraps=self.ids.decorate_device) as decorate:
            result = self.get('overview')
        self.assertEqual(result['devices'], {'total': 27, 'bound': 2, 'online': 1})
        self.assertEqual({call.args[1]['id'] for call in decorate.call_args_list}, {audio, idle})
        self.assertEqual(decorate.call_count, 2)

    def test_runtime_identity_revocation_and_socket_states_gate_online(self):
        disabled = self.ids.create_user('read-disabled', 'temporary-password')
        runtimes = {}
        for device_id, options in (
            ('cat-audio', {'audio': True}),
            ('deer-camera', {'camera': WebSocketState.CONNECTED}),
            ('other-ui-only', {}),
            ('deer-camera-disconnected', {'camera': WebSocketState.DISCONNECTED}),
            ('cat-camera-connecting', {'camera': WebSocketState.CONNECTING}),
            ('other-wrong-user', {'user': self.guest, 'audio': True}),
            ('cat-closed', {'audio': True, 'closed': True}),
            ('deer-revoked', {'audio': True}),
            ('other-disabled', {'user': disabled, 'audio': True}),
            ('cat-unbound', {'audio': True}),
            ('deer-loan', {'user': self.guest, 'audio': True}),
            ('other-expired-loan', {'user': self.guest, 'audio': True}),
        ):
            self.register(device_id, bound=device_id != 'cat-unbound',
                          owner=disabled if device_id == 'other-disabled' else None)
            if device_id == 'deer-revoked':
                self.ids.revoke_device(device_id)
            if device_id == 'other-disabled':
                self.store.change_user(self.admin.id, disabled.id, disabled=True)
            if device_id == 'deer-loan':
                self.lend(device_id)
            if device_id == 'other-expired-loan':
                self.now -= 7200
                self.lend(device_id)
                self.now += 7200
            runtimes[device_id] = self.runtime(device_id, **options)
        runtimes['cat-not-registered'] = self.runtime('cat-not-registered', audio=True)
        online = {'cat-audio', 'deer-camera', 'deer-loan'}
        with patch.dict(self.app.state.devices.devices, runtimes):
            self.assertEqual(self.get('overview')['devices']['online'], len(online))
            rows = self.get('devices', page_size=100)['items']
            self.assertEqual({row['id'] for row in rows if row['online']}, online)
            self.assertEqual({row['id'] for row in rows if row['camera_online']}, {'deer-camera'})
            first = self.get('devices', status='online', page_size=2)
            second = self.get('devices', status='online', page=2, page_size=2)
            self.assertEqual(first['total'], len(online))
            self.assertEqual([row['id'] for row in first['items'] + second['items']], sorted(online))
            offline = self.get('devices', status='offline', page_size=100)
            self.assertEqual(offline['total'], 9)
            self.assertEqual({row['id'] for row in offline['items']}, set(runtimes) - online - {'cat-not-registered'})

    def test_overview_storage_bytes_match_detail_without_detail_aggregates(self):
        device_id = self.register('deer-storage')
        memory = self.app.state.memory_manager.store
        conversation = memory.create_conversation(self.owner.id, device_id)
        message = memory.append_message(conversation, 'user', '模拟归档原文' * 1000)
        memory.save_summary(conversation, message, '模拟摘要', memories=['模拟事实'])
        with self.ids._transaction(write=True) as db:
            db.execute("UPDATE conversations SET last_activity_at='2000-01-01T00:00:00+00:00'")
        retention = self.app.state.memory_retention
        retention.clock = lambda: self.now
        retention.set_mode(self.owner.id, device_id, 'temporary')
        retention.clock = lambda: self.now + 31 * 86400
        self.assertEqual(retention.archive(self.owner.id, device_id), 1)
        memory.append_message(conversation, 'user', '新消息🙂')
        document = '隔离资料🙂'.encode('utf-8')
        self.app.state.knowledge.store.add_document(self.owner.id, 'fixture.txt', document)
        detail = self.get('storage')
        with self.trace_queries() as statements:
            response = self.client.get('/api/admin/overview')
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()['storage']
        self.assertEqual(result['knowledge_bytes'], len(document))
        self.assertEqual(result['memory_content_bytes'], len('新消息🙂模拟摘要模拟事实'.encode('utf-8')))
        self.assertEqual(result['archive_bytes'], detail['memory']['archive_bytes'])
        self.assertGreater(result['archive_bytes'], 0)
        self.assertEqual(result['knowledge_bytes'], detail['documents']['bytes'])
        self.assertEqual(result['memory_content_bytes'], detail['memory']['content_bytes'])
        self.assertFalse(any('GROUP BY' in statement.upper() for statement in statements))
        self.assertFalse(any('COUNT(*) FROM messages' in statement for statement in statements))
        for private in ('新消息', '模拟摘要', '模拟事实', 'fixture.txt'):
            self.assertNotIn(private, response.text)


class AdminReadBoundaryTests(AdminReadFixture):
    def test_huge_pages_return_empty_items_and_real_total(self):
        self.register('deer-page-boundary')
        for path in ('users', 'devices', 'audit'):
            total = self.get(path)['total']
            self.assertGreater(total, 0)
            for page in (2 ** 63, 10 ** 100):
                with self.subTest(path=path, page=page):
                    result = self.get(path, page=page, page_size=100)
                    self.assertEqual(result, {'items': [], 'total': total,
                                              'page': page, 'page_size': 100})

    def test_filtered_huge_page_does_not_decorate_devices(self):
        self.mixed_devices()
        for status, total in (('all', 4), ('unbound', 1), ('offline', 4)):
            with self.subTest(status=status), \
                    patch.object(self.ids, 'decorate_device', wraps=self.ids.decorate_device) as decorate:
                result = self.get('devices', q='deer-read', status=status, page=2 ** 63)
                self.assertEqual(result['total'], total)
                self.assertEqual(result['items'], [])
                self.assertEqual(decorate.call_count, 0)

    def assert_read_unavailable(self, response):
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.headers.get('cache-control'), 'no-store')
        self.assertIn('稍后重试', response.json()['detail'])
        self.assertNotIn('private-database-location', response.text)

    def test_database_read_failures_return_retryable_private_json(self):
        for path, method in (('overview', 'counts'), ('users', 'users'), ('devices', 'devices'),
                             ('storage', 'storage'), ('audit', 'audit')):
            with self.subTest(path=path), patch.object(
                    self.store, method, side_effect=sqlite3.OperationalError('private-database-location')):
                self.assert_read_unavailable(self.client.get('/api/admin/' + path))

    def test_runtime_candidate_read_failures_do_not_report_zero_online(self):
        device_id = self.register('deer-candidate-failure')
        with patch.dict(self.app.state.devices.devices, {device_id: self.runtime(device_id, audio=True)}), \
                patch.object(self.store, 'devices', side_effect=sqlite3.OperationalError('private-database-location')):
            for path in ('overview', 'devices?status=online', 'devices?status=offline'):
                with self.subTest(path=path):
                    self.assert_read_unavailable(self.client.get('/api/admin/' + path))

    def test_capacity_failure_does_not_return_partial_overview(self):
        with patch.object(self.store, 'storage_bytes',
                          side_effect=sqlite3.DatabaseError('private-database-location')):
            self.assert_read_unavailable(self.client.get('/api/admin/overview'))

    def test_unreadable_database_stat_returns_retryable_json(self):
        stat = Path.stat

        def inaccessible(path, *args, **kwargs):
            if path == self.path:
                raise PermissionError('private-database-location')
            return stat(path, *args, **kwargs)

        with patch.object(Path, 'stat', inaccessible):
            self.assert_read_unavailable(self.client.get('/api/admin/overview'))

    def test_missing_database_is_unavailable_but_missing_wal_is_zero(self):
        stat = Path.stat
        for missing in (self.path, Path(str(self.path) + '-wal')):
            def absent(path, *args, **kwargs):
                if path == missing:
                    raise FileNotFoundError('private-database-location')
                return stat(path, *args, **kwargs)

            with self.subTest(missing=missing.name), patch.object(Path, 'stat', absent):
                response = self.client.get('/api/admin/overview')
                if missing == self.path:
                    self.assert_read_unavailable(response)
                else:
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()['storage']['wal_bytes'], 0)


if __name__ == '__main__':
    unittest.main()
