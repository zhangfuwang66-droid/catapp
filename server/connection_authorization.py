"""持续检查长连接权限，退出或停用后结束相应订阅与音频流。"""

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Callable, Optional

from fastapi import WebSocket
from fastapi.responses import StreamingResponse

from identity_store import IdentityStore, token_digest


@dataclass(frozen=True)
class DeviceAuthorization:
    kind: str
    user_id: str
    device_id: str
    token: str = field(repr=False)

    async def is_valid(self, store: IdentityStore) -> bool:
        if self.kind == "device":
            device = await asyncio.to_thread(store.credential_device, self.device_id, self.token)
            return bool(device and device["owner_user_id"] == self.user_id)
        user = await asyncio.to_thread(store.session_user, self.token)
        if user is None or user.id != self.user_id:
            return False
        return await asyncio.to_thread(store.owned_device, self.device_id, self.user_id) is not None


@dataclass(eq=False)
class ConnectionLease:
    authorization: DeviceAuthorization
    task: asyncio.Task
    ws: Optional[WebSocket] = None


class ConnectionAuthorizer:
    def __init__(self, store: IdentityStore, *, poll_seconds: float = 1.0):
        self.store = store
        self.poll_seconds = poll_seconds
        self.leases: set[ConnectionLease] = set()

    async def _stop(self, lease: ConnectionLease):
        if lease.ws is not None:
            with suppress(Exception):
                await asyncio.wait_for(lease.ws.close(code=4401, reason="连接权限已失效"), timeout=1)
        if not lease.task.done():
            lease.task.cancel()
        await asyncio.gather(lease.task, return_exceptions=True)

    async def _watch(self, lease: ConnectionLease):
        while not lease.task.done():
            await asyncio.sleep(self.poll_seconds)
            try:
                valid = await lease.authorization.is_valid(self.store)
            except Exception:
                valid = False
            if not valid:
                await self._stop(lease)
                return

    async def run(self, authorization: Optional[DeviceAuthorization], handler: Callable,
                  *, ws: Optional[WebSocket] = None):
        if authorization is None:
            return await handler()
        if not await authorization.is_valid(self.store):
            if ws is not None:
                await ws.close(code=4401, reason="连接权限已失效")
            return
        task = asyncio.create_task(handler())
        lease = ConnectionLease(authorization, task, ws)
        self.leases.add(lease)
        watcher = asyncio.create_task(self._watch(lease))
        try:
            return await task
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
        finally:
            self.leases.discard(lease)
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)

    async def revoke_session(self, token: str):
        digest = token_digest(token)
        leases = [lease for lease in self.leases if lease.authorization.kind == "session"
                  and token_digest(lease.authorization.token) == digest]
        await asyncio.gather(*(self._stop(lease) for lease in leases))

    async def revoke_device(self, device_id: str):
        leases = [lease for lease in self.leases if lease.authorization.device_id == device_id]
        await asyncio.gather(*(self._stop(lease) for lease in leases))

    async def close(self):
        await asyncio.gather(*(self._stop(lease) for lease in list(self.leases)))


class AuthorizedStreamingResponse(StreamingResponse):
    def __init__(self, response: StreamingResponse, authorizer: ConnectionAuthorizer,
                 authorization: DeviceAuthorization):
        super().__init__(response.body_iterator, status_code=response.status_code,
                         media_type=response.media_type, headers=dict(response.headers))
        self.authorizer = authorizer
        self.authorization = authorization

    async def stream_response(self, send):
        started = False
        finished = False

        async def tracked_send(message):
            nonlocal started, finished
            if message["type"] == "http.response.start":
                started = True
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                finished = True
            await send(message)

        async def produce():
            await super(AuthorizedStreamingResponse, self).stream_response(tracked_send)

        try:
            await self.authorizer.run(self.authorization, produce)
        finally:
            await self.body_iterator.aclose()
        if not started:
            await send({"type": "http.response.start", "status": 401, "headers": []})
        if not finished:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
