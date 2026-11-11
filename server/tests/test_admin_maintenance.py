"""备份入口使用临时数据库和目录，覆盖权限、状态与脱敏元数据。"""

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from support import server_app
from access_control import AccessSettings, SESSION_COOKIE
from database_backups import BackupSettings
from identity_store import csrf_for_session
from memory_store import MemoryStore, SummaryResult


async def summarize(messages):
    return SummaryResult(summary='模拟摘要')


class AdminMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='admin-maintenance-')
        self.root = Path(self.temp.name)
        self.app = server_app.create_app(store=MemoryStore(self.root / 'memory.sqlite3'), summarizer=summarize,
                                        access=AccessSettings('accounts', 'https://cat.example'),
                                        backup_settings=BackupSettings(self.root / 'backups', min_free_bytes=0))
        self.ids = self.app.state.identities
        self.ids.create_user('backup-admin', 'temporary-password')
        self.app.state.admin_store.set_role('backup-admin', True)
        self.grant = self.ids.login('backup-admin', 'temporary-password', client_key='test')
        self.headers = {'Origin': 'https://cat.example', 'X-CSRF-Token': csrf_for_session(self.grant.token)}
        self.client = TestClient(self.app, base_url='https://cat.example')
        self.client.__enter__()
        self.client.cookies.set(SESSION_COOKIE, self.grant.token)
        self.service = self.app.state.database_backups

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def test_check_existing_backup_and_safe_history(self):
        deadline = time.monotonic() + 5
        while not self.service.last_success_at and time.monotonic() < deadline:
            time.sleep(.02)
        response = self.client.post('/api/admin/backups/run', headers=self.headers, json={'confirm': '检查并备份'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['created'])
        self.assertIn('近期备份', response.json()['message'])
        self.assertFalse(response.json()['status']['running'])
        self.assertTrue(response.json()['status']['scheduler_running'])
        response = self.client.get('/api/admin/backups/history')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        data = response.json()
        self.assertGreaterEqual(data['total'], 1)
        self.assertFalse(data['includes_provider_key'])
        self.assertGreater(data['items'][0]['bytes'], 0)
        self.assertTrue(data['items'][0]['verified'])
        self.assertNotIn(self.temp.name, response.text)
        for field in ('token_hash', 'password_hash', 'sha256', 'filename', 'receipt', 'provider.key'):
            self.assertNotIn(field, response.text)
        audit = self.client.get('/api/admin/audit?q=check-backup').json()['items']
        self.assertEqual(audit[0]['result'], 'success')

    def test_failed_check_is_private_and_audited(self):
        with patch.object(self.service.repository, 'verified_entries', side_effect=OSError('private-backup-path')):
            response = self.client.post('/api/admin/backups/run', headers=self.headers, json={'confirm': '检查并备份'})
        self.assertEqual(response.status_code, 503, response.text)
        self.assertNotIn('private-backup-path', response.text)
        audit = self.client.get('/api/admin/audit?q=check-backup').json()['items']
        self.assertEqual(audit[0]['result'], 'failed')

    def test_backup_requires_confirmation_admin_and_csrf(self):
        self.assertEqual(self.client.post('/api/admin/backups/run', json={'confirm': '检查并备份'}).status_code, 403)
        self.assertEqual(self.client.post('/api/admin/backups/run', headers=self.headers,
                                          json={'confirm': 'wrong'}).status_code, 422)
        self.ids.revoke_session(self.grant.token)
        self.assertEqual(self.client.get('/api/admin/backups/history').status_code, 401)
        self.assertEqual(self.client.post('/api/admin/backups/run', headers=self.headers,
                                          json={'confirm': '检查并备份'}).status_code, 401)


if __name__ == '__main__':
    unittest.main()
