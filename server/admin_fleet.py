"""设备登记与生命周期管理；私密凭证仅在签发响应中出现一次。"""

from contextlib import AsyncExitStack, asynccontextmanager
import secrets
import sqlite3
import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from access_control import SESSION_COOKIE
from admin_routes import audited, decorate_runtime, read_metadata, require_admin, require_admin_write
from async_helpers import run_blocking
from device_identity import validate_device_id
from identity_store import token_digest
from provider_routes import lock_for


router = APIRouter(prefix='/api/admin', dependencies=[Depends(require_admin)])


class FleetConfirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirm_device_id: str = Field(min_length=1, max_length=64)


class FleetName(FleetConfirmation):
    display_name: str = Field(min_length=1, max_length=80)

    @field_validator('display_name')
    @classmethod
    def visible_name(cls, value):
        value = value.strip()
        if not value or any(unicodedata.category(char).startswith('C') for char in value):
            raise ValueError('设备名称需为 1～80 个可显示字符')
        return value


class FleetRegistration(FleetName):
    id: str = Field(min_length=1, max_length=64)

    @field_validator('id')
    @classmethod
    def valid_id(cls, value):
        return validate_device_id(value)


class FleetStore:
    def __init__(self, admins):
        self.admins = admins
        self.identities = admins.identities

    def _snapshot(self, db, device_id):
        row = db.execute('SELECT * FROM devices WHERE id=?', (device_id,)).fetchone()
        if row is None:
            return None
        snapshot = self.identities.decorate_device(db, row)
        # 删除后同编号可以重新登记，代次和时间可能相同；用内部凭证版本识别新登记。
        # 仅供事务核对，详情与列表不得输出此字段或凭证散列。
        snapshot['_credential_version'] = tuple(item[0] for item in db.execute(
            'SELECT token_hash FROM device_credentials WHERE device_id=? ORDER BY token_hash', (device_id,)))
        return snapshot

    def snapshot(self, device_id):
        with self.identities._transaction() as db:
            return self._snapshot(db, device_id)

    @staticmethod
    def _history(db, device_id):
        tables = {'memberships': 'device_memberships', 'loans': 'device_loans',
                  'conversations': 'conversations', 'memories': 'memories'}
        return {name: db.execute('SELECT COUNT(*) FROM ' + table + ' WHERE device_id=?',
                                (device_id,)).fetchone()[0] for name, table in tables.items()}

    def _delete_blockers(self, db, device):
        blockers = []
        if device['owner_user_id'] is not None:
            blockers.append('设备仍绑定账号，请先撤销设备')
        if device['revoked_at'] is None:
            blockers.append('设备凭证尚未撤销')
        history = self._history(db, device['id'])
        if history['memberships'] or history['loans']:
            blockers.append('设备存在使用或借用历史，保留撤销记录以保护历史关联')
        if history['conversations'] or history['memories']:
            blockers.append('设备存在聊天记忆，不允许删除关联记录')
        if db.execute('SELECT 1 FROM memory_policies WHERE device_id=? LIMIT 1', (device['id'],)).fetchone():
            blockers.append('设备存在记忆保留策略')
        return blockers

    @staticmethod
    def _lifecycle(device, credentials):
        revoked = device['revoked_at'] is not None
        usable_credential = credentials['active_count'] > 0 and not revoked
        bound = device['owner_user_id'] is not None
        if not usable_credential:
            state = 'revoked' if revoked else 'credential_missing'
            next_action = 'unbind_to_transfer' if bound and not revoked else 'rotate_credential_and_provision'
            message = ('硬件凭证不可用；请先解绑，再签发新凭证并通过 USB 安全配发。' if bound and not revoked else
                       '硬件凭证不可用；请由管理员签发新凭证，再通过 USB 安全配发。旧凭证不能用于重新配对。')
        elif not bound:
            state, next_action = 'unbound', 'wait_for_pairing'
            message = '设备未绑定账号；持有匹配凭证的设备联网后可申请配对码。刚签发的新凭证需先通过 USB 配发。'
        else:
            state = 'borrowed' if device['loan_active'] else 'bound'
            next_action = 'unbind_to_transfer'
            message = '转交设备请使用解绑，保留 Wi-Fi 与硬件凭证；安全停用会撤销凭证，不会自动显示配对码。'
        return {'state': state, 'pairing_available': usable_credential and not bound,
                'recovery_requires_usb': not usable_credential,
                'can_unbind': not revoked and bound,
                'can_rotate_credential': revoked or not bound,
                'next_action': next_action, 'message': message}

    def detail(self, device_id):
        with self.identities._transaction() as db:
            device = self._snapshot(db, device_id)
            if not device:
                return None
            credentials = dict(db.execute('''SELECT
                COALESCE(SUM(revoked_at IS NULL),0) AS active_count,
                COALESCE(SUM(revoked_at IS NOT NULL),0) AS revoked_count,
                MAX(created_at) AS last_issued_at FROM device_credentials WHERE device_id=?''',
                (device_id,)).fetchone())
            active_pairings = db.execute('''SELECT COUNT(*) FROM device_pairings WHERE device_id=?
                AND claimed_at IS NULL AND invalidated_at IS NULL AND expires_at>?''',
                (device_id, self.identities.clock())).fetchone()[0]
            blockers = self._delete_blockers(db, device)
            return {'credentials': credentials, 'pairings': {'active_count': active_pairings},
                    'lifecycle': self._lifecycle(device, credentials),
                    'history': self._history(db, device_id), 'can_delete': not blockers,
                    'delete_blockers': blockers}

    def mutate(self, actor, session_token, device_id, operation, *, expected=None, display_name=None):
        """授权、目标版本核对与写入共用一个 BEGIN IMMEDIATE 事务。"""
        with self.identities._transaction(write=True) as db:
            self.admins.require_writer(db, actor.id, session_token)
            current = self._snapshot(db, device_id)
            now = self.identities.clock()
            if operation == 'register':
                if current is not None:
                    raise HTTPException(409, '设备编号已登记，请核对现有记录；不要重复登记')
                db.execute('INSERT INTO devices(id,display_name,created_at) VALUES(?,?,?)',
                           (device_id, display_name, now))
            else:
                if current is None:
                    raise HTTPException(404, '设备不存在')
                if expected is None or any(current[key] != expected[key] for key in
                        ('owner_user_id', 'active_user_id', 'access_epoch', 'revoked_at',
                         'created_at', '_credential_version')):
                    raise HTTPException(409, '设备归属或状态已改变，请刷新后重新确认')
            if operation in ('register', 'rotate'):
                if operation == 'rotate' and current['owner_user_id'] is not None and current['revoked_at'] is None:
                    raise HTTPException(409, '请先解绑设备，再轮换硬件凭证')
                if operation == 'rotate':
                    # 用户安全停用保留旧主人，恢复时一次性结束旧使用权；历史不删除。
                    self.identities._end_loans(db, device_id)
                    db.execute('''UPDATE device_memberships SET ended_at=?
                        WHERE device_id=? AND ended_at IS NULL''', (now, device_id))
                    db.execute('UPDATE devices SET owner_user_id=NULL,bound_at=NULL WHERE id=?', (device_id,))
                    db.execute('UPDATE device_preferences SET voice_paused=1 WHERE device_id=?', (device_id,))
                token = secrets.token_urlsafe(32)
                db.execute('''UPDATE device_credentials SET revoked_at=?
                    WHERE device_id=? AND revoked_at IS NULL''', (now, device_id))
                db.execute('''UPDATE device_pairings SET invalidated_at=?
                    WHERE device_id=? AND claimed_at IS NULL AND invalidated_at IS NULL''', (now, device_id))
                db.execute('INSERT INTO device_credentials(token_hash,device_id,created_at) VALUES(?,?,?)',
                           (token_digest(token), device_id, now))
                db.execute('UPDATE devices SET revoked_at=NULL,access_epoch=access_epoch+1 WHERE id=?', (device_id,))
                db.execute('DELETE FROM auth_rate_limits WHERE bucket=?', ('pair-request:' + device_id,))
                return {'ok': True, 'device_id': device_id,
                        'credential': {'device_id': device_id, 'token': token}, 'credential_once': True,
                        'binding_cleared': True, 'requires_usb_provisioning': True,
                        'message': '新凭证仅显示一次，请通过 USB 安全配发后联网获取配对码；旧凭证已失效，语音不会自动恢复。'}
            if operation == 'rename':
                db.execute('UPDATE devices SET display_name=? WHERE id=?', (display_name, device_id))
            elif operation == 'revoke':
                self.identities._end_loans(db, device_id)
                db.execute('''UPDATE device_memberships SET ended_at=?
                    WHERE device_id=? AND ended_at IS NULL''', (now, device_id))
                db.execute('''UPDATE device_credentials SET revoked_at=?
                    WHERE device_id=? AND revoked_at IS NULL''', (now, device_id))
                db.execute('''UPDATE device_pairings SET invalidated_at=?
                    WHERE device_id=? AND claimed_at IS NULL AND invalidated_at IS NULL''', (now, device_id))
                db.execute('''UPDATE devices SET owner_user_id=NULL,bound_at=NULL,
                    access_epoch=access_epoch+CASE WHEN revoked_at IS NULL THEN 1 ELSE 0 END,
                    revoked_at=COALESCE(revoked_at,?) WHERE id=?''', (now, device_id))
            elif operation == 'delete':
                blockers = self._delete_blockers(db, current)
                if blockers:
                    raise HTTPException(409, '；'.join(blockers))
                for table in ('device_preferences', 'device_pairings', 'device_credentials'):
                    db.execute('DELETE FROM ' + table + ' WHERE device_id=?', (device_id,))
                db.execute('DELETE FROM auth_rate_limits WHERE bucket=?', ('pair-request:' + device_id,))
                try:
                    db.execute('DELETE FROM devices WHERE id=?', (device_id,))
                except sqlite3.IntegrityError:
                    raise HTTPException(409, '设备仍有关联数据，请保留撤销记录') from None
            else:
                raise ValueError('未知设备操作')
            if operation == 'revoke':
                return {'ok': True, 'requires_usb_provisioning': True,
                        'message': '已安全停用并撤销硬件凭证；不会自动显示配对码。恢复使用需签发新凭证并通过 USB 配发，历史记忆保留。'}
            if operation == 'delete':
                return {'ok': True, 'requires_usb_provisioning': True,
                        'message': '空设备记录已删除，旧凭证永久失效；同一设备编号可重新登记并通过 USB 配发新凭证。'}
            return {'ok': True}


