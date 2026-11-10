"""独立管理边界；统计只读，写操作有确认、锁、权限复核及脱敏审计。"""

import asyncio
from contextlib import AsyncExitStack
from dataclasses import asdict
from pathlib import Path
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, StrictBool
from starlette.requests import HTTPConnection
from starlette.websockets import WebSocketState

from access_control import require_session, require_session_write, SESSION_COOKIE
from async_helpers import run_blocking
from identity_store import User, csrf_for_session
from provider_routes import lock_for


async def require_admin(connection: HTTPConnection):
    if connection.app.state.access.mode != 'accounts':
        raise HTTPException(404, '管理员后台仅在账号模式开放')
    user = await require_session(connection)
    if not await run_blocking(connection.app.state.admin_store.is_admin,user.id):
        raise HTTPException(403, '此账号未获管理员授权')
    return user


async def require_admin_write(connection: HTTPConnection):
    user = await require_admin(connection)
    await require_session_write(connection)
    return user


router = APIRouter(prefix='/api/admin', dependencies=[Depends(require_admin)])


@router.get('/session')
async def session(request: Request, user: User = Depends(require_admin)):
    return {'user':asdict(user),'csrf_token':csrf_for_session(request.cookies[SESSION_COOKIE])}


def backup_status(app):
    value = app.state.database_backups.status()
    # 不返回目录、回执内容或异常文本。这里只是备份任务状态，不是恢复演练证明。
    return {key:value.get(key) for key in ('enabled','running','last_success_at','last_failure_at',
            'last_error_type','backup_count','skipped_unverified','pruned')}


def decorate_runtime(app, devices):
    for device in devices:
        runtime = app.state.devices.devices.get(device['id'])
        valid = bool(runtime and not runtime._closed and runtime.user_id == device['active_user_id']
                     and device['active_user_enabled'] and not device['revoked'])
        device['audio_online'] = bool(valid and runtime.esp32_audio_connected())
        device['camera_online'] = bool(valid and runtime.esp32_camera_ws
                                       and runtime.esp32_camera_ws.client_state == WebSocketState.CONNECTED)
        device['online'] = device['audio_online'] or device['camera_online']
        if valid:
            device['voice_paused'] = bool(runtime.voice_paused)
        device.pop('active_user_enabled',None)
    return devices


async def online_device_ids(app):
    candidates = tuple(app.state.devices.devices)
    if not candidates:
        return ()
    devices = await run_blocking(app.state.admin_store.devices,device_ids=candidates)
    return tuple(device['id'] for device in decorate_runtime(app,devices) if device['online'])


@router.get('/overview')
async def overview(request: Request):
    store = request.app.state.admin_store
    result = await run_blocking(store.counts)
    online_ids = await online_device_ids(request.app)
    storage = await run_blocking(store.storage_bytes)
    def file_size(path):
        try:
            return path.stat().st_size
        except FileNotFoundError:
            return 0
    result['devices']['online'] = len(online_ids)
    result.update(uptime_seconds=max(0,time.monotonic()-request.app.state.admin_started_at),
                  storage={'database_bytes':await run_blocking(file_size,store.path),
                           'wal_bytes':await run_blocking(file_size,Path(str(store.path)+'-wal')),
                           **storage},
                  backups=backup_status(request.app),model_billing=None)
    return result


@router.get('/users')
async def users(request: Request, q: str = Query('',max_length=80),
                status: Literal['all','enabled','disabled'] = 'all',
                page: int = Query(1,ge=1), page_size: int = Query(20,ge=1,le=100)):
    return await run_blocking(request.app.state.admin_store.users,q,status,page,page_size)


@router.get('/devices')
async def devices(request: Request,q: str = Query('',max_length=80),
                  status: Literal['all','bound','unbound','online','offline'] = 'all',
                  page: int = Query(1,ge=1),page_size: int = Query(20,ge=1,le=100)):
    store = request.app.state.admin_store
    online_ids = ()
    if status in ('online','offline'):
        online_ids = await online_device_ids(request.app)
    result = await run_blocking(store.devices,q,status=status,page=page,page_size=page_size,online_ids=online_ids)
    decorate_runtime(request.app,result['items'])
    return result


