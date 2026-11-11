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
from unittest.mock import AsyncMock, patch

import uvicorn

from support import server_app
from access_control import AccessSettings
from memory_store import MemoryStore, SummaryResult
from knowledge_service import KnowledgeService
from knowledge_store import KnowledgeStore
import device_runtime


async def summarize(messages):
    return SummaryResult(summary="模拟摘要")


class OfflineEmbeddings:
    model = "admin-offline-fixture"

    async def embed(self, texts, *, timeout=8):
        raise ConnectionError("offline fixture only")

    async def close(self):
        pass


def main():
    with ExitStack() as stack:
        directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="admin-web-")))
        stack.enter_context(patch.object(device_runtime, "API_KEY", ""))
        store = MemoryStore(directory / "memory.sqlite3")
        knowledge = KnowledgeService(KnowledgeStore(store.path), embedder=OfflineEmbeddings())
        # Hold deterministic queued/processing metadata without starting a worker or calling a model.
        stack.enter_context(patch.object(knowledge, "start", new=AsyncMock()))
        app = server_app.create_app(store=store, summarizer=summarize, access=AccessSettings("accounts"), knowledge_service=knowledge)
        identities = app.state.identities
        identities.create_user("site-admin", "temporary-test-password")
        owner = identities.create_user("ordinary-user", "temporary-test-password")
        app.state.admin_store.set_role("site-admin", True)
        device = identities.register_device("cat-browser-test", "测试小喵")
        code = identities.request_pairing(device.device_id, device.token)
        identities.claim_pairing(code.code, owner.id, client_key="test")
        identities.register_device("deer-browser-test", "测试小鹿")
        identities.register_device("mystery-browser-test", "待识别设备")
        for index, status in enumerate(("queued", "processing", "ready", "keyword", "failed")):
            document, _ = knowledge.store.add_document(owner.id, f"fixture-{index}.txt", f"offline fixture {index}".encode())
            with knowledge.store.connection(write=True) as db:
                db.execute("UPDATE knowledge_documents SET status=? WHERE id=?", (status, document["id"]))

        # First unbind commits in the real route, then deterministically fails connection cleanup.
        revoke_device = app.state.connections.revoke_device
        cleanup_failed = False
        async def fail_cleanup_once(device_id, *args, **kwargs):
            nonlocal cleanup_failed
            if device_id == device.device_id and not cleanup_failed:
                cleanup_failed = True
                raise RuntimeError("simulated connection cleanup failure")
            return await revoke_device(device_id, *args, **kwargs)
        stack.enter_context(patch.object(app.state.connections, "revoke_device", new=fail_cleanup_once))

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