async def fleet_action(request, user, device_id, payload, operation):
    if payload.confirm_device_id != device_id:
        raise HTTPException(409, '请准确输入目标设备编号确认')
    store = FleetStore(request.app.state.admin_store)
    initial = await read_metadata(store.snapshot, device_id)
    if operation != 'register' and initial is None:
        raise HTTPException(404, '设备不存在')
    progress = {'committed': False}

    @asynccontextmanager
    async def locked():
        registry = request.app.state.devices
        runtime = registry.devices.get(device_id)
        user_id = (initial['active_user_id'] if initial else None) or (runtime.user_id if runtime else None)
        async with AsyncExitStack() as stack:
            if user_id is not None:
                await stack.enter_async_context(lock_for(request.app, user_id))
            await stack.enter_async_context(registry.locked(device_id))
            yield

    async def perform():
        registry = request.app.state.devices
        if operation == 'delete' and device_id in registry.devices:
            raise HTTPException(409, '设备连接尚未清理，请先重试撤销设备并核对状态')
        result = await run_blocking(store.mutate, user, request.cookies.get(SESSION_COOKIE, ''),
                                   device_id, operation, expected=initial,
                                   display_name=getattr(payload, 'display_name', None))
        progress['committed'] = True
        if operation in ('revoke', 'rotate'):
            await request.app.state.connections.revoke_device(device_id, code=4403, reason='设备凭证已更新')
            await registry.retire_locked(device_id)
        return result

    action = {'register': 'register-device', 'rename': 'rename-device', 'revoke': 'revoke-device',
              'rotate': 'rotate-device-credential', 'delete': 'delete-device'}[operation]
    return await audited(request, user, 'device', device_id, action, perform, progress, locked=locked)


