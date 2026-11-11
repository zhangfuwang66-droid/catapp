"""仅监听回环地址的管理后台隔离预览，退出后删除全部模拟数据。"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from unittest.mock import patch


HOST = "127.0.0.1"
PASSWORD = "preview-only-password"
ACCOUNTS = ("preview-admin", "preview-admin-two", "preview-owner", "preview-guest")
SERVER_ROOT = Path(__file__).resolve().parents[1]


def preview_environment(directory):
    # 只继承操作系统所需路径，不继承项目配置、代理、模型地址或凭证。
    names = ("PATH", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "LANG")
    environment = {name: os.environ[name] for name in names if name in os.environ}
    environment.update({
        "CYBERMIAO_DATA_DIR": str(directory),
        "CYBERMIAO_MEMORY_DB_PATH": str(directory / "bootstrap.sqlite3"),
        "CYBERMIAO_SECRET_KEY_FILE": str(directory / "unused-preview.key"),
        "CYBERMIAO_ACCESS_MODE": "accounts", "CYBERMIAO_PUBLIC_URL": "",
        "CYBERMIAO_BACKUP_DIR": "", "DASHSCOPE_API_KEY": "",
        "DASHSCOPE_COMPAT_BASE": "http://127.0.0.1:1/disabled",
        "QWEN_REALTIME_WS_URL": "ws://127.0.0.1:1/disabled",
        "HAND_TRACKING_ENABLED": "0", "COVER_INTERACTION_ENABLED": "0",
        "ENABLE_ESP32_SCREEN_PUSH": "0", "MPLBACKEND": "Agg",
        "MPLCONFIGDIR": str(directory / "matplotlib"), "OPENBLAS_NUM_THREADS": "1",
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHON_DOTENV_DISABLED": "1",
    })
    return environment


class PreviewEmbeddings:
    """固定本地向量，仅供演示资料状态，不声称具有语义检索能力。"""

    model = "local-preview-only"

    async def embed(self, texts, *, timeout=8):
        return [[1.0] + [0.0] * 511 for _ in texts]

    async def close(self):
        pass


def preview_features(application):
    paths = application.openapi().get('paths', {})
    return {
        'dashboard': 'get' in paths.get('/api/admin/dashboard', {}),
        'device_crud': 'post' in paths.get('/api/admin/devices/{device_id}/rotate-credential', {}),
        'account_crud': 'post' in paths.get('/api/admin/users', {}),
    }


class PreviewBoundary:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'websocket':
            await send({'type': 'websocket.close', 'code': 4403, 'reason': '隔离预览不连接设备'})
            return
        if scope['type'] == 'http' and scope.get('path', '').startswith('/api/account/model'):
            from fastapi.responses import JSONResponse
            response = JSONResponse({'detail': '隔离预览不读取或保存模型 Key'}, status_code=403,
                                    headers={'Cache-Control': 'no-store'})
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


@contextmanager
def preview_application():
    if any(name in sys.modules for name in ("app", "device_runtime", "runtime_paths")):
        raise RuntimeError("请在新的 Python 进程中启动隔离预览")
    with tempfile.TemporaryDirectory(prefix="cybermiao-admin-preview-") as temporary, ExitStack() as stack:
        directory = Path(temporary)
        stack.enter_context(patch.dict(os.environ, preview_environment(directory), clear=True))
        # 即使应用今后调整 DATA_ROOT，也不能在预览导入时读取任何 .env 文件。
        stack.enter_context(patch("dotenv.load_dotenv", return_value=False))
        sys.path.insert(0, str(SERVER_ROOT))
        stack.callback(sys.path.remove, str(SERVER_ROOT))
        import app as server_app
        from access_control import AccessSettings
        from database_backups import BackupSettings
        from fastapi import HTTPException
        from knowledge_service import KnowledgeService
        from knowledge_store import KnowledgeStore, split_text
        from memory_store import MemoryStore, SummaryResult
        from provider_settings import MissingProviderKey
        from runtime_storage import StorageSettings

        async def summarize(_messages):
            return SummaryResult(summary="这是一段由隔离预览生成的模拟摘要")

        memory = MemoryStore(directory / "preview.sqlite3")
        knowledge = KnowledgeService(KnowledgeStore(memory.path), embedder=PreviewEmbeddings())
        application = server_app.create_app(
            store=memory, summarizer=summarize, access=AccessSettings("accounts"),
            backup_settings=BackupSettings(directory=directory / "backups", min_free_bytes=0),
            storage_settings=StorageSettings(),
            knowledge_service=knowledge,
        )

        def no_runtime(*_args, **_kwargs):
            raise HTTPException(409, "隔离预览只提供管理数据，不创建设备连接或模型会话")

        async def no_resolve(*_args, **_kwargs):
            return no_runtime()

        def no_model(*_args, **_kwargs):
            raise MissingProviderKey()

        stack.enter_context(patch.object(application.state.devices, "get", no_runtime))
        stack.enter_context(patch.object(application.state.devices, "resolve", no_resolve))
        stack.enter_context(patch.object(application.state.providers, "key", no_model))

        application.add_middleware(PreviewBoundary)

        identities = application.state.identities
        users = {name: identities.create_user(name, PASSWORD) for name in ACCOUNTS}
        for name in ACCOUNTS[:2]:
            application.state.admin_store.set_role(name, True)
        owner, guest = users["preview-owner"], users["preview-guest"]
        for device_id, display_name, bound in (
            ("cat-preview", "模拟小猫 · 已绑定", True),
            ("deer-preview", "模拟小鹿 · 未绑定", False),
            ("other-preview", "模拟未知设备 · 借用中", True),
            ("cat-preview-retired", "模拟已撤销登记", True),
        ):
            credential = identities.register_device(device_id, display_name)
            if bound:
                pairing = identities.request_pairing(device_id, credential.token)
                identities.claim_pairing(pairing.code, owner.id, client_key="preview-seed")
                identities.save_voice_preference(owner.id, device_id, True)
        invitation = identities.create_loan("other-preview", owner.id, 1)
        identities.claim_loan(invitation["code"], guest.id, client_key="preview-seed")
        identities.save_voice_preference(guest.id, "other-preview", True)
        identities.revoke_device("cat-preview-retired")
        for user, device_id in ((owner, "cat-preview"), (guest, "other-preview")):
            conversation = memory.create_conversation(user.id, device_id)
            message = memory.append_message(conversation, "user", "只属于此模拟账号的预览历史")
            memory.save_summary(conversation, message, "模拟私有摘要", memories=["模拟事实"])
        knowledge.store.add_document(owner.id, "模拟资料.txt", "预览资料仅用于展示容量与状态".encode("utf-8"))
        knowledge.store.finish(knowledge.store.claim_next(), split_text("预览资料仅用于展示容量与状态"))
        yield application, directory


async def serve(application, listener, directory):
    import uvicorn

    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        application, host=HOST, port=port, loop="asyncio", http="h11",
        proxy_headers=False, access_log=False, log_level="warning",
        timeout_graceful_shutdown=5, ws_max_size=1024 * 1024, ws_max_queue=8,
    ))

    async def ready():
        while not server.started and not server.should_exit:
            await asyncio.sleep(.025)
        if server.started:
            print(json.dumps({
                "url": f"http://{HOST}:{port}/admin", "temporary_data_dir": str(directory),
                "accounts": [{"username": name, "password": PASSWORD,
                              "role": "管理员" if name in ACCOUNTS[:2] else "普通用户"} for name in ACCOUNTS],
                "features": preview_features(application),
                "models": "仅本地模拟摘要与固定向量，设备运行时及真实模型访问已阻止",
                "stop": "Ctrl+C 结束服务并删除本次全部模拟数据",
            }, ensure_ascii=False), flush=True)

    reporter = asyncio.create_task(ready())
    try:
        await server.serve(sockets=[listener])
    finally:
        reporter.cancel()
        await asyncio.gather(reporter, return_exceptions=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="临时管理后台预览；仅监听127.0.0.1，Ctrl+C清理退出")
    parser.add_argument("--port", type=int, default=0, help="端口，默认0由系统选择空闲端口")
    arguments = parser.parse_args(argv)
    if not 0 <= arguments.port <= 65535:
        parser.error("端口须在0到65535之间")
    try:
        with preview_application() as (application, directory), socket.socket() as listener:
            listener.bind((HOST, arguments.port))
            listener.listen(128)
            asyncio.run(serve(application, listener, directory))
    except KeyboardInterrupt:
        pass
    print("隔离预览已结束，临时数据已清理。", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
