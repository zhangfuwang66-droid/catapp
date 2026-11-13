"""小鹿 OTA：主人会话控制、设备凭证轮询、持久化任务和同源固件下载。"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from typing import Literal

from access_control import require_device_credential, require_session, require_session_write
from async_helpers import run_blocking, finish_before_cancel
from identity_store import User
from firmware_store import BUSY
from provider_routes import lock_for

router = APIRouter()


class Report(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: str = Field(max_length=48, pattern=r'^[0-9A-Za-z._-]+$')
    sequence: int = Field(ge=0, le=2147483647)
    ota_capable: StrictBool
    slot: Literal['app0', 'app1', 'unknown']
    previous_version: str = Field(default='', max_length=48, pattern=r'^[0-9A-Za-z._-]*$')
    rollback_available: StrictBool = False
    state: Literal['idle', 'downloading', 'verifying', 'rebooting', 'pending_validation', 'updated', 'failed', 'rolled_back'] = 'idle'
    progress: int = Field(default=0, ge=0, le=100)
    command_id: str = Field(default='', max_length=32, pattern=r'^(?:[a-f0-9]{32})?$')
    error_code: str = Field(default='', max_length=64, pattern=r'^[a-z0-9_]*$')
    busy: StrictBool = True
    idle_seconds: int = Field(default=0, ge=0, le=2147483647)


class Policy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    auto_update: StrictBool


class UpdateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['install', 'rollback']
    release_id: str = Field(default='', max_length=96, pattern=r'^[a-z0-9._-]*$')
    confirm_device_id: str = Field(max_length=64)


async def owned_device(request, device_id, user_id):
    device = await run_blocking(request.app.state.identities.owned_device, device_id, user_id)
    if device is None:
        raise HTTPException(404, '设备不存在或不属于当前账号')
    current = await run_blocking(request.app.state.identities.accessible_device, device_id, user_id)
    device['active_user_id'] = current['active_user_id'] if current else None
    return device


@router.get('/api/devices/{device_id}/firmware')
async def firmware(device_id: str, request: Request, response: Response, user: User = Depends(require_session)):
    device = await owned_device(request, device_id, user.id)
    response.headers['Cache-Control'] = 'no-store'
    return await run_blocking(request.app.state.firmware.view, device)


async def mutate(request, device_id, user, callback):
    async with request.app.state.devices.locked_for_account(device_id, lock_for(request.app, user.id)):
        await require_session_write(request)
        device = await owned_device(request, device_id, user.id)
        if device.get('active_user_id') != user.id:
            raise HTTPException(409, '请先结束设备借用，再修改升级设置')
        try:
            return await finish_before_cancel(callback(device))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None


@router.put('/api/devices/{device_id}/firmware/policy')
async def policy(device_id: str, payload: Policy, request: Request, user: User = Depends(require_session_write)):
    return await mutate(request, device_id, user, lambda device: run_blocking(request.app.state.firmware.set_policy, device, payload.auto_update))


@router.post('/api/devices/{device_id}/firmware/update')
async def update(device_id: str, payload: UpdateRequest, request: Request, user: User = Depends(require_session_write)):
    if payload.confirm_device_id != device_id:
        raise HTTPException(400, '请确认要升级的设备编号')
    return await mutate(request, device_id, user, lambda device: run_blocking(request.app.state.firmware.request, device, payload.action, release_id=payload.release_id))


@router.post('/api/firmware/devices/{device_id}/check')
async def check(payload: Report, request: Request, response: Response, device=Depends(require_device_credential)):
    if not device['id'].startswith('deer-'):
        raise HTTPException(409, '本轮只支持小鹿 OTA')
    response.headers['Cache-Control'] = 'no-store'
    async with request.app.state.devices.locked(device['id']):
        # 重新核验凭证，避免请求排队期间解绑/撤销后领取旧任务。
        device = await require_device_credential(request)
        try:
            return await run_blocking(request.app.state.firmware.check, device, payload.model_dump())
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None


@router.get('/api/firmware/devices/{device_id}/download/{command_id}')
async def download(command_id: str, request: Request, device=Depends(require_device_credential)):
    try:
        image = await run_blocking(request.app.state.firmware.image, device, command_id)
    except ValueError:
        raise HTTPException(404, '固件不存在或升级任务已失效') from None
    async def chunks():
        # 解绑、转交或凭证撤销后及时中止下载；设备端校验整包哈希且切槽前再次确认任务。
        with image.open('rb') as stream:
            while True:
                try:
                    current = await require_device_credential(request)
                    await run_blocking(request.app.state.firmware.image, current, command_id)
                except (ValueError, HTTPException):
                    return
                part = stream.read(65536)
                if not part:
                    return
                yield part
    return StreamingResponse(chunks(), media_type='application/octet-stream', headers={
        'Content-Length': str(image.stat().st_size), 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