@router.post('/devices', status_code=201)
async def register(payload: FleetRegistration, request: Request, user=Depends(require_admin_write)):
    return await fleet_action(request, user, payload.id, payload, 'register')


@router.get('/devices/{device_id}')
async def detail(device_id: str, request: Request):
    store = request.app.state.admin_store
    info = await read_metadata(FleetStore(store).detail, device_id)
    devices = await read_metadata(store.devices, device_ids=(device_id,))
    if not info or not devices:
        raise HTTPException(404, '设备不存在')
    info['device'] = decorate_runtime(request.app, devices)[0]
    if device_id in request.app.state.devices.devices:
        info['can_delete'] = False
        info['delete_blockers'].append('设备连接尚未清理')
    return info


@router.patch('/devices/{device_id}')
async def rename(device_id: str, payload: FleetName, request: Request, user=Depends(require_admin_write)):
    return await fleet_action(request, user, device_id, payload, 'rename')


@router.post('/devices/{device_id}/revoke')
async def revoke(device_id: str, payload: FleetConfirmation, request: Request, user=Depends(require_admin_write)):
    return await fleet_action(request, user, device_id, payload, 'revoke')


@router.post('/devices/{device_id}/rotate-credential')
async def rotate(device_id: str, payload: FleetConfirmation, request: Request, user=Depends(require_admin_write)):
    return await fleet_action(request, user, device_id, payload, 'rotate')


@router.delete('/devices/{device_id}')
async def delete(device_id: str, payload: FleetConfirmation, request: Request, user=Depends(require_admin_write)):
    return await fleet_action(request, user, device_id, payload, 'delete')
