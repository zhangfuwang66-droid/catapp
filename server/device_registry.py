"""在单个服务进程内管理设备实例；身份认证由接口层执行。"""

import asyncio
from contextlib import asynccontextmanager
from typing import Dict

from device_runtime import DeviceRuntime
from device_identity import validate_device_id
from memory_store import MemoryManager


class DeviceLock(asyncio.Lock):
    """可等待释放通知而不预占锁，避免账号写请求阻塞管理员撤销。"""

    def __init__(self):
        super().__init__()
        self._acquiring = 0
        self._availability_waiters = 0
        self._changed = asyncio.Event()

    @property
    def available(self):
        # release 已唤醒的普通 acquire 等待者也有优先权。
        return not self.locked() and self._acquiring == 0

    @property
    def waiting_count(self):
        return self._acquiring + self._availability_waiters

    def _notify(self):
        changed, self._changed = self._changed, asyncio.Event()
        changed.set()

    async def acquire(self):
        self._acquiring += 1
        try:
            return await super().acquire()
        finally:
            self._acquiring -= 1
            if self.available:
                self._notify()

    def release(self):
        super().release()
        self._notify()

    async def wait_available(self):
        changed = self._changed
        if not self.available:
            self._availability_waiters += 1
            try:
                await changed.wait()
            finally:
                self._availability_waiters -= 1


class DeviceRegistry:
    def __init__(self, memory_manager: MemoryManager, *, storage=None, knowledge=None, providers=None, preferences=None, speech=None):
        self.memory_manager = memory_manager
        self.providers = providers
        self.preferences = preferences
        self.speech = speech
        self.storage = storage
        self.knowledge = knowledge
        self.devices: Dict[str, DeviceRuntime] = {}
        self._locks: Dict[str, DeviceLock] = {}

    @asynccontextmanager
    async def locked(self, device_id: str):
        async with self.lock_for(device_id):
            yield

    def lock_for(self, device_id: str):
        validate_device_id(device_id)
        return self._locks.setdefault(device_id, DeviceLock())

    @asynccontextmanager
    async def locked_for_account(self, device_id: str, account_lock: asyncio.Lock):
        """写入时按账号→设备持锁；等设备期间让管理员仍可取得账号锁撤销请求。"""
        device_lock = self.lock_for(device_id)
        while True:
            async with account_lock:
                # 检查到取设备锁之间没有调度点；不持账号锁等待设备。
                if device_lock.available:
                    async with device_lock:
                        yield
                    return
            await device_lock.wait_available()

    async def resolve(self, device_id: str, user_id: str) -> DeviceRuntime:
        async with self.locked(device_id):
            return self.get(device_id, user_id)

    def get(self, device_id: str, user_id: str) -> DeviceRuntime:
        validate_device_id(device_id)
        runtime = self.devices.get(device_id)
        if runtime is None:
            runtime = DeviceRuntime(device_id, user_id, self.memory_manager, storage=self.storage, knowledge=self.knowledge,
                                    providers=self.providers, preferences=self.preferences,speech=self.speech)
            self.devices[device_id] = runtime
        elif runtime.user_id != user_id:
            raise ValueError("设备运行身份与当前归属不一致")
        elif runtime._closed:
            raise ValueError("设备正在清理，请稍后重试")
        return runtime

    def protected_record_paths(self):
        return {entry["image_path"] for runtime in self.devices.values() for entry in runtime.recording_entries}

    async def retire(self, device_id: str):
        async with self.locked(device_id):
            await self.retire_locked(device_id)

    async def retire_locked(self, device_id: str):
        """调用方已持有对应设备锁时使用，避免重连抢先建立新会话。"""
        runtime = self.devices.get(device_id)
        if runtime is not None:
            await runtime.close()
            if self.devices.get(device_id) is runtime:
                self.devices.pop(device_id, None)

    async def close(self):
        devices = list(self.devices.values())
        try:
            results = await asyncio.gather(*(device.close() for device in devices), return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    raise result
        finally:
            self.devices.clear()
