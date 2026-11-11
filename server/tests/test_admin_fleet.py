"""设备CRUD、一次性凭证及首页使用临时库验证，不访问硬件或真实模型。"""

from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from support import server_app
from access_control import AccessSettings, SESSION_COOKIE
from admin_fleet import FleetStore
from identity_store import csrf_for_session
from memory_store import MemoryStore, SummaryResult


async def summarize(messages):
    return SummaryResult(summary='模拟摘要')


class AdminFleetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='admin-fleet-')
        self.memory = MemoryStore(Path(self.temp.name) / 'memory.sqlite3')
        self.app = server_app.create_app(store=self.memory, summarizer=summarize,
                                        access=AccessSettings('accounts', 'https://cat.example'))
        self.ids = self.app.state.identities
        self.admins = self.app.state.admin_store
        self.admin = self.ids.create_user('fleet-admin', 'temporary-password')
        self.member = self.ids.create_user('ordinary-user', 'temporary-password')
        self.admins.set_role(self.admin.username, True)
        self.grant = self.ids.login(self.admin.username, 'temporary-password', client_key='test')
        self.headers = {'Origin': 'https://cat.example', 'X-CSRF-Token': csrf_for_session(self.grant.token)}
        self.client = TestClient(self.app, base_url='https://cat.example')
        self.client.__enter__()
        self.client.cookies.set(SESSION_COOKIE, self.grant.token)

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def register(self, device_id='deer-fleet-test'):
        result = self.client.post('/api/admin/devices', headers=self.headers,
                                  json={'id': device_id, 'display_name': '测试设备', 'confirm_device_id': device_id})
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()['credential']

    def write(self, device_id, action, **body):
        method = self.client.delete if action == 'delete' else self.client.patch if action == 'rename' else self.client.post
        url = '/api/admin/devices/' + device_id + ('' if action in ('delete', 'rename') else '/' + action)
        return self.client.request('DELETE', url, headers=self.headers,
                                   json={'confirm_device_id': device_id, **body}) if action == 'delete' else method(
            url, headers=self.headers, json={'confirm_device_id': device_id, **body})

    def bind(self, credential):
        grant = self.ids.request_pairing(credential['device_id'], credential['token'])
        self.ids.claim_pairing(grant.code, self.member.id, client_key='test')

    def test_register_detail_and_rename_do_not_leak_or_start_runtime(self):
        credential = self.register()
        self.assertIsNotNone(self.ids.credential_device(credential['device_id'], credential['token']))
        with patch.object(self.app.state.devices, 'get', side_effect=AssertionError('read created runtime')):
            detail = self.client.get('/api/admin/devices/' + credential['device_id'])
        self.assertEqual(detail.status_code, 200, detail.text)
        data = detail.json()
        self.assertEqual(data['device']['device_type'], 'deer')
        self.assertEqual(data['credentials']['active_count'], 1)
        self.assertFalse(data['can_delete'])
        result = self.write(credential['device_id'], 'rename', display_name='  办公室设备  ')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.ids.list_devices()[0]['display_name'], '办公室设备')
        for endpoint in ('devices', 'devices/' + credential['device_id'], 'audit', 'dashboard'):
            response = self.client.get('/api/admin/' + endpoint)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertNotIn(credential['token'], response.text)
            self.assertNotIn('token_hash', response.text)
        self.assertEqual(self.app.state.devices.devices, {})
        self.assertNotIn(credential['token'].encode(), self.ids.path.read_bytes())

    def test_duplicate_registration_and_invalid_inputs_are_explicit(self):
        self.register()
        result = self.client.post('/api/admin/devices', headers=self.headers,
                                  json={'id': 'deer-fleet-test', 'display_name': '重复', 'confirm_device_id': 'deer-fleet-test'})
        self.assertEqual(result.status_code, 409)
        self.assertEqual(len(self.ids.list_devices()), 1)
        for extra in ({'id': '../invalid'}, {'display_name': '   '}, {'display_name': 'a\nb'}, {'unknown': 1}):
            response = self.client.post('/api/admin/devices', headers=self.headers,
                json={'id': 'cat-new', 'display_name': '新设备', 'confirm_device_id': 'cat-new', **extra})
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.write('missing', 'rename', display_name='新名字').status_code, 404)
        self.assertEqual(self.client.patch('/api/admin/devices/deer-fleet-test', headers=self.headers,
            json={'display_name': '错误确认', 'confirm_device_id': 'different'}).status_code, 409)

    def test_rotation_invalidates_old_credential_and_pending_pairing(self):
        credential = self.register()
        pairing = self.ids.request_pairing(credential['device_id'], credential['token'])
        result = self.write(credential['device_id'], 'rotate-credential')
        self.assertEqual(result.status_code, 200, result.text)
        new = result.json()['credential']
        self.assertNotEqual(new['token'], credential['token'])
        self.assertIsNone(self.ids.credential_device(credential['device_id'], credential['token']))
        self.assertIsNotNone(self.ids.credential_device(new['device_id'], new['token']))
        with self.assertRaises(ValueError):
            self.ids.claim_pairing(pairing.code, self.member.id, client_key='test')
        self.bind(new)
        self.assertEqual(self.write(new['device_id'], 'rotate-credential').status_code, 409)
        self.assertIsNotNone(self.ids.credential_device(new['device_id'], new['token']))

    def test_revoke_preserves_history_and_memory_and_blocks_permanent_delete(self):
        credential = self.register()
        self.bind(credential)
        conversation = self.memory.create_conversation(self.member.id, credential['device_id'])
        self.memory.append_message(conversation, 'user', '不能泄露的聊天正文')
        response = self.write(credential['device_id'], 'revoke')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(self.ids.credential_device(credential['device_id'], credential['token']))
        self.assertIsNone(self.ids.list_devices()[0]['owner_user_id'])
        self.assertEqual(self.memory.conversation_owner(conversation)['user_id'], self.member.id)
        detail = self.client.get('/api/admin/devices/' + credential['device_id']).json()
        self.assertFalse(detail['can_delete'])
        self.assertEqual(detail['history']['memberships'], 1)
        self.assertEqual(detail['history']['conversations'], 1)
        self.assertNotIn('不能泄露的聊天正文', str(detail))
        self.assertEqual(self.write(credential['device_id'], 'delete').status_code, 409)

    def test_unused_device_can_be_deleted_only_after_revoke(self):
        credential = self.register()
        device_id = credential['device_id']
        self.assertEqual(self.write(device_id, 'delete').status_code, 409)
        self.assertEqual(self.write(device_id, 'revoke').status_code, 200)
        self.assertTrue(self.client.get('/api/admin/devices/' + device_id).json()['can_delete'])
        self.assertEqual(self.write(device_id, 'delete').status_code, 200)
        self.assertEqual(self.client.get('/api/admin/devices/' + device_id).status_code, 404)
        self.assertIsNone(self.ids.credential_device(device_id, credential['token']))
        audit = self.client.get('/api/admin/audit?q=' + device_id).json()['items']
        self.assertTrue(any(row['action'] == 'delete-device' and row['result'] == 'success' for row in audit))
        new = self.register(device_id)
        self.assertNotEqual(new['token'], credential['token'])
        self.assertIsNone(self.ids.credential_device(device_id, credential['token']))

    def test_cleanup_failure_keeps_revoke_committed_and_retry_is_idempotent(self):
        credential = self.register()
        device_id = credential['device_id']
        with patch.object(self.app.state.connections, 'revoke_device', side_effect=OSError('private-failure-path')):
            result = self.write(device_id, 'revoke')
        self.assertEqual(result.status_code, 503, result.text)
        self.assertNotIn('private-failure-path', result.text)
        self.assertIsNone(self.ids.credential_device(device_id, credential['token']))
        epoch = FleetStore(self.admins).snapshot(device_id)['access_epoch']
        self.assertEqual(self.write(device_id, 'revoke').status_code, 200)
        self.assertEqual(FleetStore(self.admins).snapshot(device_id)['access_epoch'], epoch)
        rows = self.client.get('/api/admin/audit?q=' + device_id).json()['items']
        self.assertTrue(any(row['action'] == 'revoke-device' and row['result'] == 'started' for row in rows))

    def test_writer_revoked_after_audit_cannot_mutate_in_transaction(self):
        self.register()
        original = FleetStore.mutate

        def revoked(store, *args, **kwargs):
            self.ids.revoke_session(self.grant.token)
            return original(store, *args, **kwargs)

        with patch.object(FleetStore, 'mutate', revoked):
            response = self.write('deer-fleet-test', 'rename', display_name='不应写入')
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(self.ids.list_devices()[0]['display_name'], '测试设备')

    def test_ownership_epoch_change_after_snapshot_cannot_mutate(self):
        credential = self.register()
        original = FleetStore.mutate

        def changed(store, *args, **kwargs):
            self.bind(credential)
            return original(store, *args, **kwargs)

        with patch.object(FleetStore, 'mutate', changed):
            response = self.write(credential['device_id'], 'revoke')
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.ids.list_devices()[0]['owner_user_id'], self.member.id)
        self.assertIsNotNone(self.ids.credential_device(credential['device_id'], credential['token']))

    def test_all_routes_reject_missing_admin_or_csrf(self):
        self.register()
        for method, url, data in (
                ('POST', '/api/admin/devices', {'id': 'cat-other', 'display_name': '设备', 'confirm_device_id': 'cat-other'}),
                ('PATCH', '/api/admin/devices/deer-fleet-test', {'display_name': '新名字', 'confirm_device_id': 'deer-fleet-test'}),
                ('POST', '/api/admin/devices/deer-fleet-test/revoke', {'confirm_device_id': 'deer-fleet-test'}),
                ('POST', '/api/admin/devices/deer-fleet-test/rotate-credential', {'confirm_device_id': 'deer-fleet-test'}),
                ('DELETE', '/api/admin/devices/deer-fleet-test', {'confirm_device_id': 'deer-fleet-test'})):
            with self.subTest(method=method, url=url):
                response = self.client.request(method, url, json=data)
                self.assertEqual(response.status_code, 403, response.text)
        grant = self.ids.login(self.member.username, 'temporary-password', client_key='member')
        self.client.cookies.clear()
        self.client.cookies.set(SESSION_COOKIE, grant.token)
        for path in ('dashboard', 'devices/deer-fleet-test'):
            self.assertEqual(self.client.get('/api/admin/' + path).status_code, 403)
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/api/admin/dashboard').status_code, 401)

    def test_dashboard_uses_real_seven_utc_days_and_sanitized_health(self):
        self.register()
        result = self.client.get('/api/admin/dashboard')
        self.assertEqual(result.status_code, 200, result.text)
        data = result.json()
        self.assertEqual(len(data['activity']), 7)
        self.assertEqual(sum(day['users'] for day in data['activity']), 2)
        self.assertEqual(sum(day['devices'] for day in data['activity']), 1)
        self.assertEqual(sum(day['actions'] for day in data['activity']), 2)
        self.assertEqual(data['workload']['active_sessions'], 1)
        self.assertEqual(data['workload']['unbound_devices'], 1)
        self.assertTrue(any(item['code'] == 'devices_unbound' for item in data['attention']))
        self.assertEqual(data['system']['database_engine'], 'SQLite')
        self.assertEqual(data['system']['worker_mode'], 'single')
        with patch('admin_dashboard.shutil.disk_usage', side_effect=OSError('private-failure-path')):
            result = self.client.get('/api/admin/dashboard')
        self.assertEqual(result.json()['system']['disk_status'], 'unknown')
        self.assertIsNone(result.json()['system']['disk_free_bytes'])
        self.assertNotIn('private-failure-path', result.text)
        with patch('admin_dashboard.dashboard_metadata', side_effect=sqlite3.OperationalError('private-failure-path')):
            result = self.client.get('/api/admin/dashboard')
        self.assertEqual(result.status_code, 503, result.text)
        self.assertNotIn('private-failure-path', result.text)

    def test_user_search_matches_visible_user_id_as_well_as_username(self):
        response = self.client.get('/api/admin/users', params={'q': self.member.id.upper()})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['total'], 1)
        self.assertEqual(response.json()['items'][0]['id'], self.member.id)
        self.assertEqual(self.client.get('/api/admin/users', params={'q': "%' OR 1=1 --"}).json()['total'], 0)


if __name__ == '__main__':
    unittest.main()
