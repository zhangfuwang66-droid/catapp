"""重复安全头、DNS/IP 主机和框架外层 500 的独立回归；仅用临时数据。"""

import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from access_control import AccessSettings
from database_backups import BackupSettings
from memory_store import MemoryStore, SummaryResult
from network_security import NetworkSecurityMiddleware, authority

with patch('dotenv.load_dotenv', return_value=False):
    from support import server_app


def request_scope(path='/api/check', *, kind='http', method='POST', headers=(), host='127.0.0.1:8081'):
    return {
        'type': kind, 'asgi': {'version': '3.0', 'spec_version': '2.4'}, 'http_version': '1.1',
        'path': path, 'raw_path': path.encode(), 'root_path': '', 'query_string': b'',
        'scheme': 'ws' if kind == 'websocket' else 'http', 'method': method,
        'client': ('127.0.0.1', 1000), 'server': ('127.0.0.1', 8081),
        'headers': [(b'host', host.encode()), *headers],
    }


async def success(scope, receive, send):
    if scope['type'] == 'websocket':
        await send({'type': 'websocket.accept'})
        await send({'type': 'websocket.close', 'code': 1000})
    else:
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'OK'})


async def capture(application, scope, sent=None):
    sent = [] if sent is None else sent
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        await asyncio.Event().wait()

    async def send(message):
        sent.append(message)

    await application(scope, receive, send)
    return sent


class HeaderBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def reject_before_body(self, headers, *, mode='accounts', path='/api/check', kind='http', method='POST'):
        async def unexpected(*args):
            self.fail('歧义请求不得读取正文或调用业务')

        sent = []

        async def send(message):
            sent.append(message)

        guard = NetworkSecurityMiddleware(unexpected, settings=AccessSettings(mode))
        scope = request_scope(path, kind=kind, method=method, headers=headers)
        await guard(scope, unexpected, send)
        if kind == 'websocket':
            self.assertEqual(sent, [{'type': 'websocket.close', 'code': 4403, 'reason': '请求头存在歧义'}])
        else:
            self.assertEqual(sent[0]['status'], 400)
            self.assertEqual(dict(sent[0]['headers'])[b'x-content-type-options'], b'nosniff')

    async def test_duplicate_content_encoding_rejected_in_both_orders_and_modes(self):
        for mode in ('local', 'accounts'):
            for path in ('/api/check', '/api/knowledge/documents'):
                for encodings in ((b'identity', b'gzip'), (b'gzip', b'identity'), (b'identity', b'identity')):
                    with self.subTest(mode=mode, path=path, encodings=encodings):
                        await self.reject_before_body([
                            (b'content-encoding', encodings[0]), (b'Content-Encoding', encodings[1]),
                            (b'x-cybermiao-request', b'1'),
                        ], mode=mode, path=path)

    async def test_single_identity_and_absent_encoding_still_work(self):
        guard = NetworkSecurityMiddleware(success, settings=AccessSettings('accounts'))
        for headers in ([], [(b'content-encoding', b'identity')], [(b'content-encoding', b'IDENTITY')]):
            with self.subTest(headers=headers):
                self.assertEqual((await capture(guard, request_scope(headers=headers)))[0]['status'], 200)
        for encoding in (b'gzip', b'identity, gzip', b'identity, identity'):
            with self.subTest(encoding=encoding):
                sent = await capture(guard, request_scope(headers=[(b'content-encoding', encoding)]))
                self.assertEqual(sent[0]['status'], 415)

    async def test_duplicate_fetch_site_cannot_bypass_local_http_or_websocket_policy(self):
        for sites in ((b'same-origin', b'cross-site'), (b'cross-site', b'same-origin'),
                      (b'same-origin', b'same-origin')):
            for kind, path, method in (('http', '/api/check', 'GET'), ('http', '/api/check', 'POST'),
                                       ('http', '/', 'GET'), ('websocket', '/ws_audio', 'GET'),
                                       ('websocket', '/ws_ui', 'GET')):
                with self.subTest(sites=sites, kind=kind, path=path, method=method):
                    await self.reject_before_body([
                        (b'sec-fetch-site', sites[0]), (b'Sec-Fetch-Site', sites[1]),
                        (b'sec-fetch-mode', b'navigate'), (b'origin', b'http://127.0.0.1:8081'),
                        (b'x-cybermiao-request', b'1'),
                    ], mode='local', path=path, kind=kind, method=method)

    async def test_combined_or_malformed_fetch_site_is_rejected(self):
        guard = NetworkSecurityMiddleware(success, settings=AccessSettings('local'))
        for site in (b'same-origin, cross-site', b'cross-site, same-origin', b'same-origin same-site', b''):
            with self.subTest(site=site):
                sent = await capture(guard, request_scope(method='GET', headers=[(b'sec-fetch-site', site)]))
                self.assertEqual(sent[0]['status'], 400)

    async def test_accounts_mode_also_rejects_ambiguous_fetch_site_before_authentication(self):
        for kind in ('http', 'websocket'):
            with self.subTest(kind=kind):
                await self.reject_before_body([
                    (b'sec-fetch-site', b'same-origin'), (b'sec-fetch-site', b'cross-site'),
                ], kind=kind)

    async def test_valid_fetch_metadata_and_safe_navigation_are_preserved(self):
        guard = NetworkSecurityMiddleware(success, settings=AccessSettings('local'))
        for site in (b'none', b'same-origin'):
            self.assertEqual((await capture(guard, request_scope(method='GET', headers=[
                (b'sec-fetch-site', site)])))[0]['status'], 200)
        for site in (b'same-site', b'cross-site'):
            headers = [(b'sec-fetch-site', site), (b'sec-fetch-mode', b'navigate')]
            self.assertEqual((await capture(guard, request_scope('/', method='GET', headers=headers)))[0]['status'], 200)
            self.assertEqual((await capture(guard, request_scope(method='GET', headers=headers)))[0]['status'], 403)


