"""受管理员保护的备份检查；不提供文件路径、下载或在线恢复。"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from typing import Literal

from access_control import SESSION_COOKIE
from admin_routes import audited, backup_status, read_metadata, require_admin, require_admin_write
from async_helpers import run_blocking


router = APIRouter(prefix='/api/admin', dependencies=[Depends(require_admin)])


class BackupConfirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirm: Literal['检查并备份']


def verified_history(service):
    entries, skipped = service.repository.verified_entries()
    items = [{'created_at': created, 'bytes': path.stat().st_size, 'verified': True}
             for created, path, _ in reversed(entries[-20:])]
    return {'items': items, 'total': len(entries), 'skipped_unverified': skipped,
            'retain': service.settings.retain, 'interval_seconds': service.settings.interval_seconds,
            'includes_provider_key': False}


@router.get('/backups/history')
async def history(request: Request):
    result = await read_metadata(verified_history, request.app.state.database_backups)
    result['status'] = backup_status(request.app)
    return result


@router.post('/backups/run')
async def run_backup(payload: BackupConfirmation, request: Request, user=Depends(require_admin_write)):
    service = request.app.state.database_backups
    if service.settings.directory is None:
        raise HTTPException(409, '尚未配置备份目录，请先由服务器维护者启用备份')
    progress = {'committed': False}

    def authorize_transaction():
        with request.app.state.identities._transaction() as db:
            request.app.state.admin_store.require_writer(db, user.id, request.cookies.get(SESSION_COOKIE, ''))

    async def authorize():
        await require_admin_write(request)
        await run_blocking(authorize_transaction)

    async def perform():
        created = await service.run_once(authorize=authorize)
        progress['committed'] = True
        return {'ok': True, 'created': created, 'status': backup_status(request.app),
                'message': '备份已创建并校验' if created else '已有符合间隔策略的近期备份，已完成校验'}

    return await audited(request, user, 'system', 'database-backups', 'check-backup', perform, progress)
