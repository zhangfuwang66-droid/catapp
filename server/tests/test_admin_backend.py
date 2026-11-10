"""管理员真实 API 与运行时收尾，全部使用临时数据库和模拟总结。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from support import server_app
from access_control import AccessSettings, SESSION_COOKIE
from identity_store import csrf_for_session
from memory_store import MemoryStore, SummaryResult
from admin_cli import main as cli_main


async def summarize(messages):
    return SummaryResult(summary='模拟摘要')


class AdminBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='admin-backend-')
        self.path = Path(self.temp.name)/'memory.sqlite3'
        self.app = server_app.create_app(store=MemoryStore(self.path),summarizer=summarize,
                                        access=AccessSettings('accounts','https://cat.example'))
        self.ids = self.app.state.identities
        self.admin = self.ids.create_user('admin-test','temporary-password')
        self.member = self.ids.create_user('owner','temporary-password')
        self.second = self.ids.create_user('second-admin','temporary-password')
        self.app.state.admin_store.set_role('admin-test',True)
        self.credential = self.ids.register_device('cat-admin-test','测试小猫')
        pairing = self.ids.request_pairing('cat-admin-test',self.credential.token)
        self.ids.claim_pairing(pairing.code,self.member.id,client_key='test')
        self.client = TestClient(self.app,base_url='https://cat.example',client=('127.0.0.1',12345))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.temp.cleanup()

    def login(self,username='admin-test'):
        grant = self.ids.login(username,'temporary-password',client_key='test')
        self.client.cookies.set(SESSION_COOKIE,grant.token)
        return {'Origin':'https://cat.example','X-CSRF-Token':csrf_for_session(grant.token)}

    def test_every_endpoint_requires_explicit_admin_and_revocation(self):
        for path in ('session','overview','users','devices','storage','backups','audit'):
            self.assertEqual(self.client.get('/api/admin/'+path).status_code,401,path)
        self.login('owner')
        for path in ('session','overview','users','devices','storage','backups','audit'):
            self.assertEqual(self.client.get('/api/admin/'+path).status_code,403,path)
        self.login()
        self.assertEqual(self.client.get('/api/admin/session').json()['user']['username'],'admin-test')
        self.app.state.admin_store.set_role('second-admin',True)
        self.app.state.admin_store.set_role('admin-test',False)
        self.assertEqual(self.client.get('/api/admin/users').status_code,403)

    def test_csrf_pagination_and_sensitive_whitelist(self):
        headers = self.login()
        endpoint = '/api/admin/users/'+self.member.id+'/status'
        body = {'disabled':True,'confirm_username':'owner'}
        self.assertEqual(self.client.post(endpoint,json=body).status_code,403)
        self.assertEqual(self.client.post(endpoint,json=body,headers={**headers,'Origin':'https://evil.test'}).status_code,403)
        self.assertEqual(self.client.post(endpoint,json=body,headers={**headers,'X-CSRF-Token':'wrong'}).status_code,403)
        result = self.client.get('/api/admin/users?q=owner&page_size=1').json()
        self.assertEqual(result['total'],1)
        self.assertEqual(result['items'][0]['device_count'],1)
        self.assertIs(result['items'][0]['key_configured'],False)
        for query in ('page=0','page_size=101','status=nope'):
            self.assertEqual(self.client.get('/api/admin/users?'+query).status_code,422)
        self.assertEqual(self.client.get('/api/admin/users?page=999').json()['items'],[])
        all_json = ''.join(self.client.get('/api/admin/'+p).text for p in ('users','devices','overview','storage','audit'))
        for secret in ('password_hash','ciphertext','token_hash',self.credential.token,'temporary-password'):
            self.assertNotIn(secret,all_json)

    def test_disable_and_revoke_retire_runtime_and_invalidate_hardware(self):
        headers = self.login()
        old = self.ids.login('owner','temporary-password',client_key='test')
        self.client.portal.call(self.app.state.devices.resolve,'cat-admin-test',self.member.id)
        result = self.client.post('/api/admin/users/'+self.member.id+'/status',headers=headers,
                                  json={'disabled':True,'confirm_username':'owner'})
        self.assertEqual(result.status_code,200,result.text)
        self.assertIsNone(self.ids.session_user(old.token))
        self.assertIsNone(self.ids.credential_device('cat-admin-test',self.credential.token))
        self.assertNotIn('cat-admin-test',self.app.state.devices.devices)
        self.assertTrue(self.ids.voice_preference(self.member.id,'cat-admin-test'))
        self.assertEqual(self.client.get('/api/admin/users?status=disabled').json()['total'],1)
        result = self.client.post('/api/admin/users/'+self.member.id+'/status',headers=headers,
                                  json={'disabled':False,'confirm_username':'owner'})
        self.assertEqual(result.status_code,200,result.text)
        self.assertIsNotNone(self.ids.credential_device('cat-admin-test',self.credential.token))
        old2 = self.ids.login('owner','temporary-password',client_key='test')
        result = self.client.post('/api/admin/users/'+self.member.id+'/revoke-sessions',headers=headers,json={'confirm_username':'owner'})
        self.assertEqual(result.status_code,200,result.text)
        self.assertIsNone(self.ids.session_user(old2.token))

    def test_read_no_runtime_and_voice_unbind_preserve_credentials(self):
        headers = self.login()
        with patch.object(self.app.state.devices,'get',side_effect=AssertionError('read creates runtime')):
            for path in ('overview','users','devices','storage','backups','audit'):
                result = self.client.get('/api/admin/'+path)
                self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(self.client.get('/api/admin/devices?status=offline').json()['total'],1)
        endpoint = '/api/admin/devices/cat-admin-test'
        bad = self.client.post(endpoint+'/voice',headers=headers,json={'paused':True,'confirm_device_id':'wrong'})
        self.assertEqual(bad.status_code,409)
        for paused in (True,False,True):
            result = self.client.post(endpoint+'/voice',headers=headers,json={'paused':paused,'confirm_device_id':'cat-admin-test'})
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(self.ids.voice_preference(self.member.id,'cat-admin-test'),paused)
        self.assertEqual(self.app.state.devices.devices,{})
        result = self.client.post(endpoint+'/unbind',headers=headers,json={'confirm_device_id':'cat-admin-test'})
        self.assertEqual(result.status_code,200,result.text)
        credential = self.ids.credential_device('cat-admin-test',self.credential.token)
        self.assertIsNotNone(credential)
        self.assertIsNone(credential['owner_user_id'])
        audit = self.client.get('/api/admin/audit').json()['items']
        self.assertTrue(any(a['action']=='unbind-device' and a['result']=='success' for a in audit))
        self.assertTrue(any(a['result']=='failed' for a in audit))

    def test_self_lockout_last_admin_and_cli(self):
        headers = self.login()
        result = self.client.post('/api/admin/users/'+self.admin.id+'/status',headers=headers,
                                  json={'disabled':True,'confirm_username':'admin-test'})
        self.assertEqual(result.status_code,409)
        with self.assertRaises(ValueError):
            self.app.state.admin_store.set_role('admin-test',False)
        with patch('builtins.print'):
            self.assertEqual(cli_main(['--database',str(self.path),'grant','--username','second-admin']),0)
            self.assertEqual(cli_main(['--database',str(self.path),'revoke','--username','admin-test']),0)
        self.assertEqual(self.client.get('/api/admin/session').status_code,403)
