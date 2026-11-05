"""浏览器测试专用：临时账号、临时数据库与回环地址，不使用真实模型或设备。"""

import asyncio
from contextlib import ExitStack
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from unittest.mock import patch

import uvicorn
from PIL import Image

from support import server_app
from access_control import AccessSettings
from memory_store import MemoryStore, SummaryResult
import device_runtime


async def summarize(messages):
    return SummaryResult(summary="浏览器测试的模拟摘要")


def main():
    with ExitStack() as stack:
        directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="cybermiao-web-")))
        for setting in ("HAND_TRACKING_ENABLED", "COVER_INTERACTION_ENABLED", "ENABLE_ESP32_SCREEN_PUSH"):
            stack.enter_context(patch.object(device_runtime, setting, False))
        stack.enter_context(patch.object(device_runtime, "API_KEY", ""))
        store = MemoryStore(directory / "accounts.sqlite3")
        app = server_app.create_app(store=store, summarizer=summarize, access=AccessSettings("accounts"))
        identities = app.state.identities
        for name in ("alice", "bob"):
            user = identities.create_user(name, name + "-test-password")
            device = identities.register_device("cat-" + name, name.title() + "的小喵")
            code = identities.request_pairing(device.device_id, device.token)
            identities.claim_pairing(code.code, user.id, client_key="local")
            conversation = store.create_conversation(user.id, device.device_id)
            message = store.append_message(conversation, "user", name.title() + "的私有消息")
            store.save_summary(conversation, message, name.title() + "的私有摘要", [name.title() + "的专属记忆"])
            runtime = app.state.devices.get(device.device_id, user.id)
            runtime.recent_finals.append("[AI] " + name.title() + "的私有对话")
            image = io.BytesIO()
            Image.new("RGB", (8, 8), (220, 70, 80)).save(image, format="JPEG")
            runtime.last_frames.append((time.time(), image.getvalue()))
        new_device = identities.register_device("cat-new", "新来的小喵")
        pair = identities.request_pairing(new_device.device_id, new_device.token)
        local = server_app.create_app(store=MemoryStore(directory / "local.sqlite3"), summarizer=summarize,
                                       access=AccessSettings("local"))
        servers = []
        urls = []
        for application in (app, local):
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen(128)
            port = listener.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(application, host="127.0.0.1", port=port,
                                                   log_level="critical", access_log=False))
            thread = threading.Thread(target=lambda s=server, sock=listener: asyncio.run(s.serve(sockets=[sock])), daemon=True)
            thread.start()
            servers.append((server, thread, listener))
            deadline = time.monotonic() + 15
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("临时网页测试服务启动失败")
                time.sleep(.02)
            urls.append("http://127.0.0.1:" + str(port))
        try:
            # 父测试捕获此行，不转发临时配对码；不输出长期凭证。
            print(json.dumps({"accounts_url": urls[0], "local_url": urls[1], "pair_code": pair.code}), flush=True)
            sys.stdin.readline()
        finally:
            for server, _, _ in servers:
                server.should_exit = True
            for _, thread, listener in servers:
                thread.join(10)
                listener.close()


if __name__ == "__main__":
    main()
