"""可独立注册的管理员账号 CRUD；复用统一授权和脱敏审计。"""

import sqlite3
import uuid
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from access_control import SESSION_COOKIE
from admin_accounts_store import AdminAccountsStore, AccountConflict, AccountMissing
from admin_routes import audited, require_admin, require_admin_write
from admin_store import AdminSessionExpired
from async_helpers import run_blocking
from identity_store import User
from provider_routes import lock_for


router = APIRouter(prefix='/api/admin', dependencies=[Depends(require_admin)])


class ConfirmAccount(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirm_username: StrictStr = Field(min_length=1, max_length=32)


class RenameAccount(ConfirmAccount):
    username: StrictStr = Field(min_length=1, max_length=128)


class CreateAccount(RenameAccount):
    password: StrictStr = Field(min_length=10, max_length=128, repr=False)


def store_for(request):
    return AdminAccountsStore(request.app.state.admin_store)


def normalized_username(request, username):
    try:
        return request.app.state.identities.normalize_username(username)
    except ValueError:
        raise HTTPException(422, '账号需为 3～32 位英文字母、数字、下划线、点或连字符，首位为字母或数字') from None


async def call_store(function, *args):
    try:
        return await run_blocking(function, *args)
    except AdminSessionExpired:
        raise HTTPException(401, '管理员会话已失效，请重新登录') from None
    except PermissionError:
        raise HTTPException(403, '管理员权限已撤销') from None
    except AccountMissing:
        raise HTTPException(404, '账号不存在') from None
    except AccountConflict as exc:
        raise HTTPException(409, str(exc)) from None


async def account_audit(request, user, target_id, action, perform, progress, *, locked=None):
    return await audited(request, user, 'user', target_id, action, perform, progress,
                         locked=locked)


def target_lock(request, user_id):
    @asynccontextmanager
    async def locked():
        async with lock_for(request.app, user_id):
            yield
    return locked


async def cleanup_user(app, user_id):
    await app.state.account_operations.cancel_user(user_id)
    await app.state.connections.revoke_user(user_id)
    registry = app.state.devices
    candidates = sorted(runtime.device_id for runtime in list(registry.devices.values()) if runtime.user_id == user_id)
    for device_id in candidates:
        async with registry.locked(device_id):
            runtime = registry.devices.get(device_id)
            if runtime and runtime.user_id == user_id:
                await app.state.connections.revoke_device(device_id)
                await registry.retire_locked(device_id)
    await app.state.memory_manager.cancel_user_tasks(user_id)
    await app.state.knowledge.cancel_user_tasks(user_id)


@router.post('/users', status_code=201)
async def create_account(payload: CreateAccount, request: Request, user: User = Depends(require_admin_write)):
    username = normalized_username(request, payload.username)
    try:
        request.app.state.identities.validate_password(payload.password)
    except ValueError:
        raise HTTPException(422, '密码需为 10～128 个字符且不能全为空白') from None
    user_id = 'usr_' + uuid.uuid4().hex
    progress = {'committed': False}

    async def perform():
        if payload.confirm_username != username:
            raise HTTPException(409, '请准确输入规范化后的新账号名确认')
        created = await call_store(store_for(request).create, user.id, request.cookies[SESSION_COOKIE],
                                   user_id, username, payload.password)
        progress['committed'] = True
        return {'ok': True, 'user': created}

    return await account_audit(request, user, user_id, 'create-user', perform, progress)


@router.get('/users/{user_id}')
async def account_detail(user_id: str, request: Request, user: User = Depends(require_admin)):
    try:
        return await call_store(store_for(request).detail, user.id, request.cookies[SESSION_COOKIE], user_id)
    except (sqlite3.Error, OSError):
        raise HTTPException(503, '账号数据暂时无法读取，请稍后重试') from None


@router.patch('/users/{user_id}')
async def rename_account(user_id: str, payload: RenameAccount, request: Request, user: User = Depends(require_admin_write)):
    username = normalized_username(request, payload.username)
    progress = {'committed': False}

    async def perform():
        changed = await call_store(store_for(request).rename, user.id, request.cookies[SESSION_COOKIE],
                                   user_id, username, payload.confirm_username)
        progress['committed'] = True
        await cleanup_user(request.app, user_id)
        return {'ok': True, 'user': changed, 'sessions_revoked': True}

    return await account_audit(request, user, user_id, 'rename-user', perform, progress,
                               locked=target_lock(request, user_id))


@router.delete('/users/{user_id}')
async def delete_account(user_id: str, payload: ConfirmAccount, request: Request, user: User = Depends(require_admin_write)):
    progress = {'committed': False}

    async def perform():
        store = store_for(request)
        arguments = (user.id, request.cookies[SESSION_COOKIE], user_id, payload.confirm_username)
        await call_store(store.check_delete, *arguments)
        # 先清理已停用空账号的旧任务，避免删除后才发现收尾失败而无法重试。
        await cleanup_user(request.app, user_id)
        await require_admin_write(request)
        await call_store(store.delete, *arguments)
        progress['committed'] = True
        return {'ok': True, 'deleted_user_id': user_id}

    return await account_audit(request, user, user_id, 'delete-user', perform, progress,
                               locked=target_lock(request, user_id))
