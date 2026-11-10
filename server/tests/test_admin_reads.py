"""后台只读接口回归：临时身份库，不连接设备或模型。"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

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

    def register(self, device_id, *, bound=True, name='读取测试设备'):
        credential = self.ids.register_device(device_id, name)
        if bound:
            pairing = self.ids.request_pairing(device_id, credential.token)
            self.ids.claim_pairing(pairing.code, self.owner.id, client_key=device_id)
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


if __name__ == '__main__':
    unittest.main()
