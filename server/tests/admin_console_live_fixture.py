"""Real administrator API/DB fixture, using only the current worktree's integrated backend."""
import asyncio
from contextlib import ExitStack
import importlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from unittest.mock import AsyncMock, patch


def main():
    ui_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="admin-console-browser-") as temporary, ExitStack() as stack:
        directory = Path(temporary)
        environment = {key: os.environ[key] for key in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP") if key in os.environ}
        environment.update({"CYBERMIAO_DATA_DIR": str(directory), "CYBERMIAO_MEMORY_DB_PATH": str(directory / "bootstrap.sqlite3"),
            "CYBERMIAO_SECRET_KEY_FILE": str(directory / "unused.key"), "CYBERMIAO_ACCESS_MODE": "accounts",
            "CYBERMIAO_PUBLIC_URL": "", "CYBERMIAO_BACKUP_DIR": "", "DASHSCOPE_API_KEY": "",
            "PYTHON_DOTENV_DISABLED": "1", "HAND_TRACKING_ENABLED": "0", "COVER_INTERACTION_ENABLED": "0",
            "ENABLE_ESP32_SCREEN_PUSH": "0", "MPLCONFIGDIR": str(directory / "matplotlib"), "MPLBACKEND": "Agg"})
        stack.enter_context(patch.dict(os.environ, environment, clear=True))
        stack.enter_context(patch("dotenv.load_dotenv", return_value=False))
        sys.path.insert(0, str(ui_root))
        import app as server_app
        import admin_pages
        from access_control import AccessSettings
        from database_backups import BackupSettings
        from knowledge_store import KnowledgeStore
        from knowledge_service import KnowledgeService
        from memory_store import MemoryStore, SummaryResult
        import uvicorn

        class OfflineEmbeddings:
            model = "browser-only-offline"
            async def embed(self, texts, *, timeout=8):
                raise RuntimeError("model access is disabled in browser tests")
            async def close(self):
                pass

        async def summarize(_messages):
            return SummaryResult(summary="浏览器测试模拟摘要")

        stack.enter_context(patch.object(server_app, "WEB_ROOT", ui_root / "web"))
        stack.enter_context(patch.object(admin_pages, "SERVER_ROOT", ui_root))
        memory = MemoryStore(directory / "memory.sqlite3")
        knowledge = KnowledgeService(KnowledgeStore(memory.path), embedder=OfflineEmbeddings())
        stack.enter_context(patch.object(knowledge, "start", new=AsyncMock()))
        app = server_app.create_app(store=memory, summarizer=summarize, access=AccessSettings("accounts"), knowledge_service=knowledge,
            backup_settings=BackupSettings(directory=directory / "backups", min_free_bytes=0))
        if "/api/admin/users/{user_id}" not in app.openapi().get("paths", {}) and importlib.util.find_spec("admin_accounts"):
            app.include_router(importlib.import_module("admin_accounts").router)
            app.openapi_schema = None
        required = {"/api/admin/dashboard", "/api/admin/users/{user_id}", "/api/admin/backups/run", "/api/admin/devices/{device_id}"}
        missing = required - set(app.openapi().get("paths", {}))
        if missing:
            raise RuntimeError("集成后端尚未包含管理扩展接口: " + ", ".join(sorted(missing)))
        def no_runtime(*_args, **_kwargs):
            raise RuntimeError("browser reads must never create a device runtime")
        stack.enter_context(patch.object(app.state.devices, "get", new=no_runtime))
        ids = app.state.identities
        ids.create_user("site-admin", "temporary-test-password")
        owner = ids.create_user("ordinary-user", "temporary-test-password")
        app.state.admin_store.set_role("site-admin", True)
        for device_id, name in (("cat-live-history", "本地小喵"), ("deer-live-sample", "本地小鹿"), ("unknown-live-sample", "未知类型样例")):
            credential = ids.register_device(device_id, name)
            if device_id == "cat-live-history":
                code = ids.request_pairing(device_id, credential.token)
                ids.claim_pairing(code.code, owner.id, client_key="browser-only")
        for index, status in enumerate(("queued", "processing", "ready", "keyword", "failed")):
            document, _ = knowledge.store.add_document(owner.id, f"fixture-{index}.txt", f"fictional text {index}".encode())
            with knowledge.store.connection(write=True) as db:
                db.execute("UPDATE knowledge_documents SET status=? WHERE id=?", (status, document["id"]))
        revoke = app.state.connections.revoke_device
        failed = False
        async def fail_once(device_id, *args, **kwargs):
            nonlocal failed
            if device_id == "cat-live-created" and not failed:
                failed = True
                raise RuntimeError("fictional post-commit cleanup failure")
            return await revoke(device_id, *args, **kwargs)
        stack.enter_context(patch.object(app.state.connections, "revoke_device", new=fail_once))
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0)); listener.listen(128)
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
        thread = threading.Thread(target=lambda: asyncio.run(server.serve(sockets=[listener])), daemon=True)
        thread.start()
        deadline = time.monotonic() + 20
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("本地集成夹具启动失败")
            time.sleep(.02)
        try:
            print(json.dumps({"url": f"http://127.0.0.1:{listener.getsockname()[1]}"}), flush=True)
            sys.stdin.readline()
        finally:
            server.should_exit = True; thread.join(10); listener.close()


if __name__ == "__main__":
    main()
