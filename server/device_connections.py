"""串行替换同一设备的连接，先完成旧会话清理再启动新会话。"""

import asyncio
import contextlib
from typing import Awaitable, Callable, Optional

from fastapi import WebSocket


class DeviceConnectionSlot:
    def __init__(self, *, cleanup_timeout: float = 5.0):
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._closed = False
        self._stopping: set[asyncio.Task] = set()
        self.cleanup_timeout = cleanup_timeout

    async def _stop_previous(self) -> bool:
        task = self._task
        if task is None:
            return True
        if not task.done():
            if task not in self._stopping:
                self._stopping.add(task)
                task.cancel()
            done, _ = await asyncio.wait({task}, timeout=self.cleanup_timeout)
            if not done:
                return False
        with contextlib.suppress(asyncio.CancelledError, Exception):
            task.result()
        return True

    async def run(self, ws: WebSocket, handler: Callable[[WebSocket], Awaitable[None]]):
        async with self._lock:
            if self._closed or not await self._stop_previous():
                # 清理超时不放行新会话，避免旧回调改变新连接的状态。
                await ws.close(code=1013)
                return
            task = asyncio.create_task(handler(ws))
            self._task = task
        try:
            await task
        except asyncio.CancelledError:
            # 被下一条连接替换属于正常退出；服务关闭的取消继续向外传播。
            if task not in self._stopping:
                raise
        finally:
            if self._task is task and task.done():
                self._task = None
            self._stopping.discard(task)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(ws.close(code=1000), timeout=1.0)

    async def close(self):
        async with self._lock:
            self._closed = True
            if not await self._stop_previous():
                raise TimeoutError("设备连接清理超时")
