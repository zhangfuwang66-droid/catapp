"""独立管理登录；显式角色在会话签发事务内核验，公开资源由应用精确放行。"""

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from starlette.requests import HTTPConnection

from access_control import SESSION_COOKIE, require_origin, require_session_write
from admin_pages import ADMIN_CSP, admin_login_html
from admin_routes import require_admin
from async_helpers import finish_before_cancel, run_blocking
from auth_routes import finish_login
from identity_store import RateLimited, csrf_for_session


async def require_accounts(connection: HTTPConnection):
    if connection.app.state.access.mode != 'accounts':
        raise HTTPException(404, '管理员后台仅在账号模式开放')


router = APIRouter(dependencies=[Depends(require_accounts)])


class AdminLogin(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: StrictStr = Field(min_length=1, max_length=128)
    password: StrictStr = Field(min_length=1, max_length=128, repr=False)


class AdminLoginForbidden(PermissionError):
    pass


def validate_destination(request, *, page=False):
    allowed = {'notice': {'expired', 'login', 'logout'}} if page else {}
    for key in request.query_params:
        values = request.query_params.getlist(key)
        if key not in allowed or len(values) != 1 or values[0] not in allowed[key]:
            raise HTTPException(422, '管理登录只允许固定站内入口，请从管理登录页重试')


@router.get('/admin/login', response_class=HTMLResponse)
async def login_page(request: Request):
    validate_destination(request, page=True)
    try:
        await require_admin(request)
    except HTTPException as exc:
        if exc.status_code not in (401, 403):
            raise
    else:
        return RedirectResponse('/admin', status_code=303, headers={'Cache-Control': 'no-store'})
    try:
        content = admin_login_html()
    except OSError:
        raise HTTPException(503, '管理登录页面暂不可用，请稍后重试') from None
    return HTMLResponse(content, headers={'Cache-Control': 'no-store', 'Content-Security-Policy': ADMIN_CSP})


@router.post('/api/admin/auth/login')
async def login(payload: AdminLogin, request: Request, response: Response):
    require_origin(request)
    validate_destination(request)
    identities = request.app.state.identities
    issued = {}

    def require_admin_role(db, user_id):
        if not request.app.state.admin_store._is_admin(db, user_id):
            raise AdminLoginForbidden()
        return True

    async def perform():
        grant = await run_blocking(identities.login, payload.username, payload.password,
                                  client_key=request.client.host if request.client else 'unknown',
                                  authorize=require_admin_role)
        if grant is None:
            raise HTTPException(401, '账号或密码错误')
        issued['token'] = grant.token
        previous = request.cookies.get(SESSION_COOKIE)
        if previous:
            await run_blocking(identities.revoke_session, previous)
            await request.app.state.connections.revoke_session(previous)
        return grant

    try:
        grant = await finish_login(identities, perform, issued)
    except BaseException as exc:
        if isinstance(exc, asyncio.CancelledError):
            raise
        if isinstance(exc, RateLimited):
            raise HTTPException(429, str(exc), headers={'Retry-After': str(exc.retry_after)}) from None
        if isinstance(exc, AdminLoginForbidden):
            raise HTTPException(403, '此账号未获管理员授权') from None
        if isinstance(exc, HTTPException):
            raise
        if not isinstance(exc, Exception):
            raise
        raise HTTPException(503, '管理登录暂时无法完成，请稍后重试') from None
    response.set_cookie(SESSION_COOKIE, grant.token, max_age=7 * 86400, httponly=True,
                        secure=request.url.scheme == 'https', samesite='strict', path='/')
    response.headers['Cache-Control'] = 'no-store'
    return {'ok': True, 'user': asdict(grant.user), 'csrf_token': csrf_for_session(grant.token)}


@router.post('/api/admin/auth/logout')
async def logout(request: Request, response: Response, _user=Depends(require_session_write)):
    # 角色被撤销后仍可安全退出；会话、同源及 CSRF 校验不能省略。
    validate_destination(request)
    async def perform():
        token = request.cookies[SESSION_COOKIE]
        await run_blocking(request.app.state.identities.revoke_session, token)
        await request.app.state.connections.revoke_session(token)
    try:
        await finish_before_cancel(perform())
    except Exception:
        raise HTTPException(503, '退出状态尚未完整同步，请刷新后重试') from None
    response.delete_cookie(SESSION_COOKIE, path='/', secure=request.url.scheme == 'https',
                           httponly=True, samesite='strict')
    response.headers['Cache-Control'] = 'no-store'
    return {'ok': True}
