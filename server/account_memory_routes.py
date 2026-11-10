"""历史记忆归账号所有，设备解绑或借用后仍只能由原账号查看。"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from access_control import require_session, require_session_write
from async_helpers import run_blocking, finish_before_cancel
from identity_store import User
from provider_routes import lock_for

router = APIRouter(prefix='/api/account/memories')


@router.get('')
async def history(request: Request, user: User = Depends(require_session)):
    return {'devices':await run_blocking(request.app.state.identities.history_devices, user.id)}


async def history_device(request, user_id, device_id):
    if not await run_blocking(request.app.state.identities.historical_device, device_id, user_id):
        raise HTTPException(404, '没有属于当前账号的设备历史')


@router.get('/{device_id}')
async def memory(device_id: str, request: Request, user: User = Depends(require_session)):
    await history_device(request, user.id, device_id)
    context = await request.app.state.memory_manager.context_for(user.id,device_id)
    policy = await run_blocking(request.app.state.memory_retention.preview,user.id,device_id)
    return {'ok':True, 'user_id':user.id, 'device_id':device_id, 'summaries':context['summaries'],
            'memories':context['memories'], 'recent_messages':context['messages'], 'retention':policy}


class ClearRequest(BaseModel):
    confirm_device_id: str = Field(min_length=1, max_length=64)


@router.delete('/{device_id}')
async def clear(device_id: str, payload: ClearRequest, request: Request, user: User = Depends(require_session_write)):
    if payload.confirm_device_id != device_id:
        raise HTTPException(400, '请核对设备编号')
    await history_device(request,user.id,device_id)
    async def perform():
        registry = request.app.state.devices
        runtime = registry.devices.get(device_id)
        if runtime and runtime.user_id == user.id:
            await request.app.state.connections.revoke_device(device_id,code=1012,reason='当前账号已清除记忆')
            await registry.retire_locked(device_id)
        await request.app.state.memory_manager.clear_owner(user.id,device_id)
        await run_blocking(request.app.state.memory_retention.reset,user.id,device_id)
    async with request.app.state.devices.locked_for_account(device_id,lock_for(request.app,user.id)):
        await require_session_write(request)
        await finish_before_cancel(perform())
    return {'ok':True}


class RetentionRequest(BaseModel):
    mode: str = Field(pattern='^(long_term|temporary)$')


@router.put('/{device_id}/retention')
async def policy(device_id: str, payload: RetentionRequest, request: Request, user: User = Depends(require_session_write)):
    await history_device(request,user.id,device_id)
    async with lock_for(request.app,user.id):
        await require_session_write(request)
        return await run_blocking(request.app.state.memory_retention.set_mode,user.id,device_id,payload.mode)


@router.post('/{device_id}/restore')
async def restore(device_id: str, request: Request, user: User = Depends(require_session_write)):
    await history_device(request,user.id,device_id)
    try:
        async with lock_for(request.app,user.id):
            await require_session_write(request)
            count = await run_blocking(request.app.state.memory_retention.restore,user.id,device_id)
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from None
    return {'ok':True,'restored_messages':count}
