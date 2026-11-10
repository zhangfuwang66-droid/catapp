"""Temporary administrator browser fixture; never uses cloud settings or a model."""

import asyncio
from contextlib import ExitStack
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from unittest.mock import patch

import uvicorn

from support import server_app
from access_control import AccessSettings
from memory_store import MemoryStore, SummaryResult
import device_runtime


async def summarize(messages):
    return SummaryResult(summary="模拟摘要")


def main():
    with ExitStack() as stack:
        directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="admin-web-")))
        stack.enter_context(patch.object(device_runtime, "API_KEY", ""))
        store = MemoryStore(directory / "memory.sqlite3")
        app = server_app.create_app(store=store, summarizer=summarize, access=AccessSettings("accounts"))
        identities = app.state.identities
        identities.create_user("site-admin", "temporary-test-password")
        owner = identities.create_user("ordinary-user", "temporary-test-password")
        app.state.admin_store.set_role("site-admin", True)
        device = identities.register_device("cat-browser-test", "测试小喵")
        code = identities.request_pairing(device.device_id, device.token)
        identities.claim_pairing(code.code, owner.id, client_key="test")

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=listener.getsockname()[1],
                                               log_level="critical", access_log=False))
        thread = threading.Thread(target=lambda: asyncio.run(server.serve(sockets=[listener])), daemon=True)
        thread.start()
        deadline = time.monotonic() + 15
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("管理员测试服务启动失败")
            time.sleep(.02)
        try:
            print(json.dumps({"url": f"http://127.0.0.1:{listener.getsockname()[1]}"}), flush=True)
            sys.stdin.readline()
        finally:
            server.should_exit = True
            thread.join(10)
            listener.close()


if __name__ == "__main__":
    main()
