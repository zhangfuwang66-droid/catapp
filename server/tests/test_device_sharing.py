"""设备转交、临时借用、旧连接失效与双方历史隔离。"""

import asyncio
from contextlib import closing
import sqlite3
import unittest

from starlette.websockets import WebSocketDisconnect
from connection_authorization import DeviceAuthorization
from identity_store import IdentityStore
import test_device_auth as auth_fixture
from tools.memory_database import inspect_database


class SharingTests(unittest.TestCase):
    setUp = auth_fixture.DeviceAuthorizationTests.setUp
    tearDown = auth_fixture.DeviceAuthorizationTests.tearDown

    def seed(self, name, text):
        conversation = self.store.create_conversation(self.users[name].id, 'cat-alice')
        last = self.store.append_message(conversation, 'user', text)
        self.store.save_summary(conversation,last,text)

    def test_unbind_keeps_credential_invalidates_connection_and_preserves_old_history(self):
        self.seed('alice','甲的私有回忆')
        with self.client.websocket_connect('/ws/devices/cat-alice/ui',headers=self.headers['alice']) as ws:
            while not ws.receive_text().startswith('INIT:'):
                pass
            response = self.client.post('/api/devices/cat-alice/unbind',headers=self.headers['alice'],json={'confirm_device_id':'cat-alice'})
            self.assertEqual(response.status_code,200,response.text)
            with self.assertRaises(WebSocketDisconnect):
                while True:
                    ws.receive_text()
        self.assertFalse(self.client.get('/api/devices/cat-alice/binding',headers=self.credentials['alice']).json()['bound'])
        self.assertEqual(self.client.get('/api/devices/cat-alice/memory',headers=self.headers['alice']).status_code,404)
        self.assertIn('甲的私有回忆',self.client.get('/api/account/memories/cat-alice',headers=self.headers['alice']).text)
        pairing = self.client.post('/api/devices/cat-alice/pairing',headers=self.credentials['alice']).json()
        response = self.client.post('/api/devices/pair',headers=self.headers['bob'],json={'code':pairing['code']})
        self.assertEqual(response.status_code,200,response.text)
        self.assertNotIn('甲的私有回忆',self.client.get('/api/devices/cat-alice/memory',headers=self.headers['bob']).text)
        self.assertTrue(inspect_database(self.store.path)['ok'])

    def borrow(self, hours=1):
        response = self.client.post('/api/devices/cat-alice/loan',headers=self.headers['alice'],json={'confirm_device_id':'cat-alice','hours':hours})
        self.assertEqual(response.status_code,200,response.text)
        code = response.json()['code']
        response = self.client.post('/api/devices/borrow',headers=self.headers['bob'],json={'code':code})
        self.assertEqual(response.status_code,200,response.text)
        return code

    def test_borrower_is_only_controller_and_owner_cannot_read_guest_memory(self):
        self.seed('alice','甲的历史')
        code = self.borrow()
        self.seed('bob','乙的私聊')
        self.assertEqual(self.client.get('/console/cat-alice',headers=self.headers['alice']).status_code,404)
        self.assertEqual(self.client.get('/api/devices/cat-alice/voice',headers=self.headers['alice']).status_code,404)
        self.assertEqual(self.client.get('/api/devices/cat-alice/voice',headers=self.headers['bob']).status_code,200)
        self.assertNotIn('乙的私聊',self.client.get('/api/account/memories/cat-alice',headers=self.headers['alice']).text)
        self.assertNotIn('甲的历史',self.client.get('/api/account/memories/cat-alice',headers=self.headers['bob']).text)
        self.assertEqual(self.client.post('/api/devices/borrow',headers=self.headers['bob'],json={'code':code}).status_code,400)
        self.assertEqual(self.client.post('/api/devices/cat-alice/unbind',headers=self.headers['bob'],json={'confirm_device_id':'cat-alice'}).status_code,404)
        self.assertEqual(self.client.post('/api/devices/cat-alice/loan/end',headers=self.headers['bob'],json={'confirm_device_id':'cat-alice'}).status_code,200)
        self.assertEqual(self.client.get('/api/devices/cat-alice/voice',headers=self.headers['alice']).status_code,200)
        self.assertEqual(self.client.get('/api/devices/cat-alice/voice',headers=self.headers['bob']).status_code,404)
        self.assertIn('乙的私聊',self.client.get('/api/account/memories/cat-alice',headers=self.headers['bob']).text)
        self.assertTrue(inspect_database(self.store.path)['ok'])

    def test_expired_loan_and_epoch_reject_old_authorization_even_when_owner_returns(self):
        row = self.identities.accessible_device('cat-alice',self.users['alice'].id)
        token = self.credentials['alice']['Authorization'].split()[1]
        old = DeviceAuthorization('device',self.users['alice'].id,'cat-alice',token,row['access_epoch'])
        self.assertTrue(asyncio.run(old.is_valid(self.identities)))
        self.borrow()
        self.assertFalse(asyncio.run(old.is_valid(self.identities)))
        now = self.identities.clock()
        self.identities.clock = lambda: now + 3601
        self.identities.expire_loans()
        self.assertIsNotNone(self.identities.accessible_device('cat-alice',self.users['alice'].id))
        self.assertFalse(asyncio.run(old.is_valid(self.identities)))

    def test_voice_pause_persists_across_runtime_recreation_and_new_store(self):
        response = self.client.post('/api/devices/cat-alice/voice',headers=self.headers['alice'],json={'paused':True})
        self.assertEqual(response.status_code,200,response.text)
        self.client.portal.call(self.app.state.devices.retire,'cat-alice')
        self.assertTrue(self.client.get('/api/devices/cat-alice/voice',headers=self.headers['alice']).json()['paused'])
        self.assertTrue(IdentityStore(self.store.path).voice_preference(self.users['alice'].id,'cat-alice'))

    def test_key_rotation_closes_only_current_account_runtime_and_preserves_pause(self):
        self.client.post('/api/devices/cat-alice/voice',headers=self.headers['alice'],json={'paused':True})
        with self.client.websocket_connect('/ws/devices/cat-alice/audio',headers=self.credentials['alice']) as audio:
            self.client.get('/api/devices/cat-bob/voice',headers=self.headers['bob'])
            other=self.app.state.devices.devices['cat-bob']
            response=self.client.put('/api/account/model',headers=self.headers['alice'],json={'api_key':'sk-simulated-rotated-key'})
            self.assertEqual(response.status_code,200,response.text)
            with self.assertRaises(WebSocketDisconnect):
                while True: audio.receive_text()
            self.assertIs(self.app.state.devices.devices['cat-bob'],other)
        self.assertTrue(self.client.get('/api/devices/cat-alice/voice',headers=self.headers['alice']).json()['paused'])

    def test_v2_migration_records_existing_ownership_without_moving_memory(self):
        self.seed('alice','升级前记忆')
        # 独立数据库副本，不与正在运行的应用并发降级。
        destination = self.store.path.with_name('old-v2.sqlite3')
        with closing(sqlite3.connect(self.store.path)) as source, closing(sqlite3.connect(destination)) as db:
            source.backup(db)
            for name in ('device_preferences','device_loans','device_memberships'):
                db.execute('DROP TABLE '+name)
            db.execute('ALTER TABLE devices DROP COLUMN access_epoch')
            db.execute("UPDATE schema_migrations SET version=2 WHERE component='identity'")
            db.commit()
        self.assertTrue(inspect_database(destination)['ok'])
        upgraded = IdentityStore(destination)
        self.assertTrue(upgraded.historical_device('cat-alice',self.users['alice'].id))
        self.assertTrue(inspect_database(destination)['ok'])


if __name__ == '__main__':
    unittest.main()
