"""应用入口的有界请求与浏览器边界；不信任客户端提供的代理地址头。"""

import asyncio
from dataclasses import dataclass
import math
from urllib.parse import urlsplit

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect

from access_control import is_local_client


@dataclass(frozen=True)
class NetworkLimits:
    body_bytes: int = 64 * 1024
    upload_bytes: int = 20 * 1024 * 1024
    header_bytes: int = 32 * 1024
    header_count: int = 100
    url_bytes: int = 8192
    http_connections: int = 64
    websocket_connections: int = 128
    websocket_text_bytes: int = 16 * 1024
    websocket_audio_bytes: int = 256 * 1024
    websocket_camera_bytes: int = 1024 * 1024
    body_timeout: float = 10.0

    def __post_init__(self):
        for name, value in vars(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError('网络限额必须是有限正数')
            if name != 'body_timeout' and not isinstance(value, int):
                raise ValueError('网络容量限额必须为整数')


def authority(value, scheme):
    """只接受单一规范 Host；不接受用户信息、路径、空端口或控制字符。"""
    if not value or any(char.isspace() or ord(char) < 33 or ord(char) > 126 for char in value):
        raise ValueError()
    parsed = urlsplit('http://' + value)
    if (not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment or '\\' in value or value.endswith(':')):
        raise ValueError()
    port = parsed.port
    if port is not None and not 1 <= port <= 65535:
        raise ValueError()
    host = parsed.hostname.lower()
    expected = ('[' + host + ']') if ':' in host else host
    if value.lower() != expected + (':' + str(port) if port is not None else ''):
        raise ValueError()
    return host, port if port is not None else (443 if scheme in {'https', 'wss'} else 80)


class NetworkSecurityMiddleware:
    def __init__(self, app, *, settings, limits=None):
        self.app, self.settings = app, settings
        self.limits = limits or NetworkLimits()
        self.http_active = self.websocket_active = 0

    async def __call__(self, scope, receive, send):
        kind = scope['type']
        if kind not in {'http', 'websocket'}:
            return await self.app(scope, receive, send)
        settings = getattr(getattr(scope.get('app'), 'state', None), 'access', self.settings)

        async def protected_send(message):
            if message['type'] == 'http.response.start':
                headers = list(message.get('headers', []))
                names = {key.lower() for key, _ in headers}
                defaults = {
                    b'x-content-type-options': b'nosniff', b'x-frame-options': b'DENY',
                    b'referrer-policy': b'no-referrer',
                    b'permissions-policy': b'camera=(self), microphone=(self), geolocation=(), payment=()',
                    b'content-security-policy': b"frame-ancestors 'none'; object-src 'none'; base-uri 'self'",
                    b'cache-control': b'no-store',
                }
                if scope.get('scheme') == 'https' and settings.public_url.startswith('https://'):
                    defaults[b'strict-transport-security'] = b'max-age=86400'
                headers.extend((key, value) for key, value in defaults.items() if key not in names)
                message = {**message, 'headers': headers}
            await send(message)

        async def deny(status, detail, *, retry=False):
            if kind == 'websocket':
                await send({'type': 'websocket.close', 'code': 1013 if retry else 4403, 'reason': detail})
            else:
                response = JSONResponse({'detail': detail}, status_code=status,
                                        headers={'Retry-After': '1'} if retry else None)
                await response(scope, receive, protected_send)

        headers = scope.get('headers', [])
        if (len(headers) > self.limits.header_count
                or sum(len(key) + len(value) for key, value in headers) > self.limits.header_bytes):
            return await deny(431, '请求头过大')
        if len(scope.get('raw_path', scope['path'].encode())) + len(scope.get('query_string', b'')) > self.limits.url_bytes:
            return await deny(414, '请求地址过长')
        values = {}
        for key, value in headers:
            values.setdefault(key.lower(), []).append(value.decode('latin-1'))
        if len(values.get(b'host', [])) != 1:
            return await deny(400, '请求主机格式无效')
        try:
            host = authority(values[b'host'][0], scope.get('scheme', 'http'))
            public = urlsplit(settings.public_url) if settings.public_url else None
            configured = authority(public.netloc, public.scheme) if public else None
        except ValueError:
            return await deny(400, '请求主机格式无效')
        if configured and host != configured:
            # 反代之后仍允许同机健康探针；其他路由不能借此绕过规范站点。
            client = scope.get('client')
            probe = (kind == 'http' and scope['path'] in {'/api/health', '/api/ready'}
                     and scope['method'] in {'GET', 'HEAD'} and client
                     and client[0] in {'127.0.0.1', '::1'}
                     and host[0] in {'127.0.0.1', '::1', 'localhost'})
            if not probe:
                return await deny(400, '请求主机不在允许范围')
        if settings.mode == 'local' and not configured:
            bound_host = (scope.get('server') or ('', 0))[0].lower()
            # scope.server 是 ASGI 服务器的监听地址，不是 Host/Forwarded 输入。
            if host[0] not in {'localhost', bound_host} and not is_local_client(host[0]):
                return await deny(400, '本地模式不接受此主机地址')
        if any(len(values.get(key, [])) > 1 for key in (b'origin', b'content-length', b'transfer-encoding')):
            return await deny(400, '请求头存在歧义')
        if values.get(b'content-length') and values.get(b'transfer-encoding'):
            return await deny(400, '请求长度存在歧义')
        if settings.mode == 'local':
            origin = values.get(b'origin', [''])[0]
            scheme = {'ws': 'http', 'wss': 'https'}.get(scope.get('scheme'), scope.get('scheme', 'http'))
            expected = settings.public_url or scheme + '://' + values[b'host'][0]
            if origin and origin.rstrip('/') != expected.rstrip('/'):
                return await deny(403, '拒绝跨站设备请求')
            safe_navigation = (kind == 'http' and scope['method'] in {'GET', 'HEAD'}
                               and scope['path'] in {'/', '/knowledge', '/docs', '/redoc'}
                               and values.get(b'sec-fetch-mode', [''])[0] == 'navigate')
            if values.get(b'sec-fetch-site', [''])[0] in {'cross-site', 'same-site'} and not safe_navigation:
                return await deny(403, '拒绝跨站设备请求')
            browser_socket = (scope['path'] in {'/ws_ui', '/ws/viewer'}
                              or scope['path'].endswith(('/ui', '/viewer')))
            if kind == 'websocket' and browser_socket and not origin:
                return await deny(403, '网页控制连接需要同源来源')
            if (kind == 'http' and scope['method'] not in {'GET', 'HEAD', 'OPTIONS'}
                    and not origin and values.get(b'x-cybermiao-request', [''])[0] != '1'):
                return await deny(403, '本地写请求需要同源来源或客户端校验头')
        if kind == 'websocket':
            if self.websocket_active >= self.limits.websocket_connections:
                return await deny(503, '设备连接繁忙，请稍后重试', retry=True)
            self.websocket_active += 1
            closed = False

            async def socket_send(message):
                nonlocal closed
                if closed:
                    return
                if message['type'] == 'websocket.close':
                    closed = True
                await send(message)

            async def socket_receive():
                nonlocal closed
                message = await receive()
                if message['type'] == 'websocket.receive':
                    text, data = message.get('text'), message.get('bytes')
                    binary_limit = (self.limits.websocket_camera_bytes if scope['path'].endswith('/camera')
                                    else self.limits.websocket_audio_bytes)
                    if ((text is not None and len(text.encode('utf-8')) > self.limits.websocket_text_bytes)
                            or (data is not None and len(data) > binary_limit)):
                        await socket_send({'type': 'websocket.close', 'code': 1009, 'reason': '消息超过通道容量限制'})
                        raise WebSocketDisconnect(1009)
                return message

            try:
                await self.app(scope, socket_receive, socket_send)
            except WebSocketDisconnect:
                pass
            finally:
                self.websocket_active -= 1
            return
        if self.http_active >= self.limits.http_connections:
            return await deny(503, '服务繁忙，请稍后重试', retry=True)
        self.http_active += 1
        try:
            upload = scope['method'] == 'POST' and scope['path'] == '/api/knowledge/documents'
            limit = self.limits.upload_bytes if upload else self.limits.body_bytes
            length = values.get(b'content-length', [None])[0]
            if length is not None and (not length.isascii() or not length.isdecimal()):
                return await deny(400, '请求长度格式无效')
            if length is not None and len(length) > 10:
                return await deny(413, '请求内容超过容量限制')
            declared = int(length) if length is not None else None
            if declared is not None and declared > limit:
                return await deny(413, '请求内容超过容量限制')
            if values.get(b'content-encoding', ['identity'])[0].lower() != 'identity':
                return await deny(415, '不支持此请求内容编码')
            if upload:
                # 上传接口已在鉴权之后逐块计数，限定并发与60秒总上传时间，避免双份缓冲。
                received = 0
                async def upload_receive():
                    nonlocal received
                    message = await receive()
                    if message['type'] == 'http.request':
                        received += len(message.get('body', b''))
                        if received > limit:
                            raise HTTPException(413, '请求内容超过容量限制')
                    return message
                return await self.app(scope, upload_receive, protected_send)
            chunks, total = [], 0

            async def read_body():
                nonlocal total
                while True:
                    message = await receive()
                    if message['type'] == 'http.disconnect':
                        return False
                    chunk = message.get('body', b'')
                    total += len(chunk)
                    if total > limit:
                        return 413
                    chunks.append(chunk)
                    if not message.get('more_body', False):
                        return True

            try:
                result = await asyncio.wait_for(read_body(), timeout=self.limits.body_timeout)
            except asyncio.TimeoutError:
                return await deny(408, '接收请求内容超时')
            if result is False:
                return
            if result == 413:
                return await deny(413, '请求内容超过容量限制')
            if declared is not None and total != declared:
                return await deny(400, '请求长度与实际内容不一致')
            body, delivered = b''.join(chunks), False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {'type': 'http.request', 'body': body, 'more_body': False}
                return await receive()

            await self.app(scope, replay, protected_send)
        finally:
            self.http_active -= 1
