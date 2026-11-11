"""网页登录接口。账号通过本机管理工具创建，不提供公开注册。"""

import asyncio

from async_helpers import finish_before_cancel, run_blocking
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from access_control import SESSION_COOKIE, require_origin, require_session, require_session_write
from identity_store import RateLimited, User, csrf_for_session

router = APIRouter(prefix="/api/auth")


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=128, repr=False)


async def finish_login(identities, perform, issued):
    # 必须在进入共享 executor 之前拒绝，避免攻击请求占满队列拖住会话和设备鉴权。
    with identities.login_admission():
        try:
            return await finish_before_cancel(perform())
        except BaseException as exc:
            if issued.get('token'):
                try:
                    await run_blocking(identities.revoke_session, issued['token'])
                except Exception:
                    if isinstance(exc, Exception):
                        raise HTTPException(503, '登录暂时无法完成，请稍后重试') from None
            raise


@router.get("/mode")
async def mode(request: Request):
    return {"mode": request.app.state.access.mode}


@router.post("/login")
async def login(payload: LoginRequest, request: Request, response: Response):
    require_origin(request)
    identities = request.app.state.identities
    issued = {}

    async def perform():
        grant = await run_blocking(identities.login, payload.username, payload.password,
                                  client_key=request.client.host if request.client else 'unknown')
        if grant is None:
            raise HTTPException(status_code=401, detail="账号或密码错误")
        issued['token'] = grant.token
        is_admin = await run_blocking(request.app.state.admin_store.is_admin, grant.user.id)
        previous = request.cookies.get(SESSION_COOKIE)
        if previous:
            await run_blocking(identities.revoke_session, previous)
            await request.app.state.connections.revoke_session(previous)
        return grant, is_admin

    try:
        grant, is_admin = await finish_login(identities, perform, issued)
    except BaseException as exc:
        if isinstance(exc, asyncio.CancelledError):
            raise
        if isinstance(exc, RateLimited):
            raise HTTPException(429, str(exc), headers={'Retry-After': str(exc.retry_after)}) from None
        if isinstance(exc, HTTPException):
            raise
        if not isinstance(exc, Exception):
            raise
        raise HTTPException(503, '登录暂时无法完成，请稍后重试') from None
    response.set_cookie(SESSION_COOKIE, grant.token, max_age=7 * 86400, httponly=True,
                        secure=request.url.scheme == "https", samesite="strict", path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True, "user": asdict(grant.user), "csrf_token": csrf_for_session(grant.token), "is_admin": is_admin}


@router.get("/session")
async def session(request: Request, response: Response, user: User = Depends(require_session)):
    response.headers["Cache-Control"] = "no-store"
    is_admin = await run_blocking(request.app.state.admin_store.is_admin, user.id)
    return {"ok": True, "user": asdict(user), "csrf_token": csrf_for_session(request.cookies[SESSION_COOKIE]), "is_admin": is_admin}


@router.post("/logout")
async def logout(request: Request, response: Response, user: User = Depends(require_session_write)):
    await run_blocking(request.app.state.identities.revoke_session, request.cookies[SESSION_COOKIE])
    await request.app.state.connections.revoke_session(request.cookies[SESSION_COOKIE])
    response.delete_cookie(SESSION_COOKIE, path="/", secure=request.url.scheme == "https", httponly=True, samesite="strict")
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}
