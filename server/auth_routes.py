"""网页登录接口。账号通过本机管理工具创建，不提供公开注册。"""

from async_helpers import run_blocking
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from access_control import SESSION_COOKIE, require_origin, require_session, require_session_write
from identity_store import RateLimited, User, csrf_for_session

router = APIRouter(prefix="/api/auth")


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=128, repr=False)


@router.get("/mode")
async def mode(request: Request):
    return {"mode": request.app.state.access.mode}


@router.post("/login")
async def login(payload: LoginRequest, request: Request, response: Response):
    require_origin(request)
    try:
        grant = await run_blocking(
            request.app.state.identities.login, payload.username, payload.password,
            client_key=request.client.host if request.client else "unknown",
        )
    except RateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": str(exc.retry_after)}) from None
    if grant is None:
        raise HTTPException(status_code=401, detail="账号或密码错误")
    is_admin = await run_blocking(request.app.state.admin_store.is_admin, grant.user.id)
    previous = request.cookies.get(SESSION_COOKIE)
    if previous:
        await run_blocking(request.app.state.identities.revoke_session, previous)
        await request.app.state.connections.revoke_session(previous)
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