@router.get('/storage')
async def storage(request: Request):
    return await run_blocking(request.app.state.admin_store.storage)


@router.get('/backups')
async def backups(request: Request):
    return backup_status(request.app)


@router.get('/audit')
async def audit(request: Request,q: str = Query('',max_length=80),page: int = Query(1,ge=1),page_size: int = Query(20,ge=1,le=100)):
    return await run_blocking(request.app.state.admin_store.audit,q,page,page_size)


class ConfirmUser(BaseModel):
    confirm_username: str = Field(min_length=1,max_length=32)


class UserStatus(ConfirmUser):
    disabled: StrictBool


class ConfirmDevice(BaseModel):
    confirm_device_id: str = Field(min_length=1,max_length=64)


class Voice(ConfirmDevice):
    paused: StrictBool


async def audited(request,user,target_type,target_id,action,perform,progress=None):
    """取消 HTTP 请求也完成清理；started 在进程中断时保留为未完成，绝不谎报成功。"""
    async def run():
        store = request.app.state.admin_store
        async with request.app.state.admin_mutation_lock:
            try:
                audit_id = await run_blocking(store.begin_action,user,target_type,target_id,action)
            except PermissionError:
                raise HTTPException(403,'管理员权限已撤销') from None
            try:
                result = await perform()
            except BaseException as exc:
                if progress and progress.get('committed'):
                    # 保留 started 表示尚未完整收尾，不能将已生效的权限变更记为全失败。
                    raise HTTPException(503,'状态变更已生效，连接、任务或设备状态同步未完成；请刷新后重试，审计保留未完成状态') from None
                await run_blocking(store.finish_action,audit_id,False)
                if isinstance(exc,HTTPException):
                    raise
                if isinstance(exc,PermissionError):
                    raise HTTPException(403,'管理员权限已撤销') from None
                if isinstance(exc,ValueError):
                    raise HTTPException(409,'操作与当前状态冲突，请刷新后核对目标、账号状态及管理员保护') from None
                if isinstance(exc,asyncio.CancelledError):
                    raise
                raise HTTPException(503,'操作未完成，请刷新状态后重试；已保留失败审计') from None
            await run_blocking(store.finish_action,audit_id,True)
            return result
    return await asyncio.shield(run())


async def user_action(request,user,user_id,payload,disabled=None):
    store = request.app.state.admin_store
    progress = {'committed':False}
    target = await run_blocking(store.user,user_id)
    if not target:
        raise HTTPException(404,'账号不存在')
    async def perform():
        if payload.confirm_username != target['username']:
            raise HTTPException(409,'请准确输入目标账号确认')
        async with lock_for(request.app,user_id):
            await run_blocking(store.change_user,user.id,user_id,disabled)
            progress['committed'] = True
            if disabled is not False:
                # 数据库会话先失效，阻止等待设备锁的旧请求；硬件认证另校验 disabled。
                await request.app.state.account_operations.cancel_user(user_id)
                await request.app.state.connections.revoke_user(user_id)
                registry = request.app.state.devices
                all_devices = await run_blocking(store.devices)
                affected = {d['id'] for d in all_devices if d['active_user_id'] == user_id or d['owner_user_id'] == user_id}
                active_targets = {d['id'] for d in all_devices if d['active_user_id'] == user_id}
                affected.update(r.device_id for r in list(registry.devices.values()) if r.user_id == user_id)
                async with AsyncExitStack() as stack:
                    for device_id in sorted(affected):
                        await stack.enter_async_context(registry.locked(device_id))
                    for device_id in sorted(affected):
                        # 重新连接不会悄悄恢复声音；解绑和 Key 都不变。
                        if device_id in active_targets:
                            await run_blocking(request.app.state.identities.save_voice_preference,user_id,device_id,True)
                        await request.app.state.connections.revoke_device(device_id)
                        await registry.retire_locked(device_id)
                await request.app.state.memory_manager.cancel_user_tasks(user_id)
                await request.app.state.knowledge.cancel_user_tasks(user_id)
            return {'ok':True}
    action = 'enable-user' if disabled is False else 'disable-user' if disabled else 'revoke-sessions'
    return await audited(request,user,'user',target['id'],action,perform,progress)


