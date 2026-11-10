"""历史记忆归账号所有，设备解绑或借用后仍只能由原账号查看。"""

import asyncio
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from access_control import require_session, require_session_write
from async_helpers import run_blocking
from identity_store import User

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
    return {'ok':True, 'user_id':user.id, 'device_id':device_id, 'summaries':context['summaries'],
            'memories':context['memories'], 'recent_messages':context['messages']}


class ClearRequest(BaseModel):
    confirm_device_id: str = Field(min_length=1, max_length=64)


@router.delete('/{device_id}')
async def clear(device_id: str, payload: ClearRequest, request: Request, user: User = Depends(require_session_write)):
    if payload.confirm_device_id != device_id:
        raise HTTPException(400, '请核对设备编号')
    await history_device(request,user.id,device_id)
    async def perform():
        registry = request.app.state.devices
        async with registry.locked(device_id):
            runtime = registry.devices.get(device_id)
            if runtime and runtime.user_id == user.id:
                await request.app.state.connections.revoke_device(device_id,code=1012,reason='当前账号已清除记忆')
                await registry.retire_locked(device_id)
            await request.app.state.memory_manager.clear_owner(user.id,device_id)
    await asyncio.shield(perform())
    return {'ok':True}
