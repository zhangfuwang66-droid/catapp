"""管理登录和页面体验独立浏览器夹具；只使用本工作树与临时模拟数据。"""

import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from unittest.mock import AsyncMock, patch


PRIVATE_MARKERS = ("PRIVATE_EXPERIENCE_MESSAGE", "PRIVATE_EXPERIENCE_SUMMARY",
                   "PRIVATE_EXPERIENCE_FACT", "PRIVATE_EXPERIENCE_FILENAME.txt",
                   "PRIVATE_EXPERIENCE_DOCUMENT")


def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="admin-experience-") as temporary, ExitStack() as stack:
        directory = Path(temporary)
        environment = {name: os.environ[name] for name in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP") if name in os.environ}
        environment.update({
            "CYBERMIAO_DATA_DIR": str(directory),
            "CYBERMIAO_MEMORY_DB_PATH": str(directory / "bootstrap.sqlite3"),
            "CYBERMIAO_SECRET_KEY_FILE": str(directory / "unused.key"),
            "CYBERMIAO_ACCESS_MODE": "accounts", "CYBERMIAO_PUBLIC_URL": "",
            "CYBERMIAO_BACKUP_DIR": "", "DASHSCOPE_API_KEY": "",
            "PYTHON_DOTENV_DISABLED": "1", "HAND_TRACKING_ENABLED": "0",
            "COVER_INTERACTION_ENABLED": "0", "ENABLE_ESP32_SCREEN_PUSH": "0",
            "MPLCONFIGDIR": str(directory / "matplotlib"), "MPLBACKEND": "Agg",
            "OPENBLAS_NUM_THREADS": "1",
        })
        stack.enter_context(patch.dict(os.environ, environment, clear=True))
        stack.enter_context(patch("dotenv.load_dotenv", return_value=False))
        sys.path.insert(0, str(root))
        from access_control import AccessSettings
        from database_backups import BackupSettings
        from fastapi import HTTPException
        from knowledge_service import KnowledgeService
        from knowledge_store import KnowledgeStore, split_text
        from memory_store import MemoryStore, SummaryResult
        import app as server_app
        import uvicorn

        class OfflineEmbeddings:
            async def embed(self, _texts, *, timeout=8):
                raise AssertionError("独立验收禁止模型访问")

            async def close(self):
                pass

        async def summarize(_messages):
            return SummaryResult(summary="独立验收模拟摘要")

        memory = MemoryStore(directory / "memory.sqlite3")
        knowledge = KnowledgeService(KnowledgeStore(memory.path), embedder=OfflineEmbeddings())
        stack.enter_context(patch.object(knowledge, "start", new=AsyncMock()))
        application = server_app.create_app(
            store=memory, summarizer=summarize, access=AccessSettings("accounts"),
            backup_settings=BackupSettings(), knowledge_service=knowledge)
        required = {"/api/admin/auth/login": "post", "/api/admin/auth/logout": "post", "/api/admin/session": "get",
                    "/api/admin/dashboard": "get", "/api/admin/users/{user_id}": "get",
                    "/api/admin/devices/{device_id}": "get"}
        paths = application.openapi().get("paths", {})
        missing = [method.upper() + " " + path for path, method in required.items()
                   if method not in paths.get(path, {})]
        if missing:
            raise RuntimeError("真实统一应用缺少体验验收接口: " + ", ".join(sorted(missing)))
        identities = application.state.identities
        admin = identities.create_user("experience-admin", "temporary-experience-password")
        owner = identities.create_user("experience-owner", "temporary-experience-password")
        application.state.admin_store.set_role(admin.username, True)
        credential = identities.register_device("cat-experience", "独立验收离线登记")
        pairing = identities.request_pairing(credential.device_id, credential.token)
        identities.claim_pairing(pairing.code, owner.id, client_key="experience-fixture")
        conversation = memory.create_conversation(owner.id, credential.device_id)
        message = memory.append_message(conversation, "user", PRIVATE_MARKERS[0])
        memory.save_summary(conversation, message, PRIVATE_MARKERS[1], memories=[PRIVATE_MARKERS[2]])
        knowledge.store.add_document(owner.id, PRIVATE_MARKERS[3], PRIVATE_MARKERS[4].encode("utf-8"))
        knowledge.store.finish(knowledge.store.claim_next(), split_text(PRIVATE_MARKERS[4]))
        application.state.admin_started_at = time.monotonic() - 3661

        def no_runtime(*_args, **_kwargs):
            raise HTTPException(503, "独立验收不允许读接口创建设备运行时")

        def no_model(*_args, **_kwargs):
            raise AssertionError("独立验收不允许读取模型凭证")

        stack.enter_context(patch.object(application.state.devices, "get", side_effect=no_runtime))
        stack.enter_context(patch.object(application.state.devices, "resolve", new=AsyncMock(side_effect=no_runtime)))
        stack.enter_context(patch.object(application.state.providers, "key", side_effect=no_model))
        # 不在夹具补挂任何路由；真实统一应用遗漏接口必须使验收失败。
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        server = uvicorn.Server(uvicorn.Config(
            application, log_level="critical", access_log=False, proxy_headers=False))
        thread = threading.Thread(target=lambda: asyncio.run(server.serve(sockets=[listener])), daemon=True)
        thread.start()
        deadline = time.monotonic() + 25
        try:
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("独立体验验收临时服务未就绪")
                time.sleep(.025)
            print(json.dumps({"url": f"http://127.0.0.1:{listener.getsockname()[1]}",
                              "owner_id": owner.id}), flush=True)
            sys.stdin.readline()
        finally:
            server.should_exit = True
            thread.join(10)
            listener.close()


if __name__ == "__main__":
    main()