INVALID_HOSTS = (
    'bad%.example', 'bad|host.example', 'two..labels.example', '-bad.example', 'bad-.example',
    '.example', '.', 'under_score.example', 'bad,host.example', 'a' * 64 + '.example',
    '.'.join(['a' * 63] * 4), '256.0.0.1', '127.000.0.1', '127.1', '[fe80::1%25zone]', '[v1.a]',
)
VALID_AUTHORITIES = (
    ('localhost', 'http', ('localhost', 80)),
    ('testserver', 'http', ('testserver', 80)),
    ('CAT.Example:8443', 'https', ('cat.example', 8443)),
    ('cat.example.', 'https', ('cat.example.', 443)),
    ('xn--fiqs8s.example', 'https', ('xn--fiqs8s.example', 443)),
    ('192.168.1.20:8081', 'http', ('192.168.1.20', 8081)),
    ('[::1]:8081', 'ws', ('::1', 8081)),
    ('[2001:DB8::1]', 'wss', ('2001:db8::1', 443)),
    ('[::ffff:127.0.0.1]', 'http', ('::ffff:127.0.0.1', 80)),
    ('.'.join(['a' * 63] * 3 + ['b' * 61]), 'http', ('.'.join(['a' * 63] * 3 + ['b' * 61]), 80)),
)


class AuthorityBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def test_authority_rejects_invalid_dns_labels_and_ip_literals(self):
        for host in INVALID_HOSTS:
            with self.subTest(host=host), self.assertRaises(ValueError):
                authority(host, 'http')

    def test_valid_dns_and_ip_authorities_keep_existing_ports_and_case_rules(self):
        for value, scheme, expected in VALID_AUTHORITIES:
            with self.subTest(value=value, scheme=scheme):
                self.assertEqual(authority(value, scheme), expected)

    async def test_accounts_without_public_url_validates_host_before_business(self):
        async def unexpected(*args):
            self.fail('非法 Host 不得调用业务')

        guard = NetworkSecurityMiddleware(unexpected, settings=AccessSettings('accounts'))
        for host in INVALID_HOSTS:
            for kind in ('http', 'websocket'):
                with self.subTest(host=host, kind=kind):
                    sent = await capture(guard, request_scope(host=host, kind=kind))
                    self.assertEqual(sent[0].get('status', sent[0].get('code')), 400 if kind == 'http' else 4403)

    async def test_accounts_development_accepts_valid_dns_and_ip_hosts(self):
        guard = NetworkSecurityMiddleware(success, settings=AccessSettings('accounts'))
        for host, _, _ in VALID_AUTHORITIES:
            with self.subTest(host=host):
                self.assertEqual((await capture(guard, request_scope(host=host)))[0]['status'], 200)


async def summarize(_messages):
    return SummaryResult(summary='simulated-network-boundary-only')


class FrameworkErrorBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='network-error-boundary-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        with patch.dict(os.environ, {'CYBERMIAO_SECRET_KEY_FILE': str(root / 'unused-simulated.key')}):
            self.app = server_app.create_app(
                store=MemoryStore(root / 'memory.sqlite3'), summarizer=summarize,
                access=AccessSettings('accounts', 'https://cat.example'), backup_settings=BackupSettings())
        self.failure = RuntimeError('simulated-exception-detail-must-not-reach-client')

        @self.app.get('/_network/raise')
        async def fail():
            raise self.failure

        @self.app.get('/_network/stream')
        async def stream():
            async def content():
                yield b'partial-response'
                raise self.failure
            return StreamingResponse(content())

    def error_scope(self, path='/_network/raise'):
        scope = request_scope(path, host='cat.example', method='GET')
        scope['scheme'] = 'https'
        return scope

    def assert_secure(self, raw_headers, *, hsts=True, csp=None):
        headers = {name.lower(): value for name, value in raw_headers}
        expected = {
            b'x-content-type-options': b'nosniff', b'x-frame-options': b'DENY',
            b'referrer-policy': b'no-referrer', b'cache-control': b'no-store',
            b'permissions-policy': b'camera=(self), microphone=(self), geolocation=(), payment=()',
            b'content-security-policy': (csp or "frame-ancestors 'none'; object-src 'none'; base-uri 'self'").encode(),
        }
        if hsts:
            expected[b'strict-transport-security'] = b'max-age=86400'
        else:
            self.assertNotIn(b'strict-transport-security', headers)
        for name, value in expected.items():
            self.assertEqual(headers.get(name), value, name)
            self.assertEqual(sum(key.lower() == name for key, _ in raw_headers), 1, name)

    async def test_outer_500_has_all_security_headers_and_reraises_original_exception(self):
        sent = []
        with self.assertRaises(RuntimeError) as raised:
            await capture(self.app, self.error_scope(), sent)
        self.assertIs(raised.exception, self.failure)
        self.assertEqual([item['type'] for item in sent], ['http.response.start', 'http.response.body'])
        self.assertEqual(sent[0]['status'], 500)
        self.assert_secure(sent[0]['headers'])
        self.assertEqual(sent[1]['body'], b'Internal Server Error')

    async def test_failure_outside_network_guard_uses_same_safe_500(self):
        failure = self.failure

        class FailingMiddleware:
            def __init__(self, app):
                self.app = app

            async def __call__(self, scope, receive, send):
                raise failure

        self.app.add_middleware(FailingMiddleware)
        sent = []
        with self.assertRaises(RuntimeError) as raised:
            await capture(self.app, self.error_scope(), sent)
        self.assertIs(raised.exception, failure)
        self.assertEqual(sent[0]['status'], 500)
        self.assert_secure(sent[0]['headers'])
        self.assertEqual(sent[1]['body'], b'Internal Server Error')

    async def test_started_stream_is_not_replaced_by_another_response(self):
        sent = []
        with self.assertRaises(RuntimeError) as raised:
            await capture(self.app, self.error_scope('/_network/stream'), sent)
        self.assertIs(raised.exception, self.failure)
        self.assertEqual([item['type'] for item in sent], ['http.response.start', 'http.response.body'])
        self.assertEqual(sent[0]['status'], 200)
        self.assert_secure(sent[0]['headers'])
        self.assertEqual(sent[1]['body'], b'partial-response')

    async def test_500_hsts_follows_current_access_settings_and_request_scheme(self):
        for public_url, scheme, hsts in (('https://cat.example', 'http', False),
                                         ('http://cat.example', 'http', False),
                                         ('', 'https', False), ('https://cat.example', 'https', True)):
            with self.subTest(public_url=public_url, scheme=scheme):
                self.app.state.access = AccessSettings('accounts', public_url)
                scope = self.error_scope()
                scope['scheme'] = scheme
                if public_url.startswith('https://') and scheme == 'http':
                    scope['headers'] = [(b'host', b'cat.example:443')]
                sent = []
                with self.assertRaises(RuntimeError):
                    await capture(self.app, scope, sent)
                self.assertEqual(sent[0]['status'], 500)
                self.assert_secure(sent[0]['headers'], hsts=hsts)

    async def test_admin_login_keeps_strict_csp_without_duplicate_headers(self):
        sent = await capture(self.app, self.error_scope('/admin/login'))
        self.assertEqual(sent[0]['status'], 200)
        self.assert_secure(sent[0]['headers'], csp=server_app.ADMIN_CSP)

    def test_testclient_receives_sanitized_500_and_can_still_raise_server_exceptions(self):
        with TestClient(self.app, base_url='https://cat.example', raise_server_exceptions=False) as client:
            response = client.get('/_network/raise')
            self.assertEqual(response.status_code, 500)
            self.assertEqual(response.text, 'Internal Server Error')
            self.assert_secure(response.headers.raw)
        with TestClient(self.app, base_url='https://cat.example') as client:
            with self.assertRaises(RuntimeError) as raised:
                client.get('/_network/raise')
            self.assertIs(raised.exception, self.failure)


if __name__ == '__main__':
    unittest.main()
