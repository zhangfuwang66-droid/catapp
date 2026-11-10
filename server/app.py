"""FastAPI 生命周期和接口；每台设备的业务状态由 DeviceRuntime 持有。"""

from contextlib import asynccontextmanager
import asyncio
import sqlite3
import time
from pathlib import Path
from async_helpers import run_blocking, finish_before_cancel
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
from starlette.requests import HTTPConnection
import uvicorn

# 桌面运行环境与打包检查保留这些只读能力入口。
from device_runtime import (
    HOST_CAMERA_AVAILABLE, MEDIAPIPE_AVAILABLE, PIL_AVAILABLE, np, _build_hand_tracker,
    MEMORY_DB_PATH, MEMORY_USER_ID, MEMORY_DEVICE_ID, MEMORY_IDLE_SECONDS,
    MEMORY_POLL_SECONDS, MEMORY_SHUTDOWN_TIMEOUT_SECONDS, RECORDS_DIR,
    DeviceRuntime, summarize_memory_messages, _log_memory_error,
    MEMORY_DEBUG_API_ENABLED,
)
from device_registry import DeviceRegistry
from web_pages import WEB_ROOT, console_html, portal_html
from memory_store import MemoryManager, MemoryStore
from database_backups import BackupSettings, DatabaseBackups
from runtime_storage import RuntimeStorage, StorageSettings
from runtime_logging import log_event
from tools.memory_database import check_database_ready
from identity_store import IdentityStore
from access_control import AccessSettings, LocalAccessMiddleware, authorize_runtime, require_session
from connection_authorization import ConnectionAuthorizer, AuthorizedStreamingResponse
from auth_routes import router as auth_router
from device_routes import router as devices_router
from knowledge_store import KnowledgeStore
from knowledge_service import KnowledgeService
from knowledge_routes import router as knowledge_router
from provider_settings import ProviderSettings, ModelCredentials
from provider_routes import router as provider_router, lock_for
from account_memory_routes import router as account_memory_router
from memory_retention import MemoryRetention
from speech_settings import SpeechSettings, SpeechService
from speech_routes import router as speech_router
from admin_store import AdminStore
from admin_routes import router as admin_router
from admin_operations import AccountOperations, AccountOperationsMiddleware
from admin_pages import admin_html
from admin_routes import require_admin

router = APIRouter()


