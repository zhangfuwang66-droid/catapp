"""服务集成测试共享的隔离导入，不读取实际配置、API Key 或聊天数据库。"""

import os
from pathlib import Path
import tempfile
from unittest.mock import patch

_import_data = tempfile.TemporaryDirectory(prefix="cybermiao-test-import-")
with patch.dict(os.environ, {
    "CYBERMIAO_DATA_DIR": _import_data.name,
    "CYBERMIAO_MEMORY_DB_PATH": str(Path(_import_data.name) / "default.sqlite3"),
    "DASHSCOPE_API_KEY": "simulated-model-only",
    "CYBERMIAO_ACCESS_MODE": "local",
    "CYBERMIAO_PUBLIC_URL": "",
}):
    import app as server_app
