"""在单个服务进程内管理设备实例；身份认证由接口层执行。"""

import asyncio
import re
from typing import Dict

from device_runtime import DeviceRuntime
from memory_store import MemoryManager

DEVICE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


def validate_device_id(device_id: str) -> str:
    if not isinstance(device_id, str) or not DEVICE_ID_PATTERN.fullmatch(device_id):
        raise ValueError("设备编号须为 1～64 位字母、数字、下划线或连字符")
    return device_id


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

    async def close(self):
        devices = list(self.devices.values())
        try:
            results = await asyncio.gather(*(device.close() for device in devices), return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    raise result
        finally:
            self.devices.clear()
