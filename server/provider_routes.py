"""登录账号的模型设置；响应、验证错误与日志不包含明文凭证。"""

import asyncio
from contextlib import suppress
import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from access_control import require_session, require_session_write
from async_helpers import run_blocking
from identity_store import RateLimited, User
from provider_settings import MissingProviderKey, VaultUnavailable, validate_api_key

router = APIRouter(prefix="/api/account/model")


class KeyRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=512, repr=False)


async def retire_user_models(app, user_id):
    for runtime in list(app.state.devices.devices.values()):
        if runtime.user_id == user_id:
            async with app.state.devices.locked(runtime.device_id):
                await app.state.connections.revoke_device(runtime.device_id, code=1012, reason="模型配置已更新，请重连")
                await app.state.devices.retire_locked(runtime.device_id)


def lock_for(app, user_id):
    return app.state.model_locks.setdefault(user_id, asyncio.Lock())


@router.get("")
async def settings(request: Request, response: Response, user: User = Depends(require_session)):
    response.headers["Cache-Control"] = "no-store"
    return await run_blocking(request.app.state.provider_settings.status, user.id)


async def change(request, user_id, key=None):
    async with lock_for(request.app, user_id):
        store = request.app.state.provider_settings
        status = await run_blocking(store.delete, user_id) if key is None else await run_blocking(store.save, user_id, key)
        await retire_user_models(request.app, user_id)
        return status


@router.put("")
async def save(payload: KeyRequest, request: Request, response: Response, user: User = Depends(require_session_write)):
    response.headers["Cache-Control"] = "no-store"
    try:
        key = validate_api_key(payload.api_key)
        return await asyncio.shield(change(request, user.id, key))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.delete("")
async def delete(request: Request, response: Response, user: User = Depends(require_session_write)):
    response.headers["Cache-Control"] = "no-store"
    return await asyncio.shield(change(request, user.id))


@router.post("/test")
async def test(request: Request, response: Response, user: User = Depends(require_session_write)):
    response.headers["Cache-Control"] = "no-store"
    try:
        await run_blocking(request.app.state.identities.consume_rate_limit, "model-test:" + user.id, limit=3, window=60)
    except RateLimited as exc:
        raise HTTPException(status_code=429, detail="测试较频繁，请稍后再试", headers={"Retry-After": str(exc.retry_after)}) from None
    async with lock_for(request.app, user.id):
        try:
            key = await run_blocking(request.app.state.providers.key, user.id)
        except (MissingProviderKey, VaultUnavailable) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        from openai import AsyncOpenAI
        from omni_realtime_client import OmniRealtimeSession
        async def ignore(*args):
            return None
        realtime = None
        try:
            async with asyncio.timeout(20):
                async with AsyncOpenAI(api_key=key, base_url=os.getenv("DASHSCOPE_COMPAT_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                                       max_retries=0, timeout=8) as client:
                    await client.chat.completions.create(model=os.getenv("QWEN_TEXT_MODEL", "qwen-plus"),
                        messages=[{"role": "user", "content": "回复 OK"}], max_tokens=8)
                realtime = OmniRealtimeSession(loop=asyncio.get_running_loop(), api_key=key, system_prompt="请简短回答。",
                    on_input_transcript=ignore, on_output_text_delta=ignore, on_output_audio=ignore,
                    on_response_started=ignore, on_response_done=ignore, on_speech_started=ignore, on_error=ignore)
                await realtime.ensure_connected()
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            message = "Key 无效或地域不匹配" if status in (401, 403) else "文本或实时语音连接未通过，请检查额度、模型权限和服务地域"
            raise HTTPException(status_code=400, detail=message) from None
        finally:
            if realtime is not None:
                with suppress(Exception):
                    await realtime.close()
    return {"ok": True, "message": "文本与实时语音连接均通过；测试会产生少量模型用量"}
