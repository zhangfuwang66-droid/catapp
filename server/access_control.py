"""本地兼容范围、服务端登录会话与同源写操作校验。"""

import asyncio
from dataclasses import dataclass
import ipaddress
import os
import secrets
from urllib.parse import urlsplit

from fastapi import HTTPException, WebSocketException
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse

from identity_store import User, csrf_for_session
from device_identity import validate_device_id
from connection_authorization import DeviceAuthorization

SESSION_COOKIE = "cybermiao_session"
LOCAL_NETWORKS = tuple(ipaddress.ip_network(network) for network in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "::1/128", "fc00::/7",
))


@dataclass(frozen=True)
class AccessSettings:
    mode: str = "local"
    public_url: str = ""

    def __post_init__(self):
        if self.mode not in {"local", "accounts"}:
            raise ValueError("CYBERMIAO_ACCESS_MODE 仅支持 local 或 accounts")
        if self.public_url:
            parsed = urlsplit(self.public_url)
            if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username
                    or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
                raise ValueError("CYBERMIAO_PUBLIC_URL 须为不含路径或凭据的 http/https 站点地址")

    @classmethod
    def from_environment(cls):
        return cls(os.getenv("CYBERMIAO_ACCESS_MODE", "local"), os.getenv("CYBERMIAO_PUBLIC_URL", "").rstrip("/"))


def is_local_client(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return any(address.version == network.version and address in network for network in LOCAL_NETWORKS)


class LocalAccessMiddleware:
    def __init__(self, app, *, settings: AccessSettings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        if self.settings.mode == "local" and scope["type"] in {"http", "websocket"}:
            client = scope.get("client")
            if not client or not is_local_client(client[0]):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 4403})
                else:
                    response = JSONResponse({"detail": "本地兼容模式仅允许本机和私有局域网访问"}, status_code=403)
                    await response(scope, receive, send)
                return
        async def private_send(message):
            if scope["type"] == "http" and message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                if not any(key.lower() == b"cache-control" for key, value in headers):
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, private_send)


def reject(connection: HTTPConnection, *, status: int, detail: str):
    if connection.scope["type"] == "websocket":
        raise WebSocketException(code=4401 if status == 401 else 4403, reason=detail)
    raise HTTPException(status_code=status, detail=detail)


def require_origin(connection: HTTPConnection):
    settings = connection.app.state.access
    expected = settings.public_url or f"{connection.url.scheme}://{connection.url.netloc}"
    # WebSocket URL 的协议转换成对应网页来源。
    expected = expected.replace("wss://", "https://", 1).replace("ws://", "http://", 1)
    origin = connection.headers.get("origin", "")
    if origin.rstrip("/") != expected.rstrip("/"):
        reject(connection, status=403, detail="请求来源与当前站点不一致")
    if connection.headers.get("sec-fetch-site", "") == "cross-site":
        reject(connection, status=403, detail="拒绝跨站请求")


async def require_session(connection: HTTPConnection) -> User:
    token = connection.cookies.get(SESSION_COOKIE, "")
    user = await asyncio.to_thread(connection.app.state.identities.session_user, token)
    if user is None:
        reject(connection, status=401, detail="请先登录，或重新登录已过期的账号")
    if connection.scope["type"] == "websocket":
        require_origin(connection)
    return user


async def require_session_write(connection: HTTPConnection) -> User:
    user = await require_session(connection)
    require_origin(connection)
    expected = csrf_for_session(connection.cookies.get(SESSION_COOKIE, ""))
    supplied = connection.headers.get("x-csrf-token", "")
    if not secrets.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8")):
        reject(connection, status=403, detail="请求校验失败，请刷新页面后重试")
    return user


def bearer_token(connection: HTTPConnection) -> str:
    value = connection.headers.get("authorization", "")
    scheme, separator, token = value.partition(" ")
    if not separator or scheme.lower() != "bearer" or not 32 <= len(token) <= 128:
        reject(connection, status=401, detail="设备凭证缺失或无效")
    return token


async def require_device_credential(connection: HTTPConnection) -> dict:
    device_id = connection.path_params.get("device_id", "")
    try:
        validate_device_id(device_id)
    except ValueError:
        reject(connection, status=404, detail="设备不存在")
    token = bearer_token(connection)
    device = await asyncio.to_thread(connection.app.state.identities.credential_device, device_id, token)
    if device is None:
        reject(connection, status=401, detail="设备凭证缺失或无效")
    return device


async def authorize_runtime(connection: HTTPConnection, *, hardware: bool = False, playback: bool = False):
    if connection.app.state.access.mode == "local":
        device_id = connection.path_params.get("device_id", connection.app.state.default_device_id)
        return device_id, connection.app.state.default_user_id
    device_id = connection.path_params.get("device_id")
    if device_id is None:
        reject(connection, status=404, detail="账号模式已关闭旧版匿名接口")
    try:
        validate_device_id(device_id)
    except ValueError:
        reject(connection, status=404, detail="设备不存在")
    if hardware or (playback and connection.headers.get("authorization")):
        device = await require_device_credential(connection)
        user_id = device["owner_user_id"]
        if user_id is None:
            reject(connection, status=409, detail="设备尚未绑定账号，请先配对")
        authorization = DeviceAuthorization("device", user_id, device_id, bearer_token(connection))
    else:
        if connection.scope["type"] == "http" and connection.scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            user = await require_session_write(connection)
        else:
            user = await require_session(connection)
        device = await asyncio.to_thread(connection.app.state.identities.owned_device, device_id, user.id)
        if device is None:
            reject(connection, status=404, detail="设备不存在或无权访问")
        user_id = user.id
        authorization = DeviceAuthorization("session", user_id, device_id, connection.cookies[SESSION_COOKIE])
    connection.state.device_authorization = authorization
    return device_id, user_id
