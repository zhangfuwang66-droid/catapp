"""在单个服务进程内管理设备实例；身份认证由接口层执行。"""

import asyncio
from typing import Dict

from device_runtime import DeviceRuntime
from device_identity import validate_device_id
from memory_store import MemoryManager

class DeviceRegistry:
    def __init__(self, memory_manager: MemoryManager):
        self.memory_manager = memory_manager
        self.devices: Dict[str, DeviceRuntime] = {}

    def get(self, device_id: str, user_id: str) -> DeviceRuntime:
        validate_device_id(device_id)
        runtime = self.devices.get(device_id)
        if runtime is None:
            runtime = DeviceRuntime(device_id, user_id, self.memory_manager)
            self.devices[device_id] = runtime
        elif runtime.user_id != user_id:
            raise ValueError("设备运行身份与当前归属不一致")
        return runtime

    async def retire(self, device_id: str):
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
