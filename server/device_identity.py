"""设备编号格式；供服务器与离线登记工具共用，不加载模型依赖。"""

import re

DEVICE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


def validate_device_id(device_id: str) -> str:
    if not isinstance(device_id, str) or not DEVICE_ID_PATTERN.fullmatch(device_id):
        raise ValueError("设备编号须为 1～64 位字母、数字、下划线或连字符")
    return device_id
