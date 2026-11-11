"""管理首页真实聚合；不将管理操作数误报为聊天量或模型费用。"""

from datetime import datetime, timedelta, timezone
import platform
import shutil

from fastapi import APIRouter, Depends, Request

from admin_routes import overview, read_metadata, require_admin


router = APIRouter(prefix='/api/admin', dependencies=[Depends(require_admin)])


def dashboard_metadata(admins, now):
    today = datetime.fromtimestamp(now, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    first = today - timedelta(days=6)
    days = [(first + timedelta(days=index)).strftime('%Y-%m-%d') for index in range(7)]
    activity = {day: {'date': day, 'users': 0, 'devices': 0, 'actions': 0} for day in days}
    with admins.identities._transaction() as db:
        for key, table in (('users', 'auth_users'), ('devices', 'devices'), ('actions', 'admin_audit')):
            for row in db.execute("SELECT strftime('%Y-%m-%d',created_at,'unixepoch') AS day,COUNT(*) AS n "
                                  'FROM ' + table + ' WHERE created_at>=? AND created_at<=? GROUP BY day',
                                  (first.timestamp(), now)):
                if row['day'] in activity:
                    activity[row['day']][key] = row['n']
        recent = [dict(row) for row in db.execute('''SELECT id,actor_id,actor_username,target_type,target_id,
            action,result,created_at FROM admin_audit ORDER BY created_at DESC,id DESC LIMIT 8''')]
        documents = dict(db.execute('SELECT status,COUNT(*) FROM knowledge_documents GROUP BY status'))
        workload = {
            'documents': documents,
            'active_sessions': db.execute('''SELECT COUNT(*) FROM auth_sessions s JOIN auth_users u ON u.id=s.user_id
                WHERE s.revoked_at IS NULL AND s.expires_at>? AND u.disabled=0''', (now,)).fetchone()[0],
            'unbound_devices': db.execute('SELECT COUNT(*) FROM devices WHERE owner_user_id IS NULL AND revoked_at IS NULL').fetchone()[0],
            'revoked_devices': db.execute('SELECT COUNT(*) FROM devices WHERE revoked_at IS NOT NULL').fetchone()[0],
            'incomplete_actions': db.execute("SELECT COUNT(*) FROM admin_audit WHERE result='started'").fetchone()[0],
        }
        stale_actions = db.execute("SELECT COUNT(*) FROM admin_audit WHERE result='started' AND created_at<=?",
                                   (now - 60,)).fetchone()[0]
    return {'activity': list(activity.values()), 'recent_audit': recent, 'workload': workload,
            'stale_actions': stale_actions}


def system_metadata(path):
    result = {'python_version': platform.python_version(), 'platform': platform.system(),
              'worker_mode': 'single', 'database_engine': 'SQLite',
              'disk_total_bytes': None, 'disk_free_bytes': None, 'disk_status': 'unknown'}
    try:
        disk = shutil.disk_usage(path.parent)
    except OSError:
        return result
    result.update(disk_total_bytes=disk.total, disk_free_bytes=disk.free,
                  disk_status='low' if disk.free < max(256 * 1024**2, disk.total * .05) else 'ok')
    return result


def attention_items(summary, metadata, system):
    items = []

    def add(code, level, count, message, view):
        items.append({'code': code, 'level': level, 'count': count, 'message': message, 'view': view})

    documents, work = metadata['workload']['documents'], metadata['workload']
    failed = documents.get('failed', 0)
    pending = sum(documents.get(key, 0) for key in ('pending', 'queued', 'processing'))
    if failed:
        add('documents_failed', 'warning', failed, f'{failed} 份资料处理失败，可由资料所属账号检查并重试', 'storage')
    if pending:
        add('documents_pending', 'info', pending, f'{pending} 份资料等待或正在处理', 'storage')
    if metadata['stale_actions']:
        count = metadata['stale_actions']
        add('actions_incomplete', 'warning', count, f'{count} 项管理操作超过一分钟仍未完整收尾，请核对审计', 'audit')
    backup = summary['backups']
    if not backup['enabled']:
        add('backups_disabled', 'warning', 1, '自动备份尚未启用，请在部署配置中设置备份目录', 'backups')
    elif backup['last_failure_at'] and (not backup['last_success_at'] or backup['last_failure_at'] > backup['last_success_at']):
        add('backups_failed', 'warning', 1, '最近一次备份未成功，请检查备份状态', 'backups')
    elif not backup['last_success_at']:
        add('backups_unverified', 'info', 1, '尚未记录成功备份，启用不代表已完成备份', 'backups')
    if system['disk_status'] == 'low':
        add('disk_low', 'warning', 1, '数据库所在磁盘可用空间偏低，请检查服务器存储', 'storage')
    elif system['disk_status'] == 'unknown':
        add('disk_unknown', 'info', 1, '暂时无法读取数据库所在磁盘用量', 'storage')
    if work['unbound_devices']:
        count = work['unbound_devices']
        add('devices_unbound', 'info', count, f'{count} 台已登记设备尚未绑定账号', 'devices')
    return items


@router.get('/dashboard')
async def dashboard(request: Request):
    admins = request.app.state.admin_store
    now = admins.identities.clock()
    summary = await overview(request)
    metadata = await read_metadata(dashboard_metadata, admins, now)
    system = await read_metadata(system_metadata, admins.path)
    return {'generated_at': now, 'summary': summary,
            'activity': metadata['activity'], 'recent_audit': metadata['recent_audit'],
            'workload': metadata['workload'], 'system': system,
            'attention': attention_items(summary, metadata, system)}
