"""ASGI 入口资源边界；不用真实数据库、代理、硬件或模型。"""

import asyncio
from dataclasses import replace
import unittest

from access_control import AccessSettings
from network_security import NetworkLimits, NetworkSecurityMiddleware, authority


def scope(path='/api/check', *, kind='http', method='POST', headers=(), host='127.0.0.1:8081'):
    result = {'type': kind, 'path': path, 'raw_path': path.encode(), 'query_string': b'',
              'scheme': 'ws' if kind == 'websocket' else 'http', 'method': method,
              'client': ('127.0.0.1', 1000), 'server': ('127.0.0.1', 8081),
              'headers': [(b'host', host.encode()), *headers]}
    return result


async def run(middleware, request, messages=()):
    sent = []
    incoming = list(messages) or [{'type': 'http.request', 'body': b'', 'more_body': False}]
    async def receive():
        if incoming:
            return incoming.pop(0)
        await asyncio.Event().wait()
    async def send(message):
        sent.append(message)
    await middleware(request, receive, send)
    return sent


async def ok(request, receive, send):
    await send({'type': 'http.response.start', 'status': 200, 'headers': []})
    await send({'type': 'http.response.body', 'body': b'OK'})


class NetworkGuardTests(unittest.IsolatedAsyncioTestCase):
    def guard(self, app=ok, mode='accounts', url='', **limits):
        return NetworkSecurityMiddleware(app, settings=AccessSettings(mode, url),
                                         limits=replace(NetworkLimits(), **limits))

    async def test_headers_cover_rejections_without_weakening_existing_csp(self):
        guard = self.guard(mode='local')
        result = await run(guard, scope(headers=[(b'origin', b'https://attacker.invalid')]))
        self.assertEqual(result[0]['status'], 403)
        headers = dict(result[0]['headers'])
        self.assertEqual(headers[b'x-frame-options'], b'DENY')
        self.assertEqual(headers[b'x-content-type-options'], b'nosniff')
        self.assertEqual(headers[b'referrer-policy'], b'no-referrer')
        async def strict(request, receive, send):
            await send({'type': 'http.response.start', 'status': 200,
                        'headers': [(b'content-security-policy', b"default-src 'none'")]})
            await send({'type': 'http.response.body', 'body': b''})
        result = await run(self.guard(strict), scope())
        self.assertEqual(dict(result[0]['headers'])[b'content-security-policy'], b"default-src 'none'")

    async def test_host_and_forwarded_spoofing_are_rejected(self):
        for mode, url in (('local', ''), ('accounts', 'https://cat.example')):
            result = await run(self.guard(mode=mode, url=url), scope(host='attacker.invalid', headers=[
                (b'x-forwarded-host', b'cat.example'), (b'forwarded', b'host=cat.example')]))
            self.assertEqual(result[0]['status'], 400)
        request = scope(host='cat.example')
        request['scheme'] = 'https'
        self.assertEqual((await run(self.guard(url='https://cat.example'), request))[0]['status'], 200)

    async def test_canonical_public_host_allows_only_local_health_probe(self):
        guard = self.guard(url='https://cat.example')
        for path in ('/api/health', '/api/ready'):
            self.assertEqual((await run(guard, scope(path, method='GET')))[0]['status'], 200)
        self.assertEqual((await run(guard, scope('/api/auth/session', method='GET')))[0]['status'], 400)
        request = scope('/api/health', method='GET')
        request['client'] = ('198.51.100.4', 1000)
        self.assertEqual((await run(guard, request))[0]['status'], 400)

    async def test_local_writes_require_origin_or_explicit_client_header(self):
        guard = self.guard(mode='local')
        self.assertEqual((await run(guard, scope()))[0]['status'], 403)
        for headers in ([(b'origin', b'http://127.0.0.1:8081')], [(b'x-cybermiao-request', b'1')]):
            self.assertEqual((await run(guard, scope(headers=headers)))[0]['status'], 200)
        for headers in ([(b'x-cybermiao-request', b'1'), (b'origin', b'null')],
                        [(b'x-cybermiao-request', b'1'), (b'sec-fetch-site', b'cross-site')]):
            self.assertEqual((await run(guard, scope(headers=headers)))[0]['status'], 403)
        request = scope('/', method='GET', headers=[(b'sec-fetch-site', b'cross-site'),
                                                   (b'sec-fetch-mode', b'navigate')])
        self.assertEqual((await run(guard, request))[0]['status'], 200)

    async def test_declared_and_chunked_bodies_are_bounded_before_application(self):
        async def never(*args):
            self.fail('超限请求不得调用业务')
        guard = self.guard(never, body_bytes=4)
        self.assertEqual((await run(guard, scope(headers=[(b'content-length', b'5')])))[0]['status'], 413)
        self.assertEqual((await run(guard, scope(), [{'type': 'http.request', 'body': b'123', 'more_body': True},
            {'type': 'http.request', 'body': b'45', 'more_body': False}]))[0]['status'], 413)
        self.assertEqual((await run(guard, scope(headers=[(b'content-length', b'9' * 5000)])))[0]['status'], 413)

    async def test_valid_body_is_replayed_and_length_ambiguity_rejected(self):
        async def echo(request, receive, send):
            self.assertEqual((await receive())['body'], b'1234')
            await ok(request, receive, send)
        result = await run(self.guard(echo, body_bytes=4), scope(headers=[(b'content-length', b'4')]), [
            {'type': 'http.request', 'body': b'12', 'more_body': True},
            {'type': 'http.request', 'body': b'34', 'more_body': False}])
        self.assertEqual(result[0]['status'], 200)
        for headers in ([(b'content-length', b'1'), (b'content-length', b'1')],
                        [(b'content-length', b'1'), (b'transfer-encoding', b'chunked')],
                        [(b'content-length', b'-1')], [(b'content-length', b'2')]):
            self.assertEqual((await run(self.guard(), scope(headers=headers)))[0]['status'], 400)

    async def test_slow_body_and_header_url_bounds(self):
        guard = self.guard(body_timeout=.01, header_count=2, url_bytes=20)
        request = scope()
        incoming = [{'type': 'http.request', 'body': b'a', 'more_body': True}]
        self.assertEqual((await run(guard, request, incoming))[0]['status'], 408)
        self.assertEqual((await run(guard, scope(headers=[(b'a', b'1'), (b'b', b'2')])))[0]['status'], 431)
        self.assertEqual((await run(guard, scope('/' + 'x' * 21)))[0]['status'], 414)

    async def test_http_concurrency_is_released_after_cancellation(self):
        entered = asyncio.Event()
        async def held(request, receive, send):
            entered.set()
            await asyncio.Event().wait()
        guard = self.guard(held, http_connections=1)
        first = asyncio.create_task(run(guard, scope()))
        await asyncio.wait_for(entered.wait(), 1)
        result = await run(guard, scope())
        self.assertEqual(result[0]['status'], 503)
        self.assertEqual(dict(result[0]['headers'])[b'retry-after'], b'1')
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        self.assertEqual(guard.http_active, 0)

    async def test_local_browser_socket_origin_and_native_hardware_compatibility(self):
        async def socket(request, receive, send):
            await send({'type': 'websocket.accept'})
            await send({'type': 'websocket.close', 'code': 1000})
        guard = self.guard(socket, mode='local')
        for path in ('/ws_ui', '/ws/viewer', '/ws/devices/cat-test/ui'):
            denied = await run(guard, scope(path, kind='websocket'))
            self.assertEqual(denied[0]['code'], 4403)
        for path in ('/ws_audio', '/ws/camera'):
            allowed = await run(guard, scope(path, kind='websocket'))
            self.assertEqual(allowed[0]['type'], 'websocket.accept')

    async def test_socket_limits_count_utf8_bytes_and_suppress_late_send(self):
        async def socket(request, receive, send):
            await send({'type': 'websocket.accept'})
            try:
                await receive()
            finally:
                await send({'type': 'websocket.send', 'text': '不得在关闭后发送'})
        guard = self.guard(socket, websocket_text_bytes=4, websocket_audio_bytes=4, websocket_camera_bytes=8)
        for path, message in (('/ws_ui', {'text': '猫猫'}), ('/ws_audio', {'bytes': b'12345'}),
                              ('/ws/camera', {'bytes': b'123456789'})):
            result = await run(guard, scope(path, kind='websocket'), [{'type': 'websocket.receive', **message}])
            self.assertEqual([item['type'] for item in result], ['websocket.accept', 'websocket.close'])
            self.assertEqual(result[-1]['code'], 1009)
        self.assertEqual(guard.websocket_active, 0)

    async def test_websocket_concurrency_and_cancellation_release_slot(self):
        entered = asyncio.Event()
        async def held(request, receive, send):
            entered.set()
            await asyncio.Event().wait()
        guard = self.guard(held, websocket_connections=1)
        first = asyncio.create_task(run(guard, scope('/ws_audio', kind='websocket')))
        await asyncio.wait_for(entered.wait(), 1)
        self.assertEqual((await run(guard, scope('/ws_audio', kind='websocket')))[0]['code'], 1013)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        self.assertEqual(guard.websocket_active, 0)

    def test_authority_and_limits_fail_closed(self):
        self.assertEqual(authority('[::1]:8081', 'http'), ('::1', 8081))
        for value in ('a:0', 'a:65536', 'a:', 'a/a', 'user@a', 'a\\b', 'a\n', 'a?x=1', 'a#x', 'a:080'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                authority(value, 'http')
        for limits in ({'body_bytes': 0}, {'http_connections': True}, {'body_timeout': float('inf')}):
            with self.assertRaises(ValueError):
                NetworkLimits(**limits)
