"""真实加密与账号接口隔离；模型调用全部模拟，不消耗云端额度。"""

import asyncio
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from starlette.responses import Response
from starlette.requests import Request
from support import server_app
from access_control import AccessSettings, SESSION_COOKIE
from identity_store import IdentityStore, csrf_for_session
from memory_store import MemoryStore
from provider_settings import ProviderSettings, ModelCredentials, MissingProviderKey, VaultUnavailable
import provider_routes


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'memory.sqlite3'
        MemoryStore(self.path)
        self.identities = IdentityStore(self.path)
        self.a = self.identities.create_user('alice', 'alice-test-password')
        self.b = self.identities.create_user('bob', 'bob-test-password')
        self.vault = ProviderSettings(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_ciphertext_is_bound_to_user_and_missing_master_fails_closed(self):
        key = 'sk-simulated-alice-private'
        status = self.vault.save(self.a.id, key)
        self.assertNotIn(key, str(status))
        self.assertEqual(self.vault.get(self.a.id), key)
        with closing(sqlite3.connect(self.path)) as db:
            ciphertext = db.execute('SELECT ciphertext FROM account_provider_keys').fetchone()[0]
            self.assertNotIn(key.encode(), ciphertext)
            db.execute('INSERT INTO account_provider_keys SELECT ?,ciphertext,masked,revision,updated_at FROM account_provider_keys', (self.b.id,))
            db.commit()
        with self.assertRaises(VaultUnavailable):
            self.vault.get(self.b.id)
        self.vault.key_file.unlink()
        with self.assertRaises(VaultUnavailable):
            self.vault.save(self.b.id, 'sk-simulated-other')
        self.assertFalse(self.vault.key_file.exists())

    def test_account_never_falls_back_to_platform_and_rotation_isolated(self):
        providers = ModelCredentials(self.vault, local_key=lambda: 'platform-key')
        with self.assertRaises(MissingProviderKey):
            providers.key(self.a.id)
        self.vault.save(self.a.id, 'sk-simulated-a-first')
        self.vault.save(self.b.id, 'sk-simulated-b-first')
        old = self.vault.status(self.a.id)['revision']
        self.vault.save(self.a.id, 'sk-simulated-a-second')
        self.assertNotEqual(old, self.vault.status(self.a.id)['revision'])
        self.vault.delete(self.a.id)
        with self.assertRaises(MissingProviderKey):
            providers.key(self.a.id)
        self.assertEqual(providers.key(self.b.id), 'sk-simulated-b-first')
        self.assertEqual(ModelCredentials(self.vault, account_mode=False, local_key=lambda: 'local-key').key('local'), 'local-key')


class ProviderRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = server_app.create_app(store=MemoryStore(Path(self.temp.name) / 'memory.sqlite3'), access=AccessSettings('accounts'))
        self.client = TestClient(self.app)
        self.headers, self.users = {}, {}
        for name in ('alice', 'bob'):
            user = self.app.state.identities.create_user(name, name + '-test-password')
            self.users[name] = user
            grant = self.app.state.identities.login(name, name + '-test-password', client_key='fixture')
            self.headers[name] = {'Cookie':f'{SESSION_COOKIE}={grant.token}', 'Origin':'http://testserver', 'X-CSRF-Token':csrf_for_session(grant.token)}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_key_is_never_returned_and_csrf_and_owner_scope_are_enforced(self):
        path = '/api/account/model'
        self.assertEqual(self.client.get(path).status_code, 401)
        key = 'sk-simulated-private-alice'
        bad = {k:v for k,v in self.headers['alice'].items() if k != 'X-CSRF-Token'}
        self.assertEqual(self.client.put(path, headers=bad, json={'api_key':key}).status_code, 403)
        response = self.client.put(path, headers=self.headers['alice'], json={'api_key':key})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(key, response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertFalse(self.client.get(path, headers=self.headers['bob']).json()['configured'])
        self.client.delete(path, headers=self.headers['bob'])
        self.assertTrue(self.client.get(path, headers=self.headers['alice']).json()['configured'])
        self.client.delete(path, headers=self.headers['alice'])
        self.assertFalse(self.client.get(path, headers=self.headers['alice']).json()['configured'])

    def test_background_summary_resolves_conversation_owner(self):
        user = self.users['alice']
        self.app.state.provider_settings.save(user.id, 'sk-simulated-summary-alice')
        store = self.app.state.memory_manager.store
        conversation = store.create_conversation(user.id, 'cat-a')
        store.append_message(conversation, 'user', '模拟记忆')
        with patch('device_runtime.plain_text_async', AsyncMock(return_value='{"summary":"模拟摘要"}')) as call:
            asyncio.run(self.app.state.memory_manager.summarize_now(conversation))
            self.assertEqual(call.call_args.kwargs['api_key'], 'sk-simulated-summary-alice')

    @staticmethod
    def simulated_connections():
        text = MagicMock()
        text.__aenter__ = AsyncMock(return_value=text)
        text.__aexit__ = AsyncMock(return_value=False)
        text.chat.completions.create = AsyncMock()
        realtime = MagicMock()
        realtime.ensure_connected = AsyncMock()
        realtime.close = AsyncMock()
        return text, realtime

    def test_connection_endpoint_works_without_python311_timeout_and_uses_only_owner_key(self):
        # Limit only this module's asyncio surface so the HTTP framework is unaffected.
        py310 = SimpleNamespace(Lock=asyncio.Lock, get_running_loop=asyncio.get_running_loop,
                                wait_for=asyncio.wait_for)
        for name in ('alice', 'bob'):
            with self.subTest(account=name):
                key = 'sk-simulated-private-' + name
                self.app.state.provider_settings.save(self.users[name].id, key)
                text, realtime = self.simulated_connections()
                with patch.object(provider_routes, 'asyncio', py310), \
                     patch('openai.AsyncOpenAI', return_value=text) as text_factory, \
                     patch('omni_realtime_client.OmniRealtimeSession', return_value=realtime) as realtime_factory:
                    response = self.client.post('/api/account/model/test', headers=self.headers[name])
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.headers['cache-control'], 'no-store')
                self.assertNotIn(key, response.text)
                self.assertEqual(text_factory.call_args.kwargs['api_key'], key)
                self.assertEqual(realtime_factory.call_args.kwargs['api_key'], key)
                text.chat.completions.create.assert_awaited_once()
                realtime.ensure_connected.assert_awaited_once()
                realtime.close.assert_awaited_once()

    def test_connection_failure_does_not_echo_provider_secrets_and_closes_realtime(self):
        key = 'sk-simulated-private-alice'
        self.app.state.provider_settings.save(self.users['alice'].id, key)
        for phase in ('text', 'realtime'):
            with self.subTest(phase=phase):
                text, realtime = self.simulated_connections()
                target = text.chat.completions.create if phase == 'text' else realtime.ensure_connected
                target.side_effect = ValueError('untrusted provider output ' + key)
                with patch('openai.AsyncOpenAI', return_value=text), \
                     patch('omni_realtime_client.OmniRealtimeSession', return_value=realtime):
                    response = self.client.post('/api/account/model/test', headers=self.headers['alice'])
                self.assertEqual(response.status_code, 400)
                self.assertNotIn(key, response.text)
                self.assertNotIn('untrusted provider output', response.text)
                if phase == 'realtime':
                    realtime.close.assert_awaited_once()
                else:
                    realtime.close.assert_not_awaited()

    def test_connection_timeout_and_request_cancellation_release_realtime(self):
        self.app.state.provider_settings.save(self.users['alice'].id, 'sk-simulated-private-alice')

        async def run(cancel):
            entered = asyncio.Event()
            text, realtime = self.simulated_connections()
            async def wait_forever():
                entered.set()
                await asyncio.Event().wait()
            realtime.ensure_connected.side_effect = wait_forever
            async def short_wait(awaitable, timeout):
                return await asyncio.wait_for(awaitable, timeout=.02 if timeout == 20 else timeout)
            surface = SimpleNamespace(Lock=asyncio.Lock, get_running_loop=asyncio.get_running_loop,
                                      wait_for=asyncio.wait_for if cancel else short_wait)
            with patch.object(provider_routes, 'asyncio', surface), \
                 patch('openai.AsyncOpenAI', return_value=text), \
                 patch('omni_realtime_client.OmniRealtimeSession', return_value=realtime):
                request = Request({'type':'http', 'method':'POST', 'scheme':'http', 'server':('testserver',80),
                                   'path':'/api/account/model/test', 'app':self.app,
                                   'headers':[(key.lower().encode(), value.encode()) for key,value in self.headers['alice'].items()]})
                task = asyncio.create_task(provider_routes.test(request, Response(), self.users['alice']))
                await asyncio.wait_for(entered.wait(), timeout=2)
                if cancel:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    with self.assertRaises(provider_routes.HTTPException) as result:
                        await task
                    self.assertEqual(result.exception.status_code, 400)
                realtime.close.assert_awaited_once()
                self.assertFalse(provider_routes.lock_for(self.app, self.users['alice'].id).locked())

        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                asyncio.run(run(cancel))

    def test_model_client_receives_explicit_key_in_executor(self):
        import omni_client
        from unittest.mock import MagicMock
        def make(key):
            client = MagicMock()
            client.__enter__.return_value = client
            client.chat.completions.create.return_value.choices[0].message.content = key
            return client
        async def run():
            return await asyncio.gather(omni_client.plain_text_async('a', 'a', api_key='alice-key'),
                                        omni_client.plain_text_async('b', 'b', api_key='bob-key'))
        with patch.object(omni_client, 'make_client', side_effect=make):
            self.assertEqual(asyncio.run(run()), ['alice-key', 'bob-key'])


if __name__ == '__main__':
    unittest.main()
