"""FastAPI 生命周期和接口；每台设备的业务状态由 DeviceRuntime 持有。"""

from contextlib import asynccontextmanager
import asyncio
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.requests import HTTPConnection
import uvicorn

# 桌面运行环境与打包检查保留这些只读能力入口。
from device_runtime import (
    HOST_CAMERA_AVAILABLE, MEDIAPIPE_AVAILABLE, PIL_AVAILABLE, np, _build_hand_tracker,
    MEMORY_DB_PATH, MEMORY_USER_ID, MEMORY_DEVICE_ID, MEMORY_IDLE_SECONDS,
    MEMORY_POLL_SECONDS, MEMORY_SHUTDOWN_TIMEOUT_SECONDS, RECORDS_DIR,
    DeviceRuntime, summarize_memory_messages, _log_memory_error,
)
from device_registry import DeviceRegistry
from index_html import INDEX_HTML
from memory_store import MemoryManager, MemoryStore
from identity_store import IdentityStore
from access_control import AccessSettings, LocalAccessMiddleware, authorize_runtime
from connection_authorization import ConnectionAuthorizer, AuthorizedStreamingResponse
from auth_routes import router as auth_router
from device_routes import router as devices_router

router = APIRouter()


async def resolve_runtime(connection: HTTPConnection, *, hardware=False, playback=False) -> DeviceRuntime:
    device_id, user_id = await authorize_runtime(connection, hardware=hardware, playback=playback)
    try:
        return connection.app.state.devices.get(device_id, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


async def get_device_runtime(connection: HTTPConnection) -> DeviceRuntime:
    return await resolve_runtime(connection)


async def get_hardware_runtime(connection: HTTPConnection) -> DeviceRuntime:
    return await resolve_runtime(connection, hardware=True)


async def get_playback_runtime(connection: HTTPConnection) -> DeviceRuntime:
    return await resolve_runtime(connection, playback=True)


async def run_authorized_websocket(ws: WebSocket, handler):
    authorization = getattr(ws.state, "device_authorization", None)
    await ws.app.state.connections.run(authorization, handler, ws=ws)


@router.get("/", response_class=HTMLResponse)
def root():
    return get_index_html()


@router.get("/api/health", response_class=PlainTextResponse)
def health():
    return "OK"


@router.get("/stream.wav")
@router.get("/api/devices/{device_id}/stream.wav")
async def stream_wav(request: Request, runtime: DeviceRuntime = Depends(get_playback_runtime)):
    response = runtime.audio.response()
    authorization = getattr(request.state, "device_authorization", None)
    return AuthorizedStreamingResponse(response, request.app.state.connections, authorization) if authorization else response


@router.get("/api/memory")
@router.get("/api/devices/{device_id}/memory")
async def memory_overview(runtime: DeviceRuntime = Depends(get_device_runtime)):
    return await runtime.memory_overview()


@router.delete("/api/memory")
@router.delete("/api/devices/{device_id}/memory")
async def clear_memory(runtime: DeviceRuntime = Depends(get_device_runtime)):
    return await runtime.clear_memory()


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
    return INDEX_HTML


def create_app(*, store: Optional[MemoryStore] = None, summarizer=summarize_memory_messages,
               access: Optional[AccessSettings] = None) -> FastAPI:
    manager = MemoryManager(
        store if store is not None else MemoryStore(MEMORY_DB_PATH),
        summarizer, idle_seconds=MEMORY_IDLE_SECONDS, poll_seconds=MEMORY_POLL_SECONDS,
        on_error=_log_memory_error,
    )
    devices = DeviceRegistry(manager)
    access = access if access is not None else AccessSettings.from_environment()
    identities = IdentityStore(manager.store.path)
    connections = ConnectionAuthorizer(identities)

    async def cleanup_revoked_devices():
        while True:
            await asyncio.sleep(1)
            for device in list(devices.devices.values()):
                try:
                    if await asyncio.to_thread(identities.owned_device, device.device_id, device.user_id) is None:
                        await connections.revoke_device(device.device_id)
                        await devices.retire(device.device_id)
                except Exception as exc:
                    print(f"[DEVICE] cleanup will retry: {type(exc).__name__}", flush=True)

    @asynccontextmanager
    async def lifespan(application):
        await manager.start()
        cleanup_task = asyncio.create_task(cleanup_revoked_devices()) if access.mode == "accounts" else None
        try:
            yield
        finally:
            try:
                if cleanup_task is not None:
                    cleanup_task.cancel()
                    await asyncio.gather(cleanup_task, return_exceptions=True)
                await connections.close()
                await devices.close()
                await manager.flush_pending(timeout=MEMORY_SHUTDOWN_TIMEOUT_SECONDS)
            finally:
                await manager.close()

    application = FastAPI(lifespan=lifespan)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        errors = [{"type": item["type"], "loc": item["loc"], "msg": item["msg"]} for item in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)
    application.state.memory_manager = manager
    application.state.devices = devices
    application.state.access = access
    application.state.identities = identities
    application.state.connections = connections
    application.state.default_user_id = MEMORY_USER_ID
    application.state.default_device_id = MEMORY_DEVICE_ID
    application.include_router(router)
    application.include_router(auth_router)
    application.include_router(devices_router)
    application.add_middleware(LocalAccessMiddleware, settings=access)
    return application


app = create_app()
memory_manager = app.state.memory_manager
memory_store = memory_manager.store


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8081, log_level="info", access_log=False)