@router.post('/users/{user_id}/status')
async def user_status(user_id: str,payload: UserStatus,request: Request,user: User = Depends(require_admin_write)):
    return await user_action(request,user,user_id,payload,payload.disabled)


@router.post('/users/{user_id}/revoke-sessions')
async def revoke_sessions(user_id: str,payload: ConfirmUser,request: Request,user: User = Depends(require_admin_write)):
    return await user_action(request,user,user_id,payload)


async def device_action(request,user,device_id,payload,paused=None):
    store = request.app.state.admin_store
    progress = {'committed':False}
    found = await run_blocking(store.devices,device_ids=(device_id,))
    if not found:
        raise HTTPException(404,'设备不存在')
    def mark_committed():
        progress['committed'] = True
    async def perform():
        if payload.confirm_device_id != device_id:
            raise HTTPException(409,'请准确输入目标设备编号确认')
        # 与 resolve_runtime 相同的用户锁→设备锁顺序，防止与停用/Key 更新互锁。
        initial = found[0]
        registry = request.app.state.devices
        runtime = registry.devices.get(device_id)
        lock_user_id = initial['active_user_id'] or (runtime.user_id if runtime else None)
        if paused is not None and not initial['active_user_id']:
            raise ValueError('设备尚未绑定')
        async with AsyncExitStack() as locks:
            if lock_user_id is not None:
                await locks.enter_async_context(lock_for(request.app,lock_user_id))
            await locks.enter_async_context(registry.locked(device_id))
            current = next(iter(await run_blocking(store.devices,device_ids=(device_id,))),None)
            if not current or current['active_user_id'] != initial['active_user_id'] or current['owner_user_id'] != initial['owner_user_id'] or current['revoked']:
                raise ValueError('设备使用权已改变')
            if not await run_blocking(store.is_admin,user.id):
                raise PermissionError()
            if paused is None:
                if current['owner_user_id'] is not None:
                    await run_blocking(request.app.state.identities.unbind_device,device_id,current['owner_user_id'])
                # 已解绑时仍重试收尾，不重复变更 access_epoch 或设备凭证。
                mark_committed()
                await request.app.state.connections.revoke_device(device_id,code=4403,reason='设备已解绑')
                await registry.retire_locked(device_id)
                return {'ok':True}
            if not current['active_user_enabled']:
                raise ValueError('当前使用者已停用')
            runtime = registry.devices.get(device_id)
            if runtime and runtime.user_id != current['active_user_id']:
                raise ValueError('运行身份已改变')
            if runtime:
                await runtime.set_voice_paused(paused,on_committed=mark_committed)
            else:
                # 离线设备只保存偏好；不创建运行时，更不为统计或操作建立模型连接。
                await run_blocking(request.app.state.identities.save_voice_preference,current['active_user_id'],device_id,paused)
                mark_committed()
            return {'ok':True,'paused':paused}
    return await audited(request,user,'device',device_id,'unbind-device' if paused is None else 'pause-voice' if paused else 'resume-voice',perform,progress)


@router.post('/devices/{device_id}/voice')
async def voice(device_id: str,payload: Voice,request: Request,user: User = Depends(require_admin_write)):
    return await device_action(request,user,device_id,payload,payload.paused)


@router.post('/devices/{device_id}/unbind')
async def unbind(device_id: str,payload: ConfirmDevice,request: Request,user: User = Depends(require_admin_write)):
    return await device_action(request,user,device_id,payload)
