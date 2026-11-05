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
        await self.app(scope, receive, send)


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