async def resolve_runtime(connection: HTTPConnection, *, hardware=False, playback=False) -> DeviceRuntime:
    device_id, user_id = await authorize_runtime(connection, hardware=hardware, playback=playback)
    try:
        async with connection.app.state.devices.locked_for_account(device_id,lock_for(connection.app,user_id)):
            # 等锁期间可能已经解绑或转交，重新核验后才创建运行时。
            device_id, current_user = await authorize_runtime(connection, hardware=hardware, playback=playback)
            if current_user != user_id:
                raise HTTPException(409, "设备使用者已改变，请重新连接")
            return connection.app.state.devices.get(device_id, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


async def get_device_runtime(connection: HTTPConnection) -> DeviceRuntime:
    return await resolve_runtime(connection)


async def get_hardware_runtime(connection: HTTPConnection) -> DeviceRuntime:
    return await resolve_runtime(connection, hardware=True)


async def get_playback_runtime(connection: HTTPConnection) -> DeviceRuntime:
    return await resolve_runtime(connection, playback=True)


@asynccontextmanager
async def runtime_write(request: Request, runtime: DeviceRuntime):
    async with request.app.state.devices.locked_for_account(runtime.device_id,lock_for(request.app,runtime.user_id)):
        device_id, user_id = await authorize_runtime(request)
        if (device_id != runtime.device_id or user_id != runtime.user_id
                or request.app.state.devices.devices.get(device_id) is not runtime):
            raise HTTPException(409, "设备运行状态已改变，请刷新后重试")
        yield


async def run_authorized_websocket(ws: WebSocket, handler):
    authorization = getattr(ws.state, "device_authorization", None)
    await ws.app.state.connections.run(authorization, handler, ws=ws)


@router.get("/", response_class=HTMLResponse)
def root(request: Request):
    return portal_html() if request.app.state.access.mode == "accounts" else get_index_html()


@router.get("/console/{device_id}", response_class=HTMLResponse)
async def device_console(device_id: str, request: Request):
    if request.app.state.access.mode != "accounts":
        return RedirectResponse("/", status_code=303)
    try:
        user = await require_session(request)
    except HTTPException as exc:
        if exc.status_code == 401:
            return RedirectResponse("/?notice=login", status_code=303)
        raise
    device = await run_blocking(request.app.state.identities.accessible_device, device_id, user.id)
    if device is None:
        raise HTTPException(status_code=404, detail="设备不存在或无权访问")
    return console_html(device_id, account_mode=True)


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    try:
        await require_admin(request)
    except HTTPException as exc:
        if exc.status_code == 401:
            return RedirectResponse("/?notice=login", status_code=303, headers={"Cache-Control": "no-store"})
        raise
    return HTMLResponse(admin_html(), headers={"Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"})


@router.get("/web/admin/{asset:path}")
async def admin_asset(asset: str, request: Request):
    # 在公共静态挂载前截住后台目录，避免绕过页面入口。
    await require_admin(request)
    if asset not in {"index.html", "admin.js", "admin.css"}:
        raise HTTPException(404, "资源不存在")
    if asset == "index.html":
        return await admin_page(request)
    return FileResponse(WEB_ROOT / "admin" / asset, headers={"Cache-Control": "no-store"})


@router.get("/api/health", response_class=PlainTextResponse)
def health():
    return "OK"


@router.get("/api/ready", response_class=PlainTextResponse)
async def ready(request: Request):
    try:
        await run_blocking(check_database_ready, request.app.state.memory_manager.store.path)
    except (OSError, ValueError, sqlite3.Error):
        return PlainTextResponse("NOT READY", status_code=503, headers={"Cache-Control": "no-store"})
    return PlainTextResponse("OK", headers={"Cache-Control": "no-store"})


@router.get("/stream.wav")
@router.get("/api/devices/{device_id}/stream.wav")
async def stream_wav(request: Request, runtime: DeviceRuntime = Depends(get_playback_runtime)):
    response = runtime.audio.response()
    authorization = getattr(request.state, "device_authorization", None)
    return AuthorizedStreamingResponse(response, request.app.state.connections, authorization) if authorization else response


@router.get("/api/memory")
@router.get("/api/devices/{device_id}/memory")
async def memory_overview(request: Request, runtime: DeviceRuntime = Depends(get_device_runtime)):
    if request.app.state.access.mode == "local" and not MEMORY_DEBUG_API_ENABLED:
        raise HTTPException(status_code=404, detail="memory debug api disabled")
    return await runtime.memory_overview()


class ClearMemoryRequest(BaseModel):
    confirm_device_id: str = Field(min_length=1, max_length=64)


@router.delete("/api/memory")
@router.delete("/api/devices/{device_id}/memory")
async def clear_memory(request: Request, payload: Optional[ClearMemoryRequest] = None,
                       runtime: DeviceRuntime = Depends(get_device_runtime)):
    if request.app.state.access.mode == "local" and not MEMORY_DEBUG_API_ENABLED:
        raise HTTPException(status_code=404, detail="memory debug api disabled")
    if request.path_params.get("device_id") and (payload is None or payload.confirm_device_id != runtime.device_id):
        raise HTTPException(status_code=400, detail="请确认要清除记忆的设备编号")
    registry = request.app.state.devices
    async def perform():
        await request.app.state.connections.revoke_device(runtime.device_id, code=1012, reason="记忆已清除，请重新连接")
        await registry.retire_locked(runtime.device_id)
        await request.app.state.memory_manager.clear_owner(runtime.user_id, runtime.device_id)
    async with runtime_write(request, runtime):
        await finish_before_cancel(perform())
    return {"ok": True, "device_id": runtime.device_id}


@router.get("/repeat.wav")
@router.get("/api/devices/{device_id}/repeat.wav")
def repeat_wav(runtime: DeviceRuntime = Depends(get_device_runtime)):
    return runtime.repeat_wav()


@router.websocket("/ws_ui")
@router.websocket("/ws/devices/{device_id}/ui")
async def ws_ui(ws: WebSocket, runtime: DeviceRuntime = Depends(get_device_runtime)):
    await run_authorized_websocket(ws, lambda: runtime.serve_client(ws, runtime.ws_ui))


@router.websocket("/ws_audio")
@router.websocket("/ws/devices/{device_id}/audio")
async def ws_audio(ws: WebSocket, runtime: DeviceRuntime = Depends(get_hardware_runtime)):
    await run_authorized_websocket(ws, lambda: runtime.audio_connection.run(ws, runtime.ws_audio))


@router.websocket("/ws/camera")
@router.websocket("/ws/devices/{device_id}/camera")
async def ws_camera_esp(ws: WebSocket, runtime: DeviceRuntime = Depends(get_hardware_runtime)):
    await run_authorized_websocket(ws, lambda: runtime.camera_connection.run(ws, runtime.ws_camera_esp))


@router.websocket("/ws/viewer")
@router.websocket("/ws/devices/{device_id}/viewer")
async def ws_viewer(ws: WebSocket, runtime: DeviceRuntime = Depends(get_device_runtime)):
    await run_authorized_websocket(ws, lambda: runtime.serve_client(ws, runtime.ws_viewer))


@router.get("/servo")
@router.post("/api/devices/{device_id}/servo")
async def servo_control(ch: int=0, angle: int=90, runtime: DeviceRuntime = Depends(get_device_runtime)):
    return await runtime.servo_control(ch=ch, angle=angle)


@router.get("/servo_raw")
@router.post("/api/devices/{device_id}/servo_raw")
async def servo_raw_control(ch: int=0, angle: int=90, runtime: DeviceRuntime = Depends(get_device_runtime)):
    return await runtime.servo_raw_control(ch=ch, angle=angle)


@router.get("/servo_raw_mode")
@router.post("/api/devices/{device_id}/servo_raw_mode")
async def servo_raw_mode_control(active: int=1, runtime: DeviceRuntime = Depends(get_device_runtime)):
    return await runtime.servo_raw_mode_control(active=active)


@router.get("/screen_mode")
async def screen_mode_control(mode: Optional[int]=None, runtime: DeviceRuntime = Depends(get_device_runtime)):
    return await runtime.screen_mode_control(mode=mode)


@router.get("/api/devices/{device_id}/screen_mode")
async def screen_mode_get(runtime: DeviceRuntime = Depends(get_device_runtime)):
    return await runtime.screen_mode_control()


@router.post("/api/devices/{device_id}/screen_mode")
async def screen_mode_set(mode: int, runtime: DeviceRuntime = Depends(get_device_runtime)):
    return await runtime.screen_mode_control(mode=mode)


@router.get("/performance_mode")
@router.get("/api/devices/{device_id}/performance_mode")
async def performance_mode_get(runtime: DeviceRuntime = Depends(get_device_runtime)):
    return await runtime.performance_mode_get()


@router.post("/performance_mode")
@router.post("/api/devices/{device_id}/performance_mode")
async def performance_mode_set(active: Optional[int]=None, runtime: DeviceRuntime = Depends(get_device_runtime)):
    return await runtime.performance_mode_set(active=active)


class VoiceControlRequest(BaseModel):
    paused: bool


@router.get("/voice")
@router.get("/api/devices/{device_id}/voice")
async def voice_state(runtime: DeviceRuntime = Depends(get_device_runtime)):
    return runtime.voice_state()


@router.post("/voice")
@router.post("/api/devices/{device_id}/voice")
async def voice_control(payload: VoiceControlRequest, request: Request, runtime: DeviceRuntime = Depends(get_device_runtime)):
    async with runtime_write(request, runtime):
        return await finish_before_cancel(runtime.set_voice_paused(payload.paused))


@router.get("/camera_setting")
@router.post("/api/devices/{device_id}/camera_setting")
async def camera_setting(param: str='', value: int=0, runtime: DeviceRuntime = Depends(get_device_runtime)):
    return await runtime.camera_setting(param=param, value=value)


@router.post("/record/start")
@router.post("/api/devices/{device_id}/record/start")
async def record_start(runtime: DeviceRuntime = Depends(get_device_runtime)):
    return await runtime.record_start()


@router.post("/record/stop")
@router.post("/api/devices/{device_id}/record/stop")
async def record_stop(runtime: DeviceRuntime = Depends(get_device_runtime)):
    return await runtime.record_stop()


@router.get("/record/state")
@router.get("/api/devices/{device_id}/record/state")
async def record_state(runtime: DeviceRuntime = Depends(get_device_runtime)):
    return await runtime.record_state()


@router.post("/record/ask")
@router.post("/api/devices/{device_id}/record/ask")
async def record_ask(payload: Dict[str, Any], runtime: DeviceRuntime = Depends(get_device_runtime)):
    return await runtime.record_ask(payload=payload)


@router.post("/record/analyze")
@router.post("/api/devices/{device_id}/record/analyze")
async def record_analyze(runtime: DeviceRuntime = Depends(get_device_runtime)):
    return await runtime.record_analyze()


@router.get("/record/image/{session_id}/{filename}")
@router.get("/api/devices/{device_id}/record/image/{session_id}/{filename}")
async def record_image(session_id: str, filename: str, runtime: DeviceRuntime = Depends(get_device_runtime)):
    return await runtime.record_image(session_id=session_id, filename=filename)


@router.get("/tft_setting")
@router.post("/api/devices/{device_id}/tft_setting")
async def tft_setting(param: str='', value: int=0, runtime: DeviceRuntime = Depends(get_device_runtime)):
    return await runtime.tft_setting(param=param, value=value)


def get_index_html():
    return console_html()


def create_app(*, store: Optional[MemoryStore] = None, summarizer=summarize_memory_messages,
               access: Optional[AccessSettings] = None, backup_settings: Optional[BackupSettings] = None,
               storage_settings: Optional[StorageSettings] = None, knowledge_service=None) -> FastAPI:
    manager = MemoryManager(
        store if store is not None else MemoryStore(MEMORY_DB_PATH),
        summarizer, idle_seconds=MEMORY_IDLE_SECONDS, poll_seconds=MEMORY_POLL_SECONDS,
        on_error=_log_memory_error,
    )
    storage = RuntimeStorage(Path(RECORDS_DIR), storage_settings)
    access = access if access is not None else AccessSettings.from_environment()
    identities = IdentityStore(manager.store.path)
    admins = AdminStore(identities)
    if access.mode == "accounts":
        manager.user_enabled = identities.user_enabled
    retention = MemoryRetention(manager.store.path)
    manager.store.retention_enabled = True
    knowledge = knowledge_service if knowledge_service is not None else KnowledgeService(KnowledgeStore(manager.store.path))
    provider_settings = ProviderSettings(manager.store.path)
    import device_runtime
    providers = ModelCredentials(provider_settings, account_mode=access.mode == "accounts", local_key=lambda: device_runtime.API_KEY)
    knowledge.providers = providers
    if access.mode == "accounts":
        knowledge.user_enabled = identities.user_enabled
    speech = SpeechService(SpeechSettings(manager.store.path),providers,knowledge=knowledge)
    if summarizer is summarize_memory_messages:
        async def account_summarizer(messages):
            owner = await run_blocking(manager.store.conversation_owner, messages[0]["conversation_id"])
            if owner is None:
                raise ValueError("对话归属不存在")
            key = await run_blocking(providers.key, owner["user_id"])
            result = await summarize_memory_messages(messages, api_key=key)
            if key != await run_blocking(providers.key, owner["user_id"]):
                raise ValueError("模型凭证已更新，保留待总结消息重试")
            return result
        manager.summarize = account_summarizer
    devices = DeviceRegistry(manager, storage=storage, knowledge=knowledge, providers=providers,
                             preferences=identities if access.mode == "accounts" else None, speech=speech)
    storage.protected_paths = devices.protected_record_paths
    connections = ConnectionAuthorizer(identities)
    backups = DatabaseBackups(manager.store.path, backup_settings if backup_settings is not None else BackupSettings.from_environment())

    async def cleanup_revoked_devices():
        while True:
            await asyncio.sleep(1)
            for device_id in await run_blocking(identities.expire_loans):
                async with devices.locked(device_id):
                    await connections.revoke_device(device_id, code=4403, reason="借用已结束，请重新选择设备")
                    await devices.retire_locked(device_id)
            for device in list(devices.devices.values()):
                try:
                    if await run_blocking(identities.accessible_device, device.device_id, device.user_id) is None:
                        await connections.revoke_device(device.device_id)
                        await devices.retire(device.device_id)
                except Exception as exc:
                    log_event(f"[DEVICE] cleanup will retry: {type(exc).__name__}", flush=True)

    @asynccontextmanager
    async def lifespan(application):
        cleanup_task = None
        try:
            await manager.start()
            await backups.start()
            await storage.start()
            await knowledge.start()
            if access.mode == "accounts":
                await retention.start()
            cleanup_task = asyncio.create_task(cleanup_revoked_devices()) if access.mode == "accounts" else None
            yield
        finally:
            try:
                if cleanup_task is not None:
                    cleanup_task.cancel()
                    await asyncio.gather(cleanup_task, return_exceptions=True)
                await connections.close()
                await retention.close()
                await devices.close()
                await manager.flush_pending(timeout=MEMORY_SHUTDOWN_TIMEOUT_SECONDS)
            finally:
                try:
                    await manager.close()
                finally:
                    try:
                        await backups.close()
                    finally:
                        try:
                            await storage.close()
                        finally:
                            await knowledge.close()

    application = FastAPI(lifespan=lifespan)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        errors = [{"type": item["type"], "loc": item["loc"], "msg": item["msg"]} for item in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)
    application.state.memory_manager = manager
    application.state.devices = devices
    application.state.access = access
    application.state.identities = identities
    application.state.admin_store = admins
    application.state.admin_started_at = time.monotonic()
    application.state.admin_mutation_lock = asyncio.Lock()
    application.state.account_operations = AccountOperations()
    application.state.connections = connections
    application.state.database_backups = backups
    application.state.runtime_storage = storage
    application.state.knowledge = knowledge
    application.state.provider_settings = provider_settings
    application.state.providers = providers
    application.state.model_locks = {}
    application.state.memory_retention = retention
    application.state.speech = speech
    application.state.default_user_id = MEMORY_USER_ID
    application.state.default_device_id = MEMORY_DEVICE_ID
    application.include_router(router)
    application.include_router(auth_router)
    application.include_router(devices_router)
    application.include_router(knowledge_router)
    application.include_router(provider_router)
    application.include_router(account_memory_router)
    application.include_router(speech_router)
    application.include_router(admin_router)
    application.mount("/web", StaticFiles(directory=str(WEB_ROOT)), name="web")
    if access.mode == 'accounts':
        application.add_middleware(AccountOperationsMiddleware, identities=identities, operations=application.state.account_operations)
    application.add_middleware(LocalAccessMiddleware, settings=access)
    return application


app = create_app()
memory_manager = app.state.memory_manager
memory_store = memory_manager.store


if __name__ == "__main__":
    from runtime_logging import configure_logging
    from runtime_paths import DATA_ROOT
    configure_logging(DATA_ROOT / "logs")
    uvicorn.run(app, host="0.0.0.0", port=8081, log_config=None, log_level="info", access_log=False)
