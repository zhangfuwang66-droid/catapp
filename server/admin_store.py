"""管理员显式授权与白名单元数据；与原身份迁移独立，可保留新增表回退代码。"""

import time
from database_schema import validate_admin_schema


class AdminStore:
    def __init__(self, identities):
        self.identities = identities
        self.path = identities.path
        with identities._transaction(write=True) as db:
            validate_admin_schema(db)
            db.execute("CREATE TABLE IF NOT EXISTS admin_schema (version INTEGER PRIMARY KEY CHECK(version=1))")
            db.execute("INSERT OR IGNORE INTO admin_schema VALUES(1)")
            db.execute("""CREATE TABLE IF NOT EXISTS admin_roles (
                user_id TEXT PRIMARY KEY REFERENCES auth_users(id), granted_at REAL NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS admin_audit (
                id INTEGER PRIMARY KEY, actor_id TEXT NOT NULL, actor_username TEXT NOT NULL,
                target_type TEXT NOT NULL, target_id TEXT NOT NULL, action TEXT NOT NULL,
                result TEXT NOT NULL CHECK(result IN ('started','success','failed')), created_at REAL NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_admin_audit_time ON admin_audit(created_at,id)")
            validate_admin_schema(db)

    @staticmethod
    def _is_admin(db, user_id):
        return bool(db.execute("""SELECT 1 FROM admin_roles a JOIN auth_users u ON u.id=a.user_id
            WHERE a.user_id=? AND u.disabled=0""", (user_id,)).fetchone())

    def is_admin(self, user_id):
        with self.identities._transaction() as db:
            return self._is_admin(db, user_id)

    @staticmethod
    def _admin_count(db):
        return db.execute("SELECT COUNT(*) FROM admin_roles a JOIN auth_users u ON u.id=a.user_id WHERE u.disabled=0").fetchone()[0]

    def set_role(self, username, granted):
        username = self.identities.normalize_username(username)
        with self.identities._transaction(write=True) as db:
            user = db.execute("SELECT id,disabled FROM auth_users WHERE username=?", (username,)).fetchone()
            if user is None:
                raise ValueError("账号不存在")
            if granted:
                if user['disabled']:
                    raise ValueError("停用账号不能授权管理员")
                db.execute("INSERT OR IGNORE INTO admin_roles VALUES(?,?)", (user['id'], time.time()))
            else:
                if self._is_admin(db, user['id']) and self._admin_count(db) <= 1:
                    raise ValueError("不能撤销最后一名启用管理员")
                db.execute("DELETE FROM admin_roles WHERE user_id=?", (user['id'],))
            db.execute("""INSERT INTO admin_audit(actor_id,actor_username,target_type,target_id,action,result,created_at)
                VALUES('server-cli','服务端命令行','user',?,?, 'success',?)""",
                (user['id'], 'grant-admin' if granted else 'revoke-admin', time.time()))

    def roles(self):
        with self.identities._transaction() as db:
            return [dict(r) for r in db.execute("""SELECT u.id,u.username,u.disabled,a.granted_at
                FROM admin_roles a JOIN auth_users u ON u.id=a.user_id ORDER BY u.username""")]

    def begin_action(self, actor, target_type, target_id, action):
        with self.identities._transaction(write=True) as db:
            if not self._is_admin(db, actor.id):
                raise PermissionError("管理员权限已撤销")
            return db.execute("""INSERT INTO admin_audit(actor_id,actor_username,target_type,target_id,action,result,created_at)
                VALUES(?,?,?,?,?,'started',?)""", (actor.id,actor.username,target_type,target_id,action,time.time())).lastrowid

    def finish_action(self, audit_id, success):
        with self.identities._transaction(write=True) as db:
            db.execute("UPDATE admin_audit SET result=? WHERE id=?", ('success' if success else 'failed',audit_id))

    def user(self, user_id):
        with self.identities._transaction() as db:
            row = db.execute("SELECT id,username,disabled FROM auth_users WHERE id=?", (user_id,)).fetchone()
            return dict(row) if row else None

    def change_user(self, actor_id, user_id, disabled=None):
        """同步撤销会话；禁用用户即使持有硬件连接也不能继续获得授权。"""
        with self.identities._transaction(write=True) as db:
            if not self._is_admin(db, actor_id):
                raise PermissionError("管理员权限已撤销")
            if actor_id == user_id:
                raise ValueError("不能对当前管理员执行此操作")
            if not db.execute("SELECT 1 FROM auth_users WHERE id=?", (user_id,)).fetchone():
                raise ValueError("账号不存在")
            if disabled and self._is_admin(db,user_id) and self._admin_count(db) <= 1:
                raise ValueError("不能停用最后一名启用管理员")
            if disabled is not None:
                db.execute("UPDATE auth_users SET disabled=? WHERE id=?", (int(disabled),user_id))
            if disabled is not False:
                db.execute("UPDATE auth_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (time.time(),user_id))

    @staticmethod
    def _page(db, query, args, page, page_size):
        total = db.execute('SELECT COUNT(*) FROM (' + query + ')', args).fetchone()[0]
        rows = db.execute(query + ' LIMIT ? OFFSET ?', (*args,page_size,(page-1)*page_size)).fetchall()
        return {'items':[dict(r) for r in rows], 'total':total,'page':page,'page_size':page_size}

    def users(self, q, status, page, page_size):
        query = """SELECT u.id,u.username,u.created_at,u.disabled,
            EXISTS(SELECT 1 FROM admin_roles a WHERE a.user_id=u.id) AS is_admin,
            (SELECT COUNT(*) FROM devices d WHERE d.owner_user_id=u.id) AS device_count,
            EXISTS(SELECT 1 FROM account_provider_keys k WHERE k.user_id=u.id) AS key_configured
            FROM auth_users u WHERE instr(u.username,?)>0"""
        if status != 'all':
            query += ' AND u.disabled=' + ('1' if status == 'disabled' else '0')
        with self.identities._transaction() as db:
            result = self._page(db,query+' ORDER BY u.created_at DESC,u.id',(q.casefold(),),page,page_size)
        for row in result['items']:
            for field in ('disabled','is_admin','key_configured'):
                row[field] = bool(row[field])
        return result

    def devices(self, q=''):
        with self.identities._transaction() as db:
            rows = db.execute("""SELECT d.id,d.display_name,d.owner_user_id,d.created_at,d.revoked_at,
                u.username AS owner_username FROM devices d LEFT JOIN auth_users u ON u.id=d.owner_user_id
                WHERE instr(lower(d.id),?)>0 OR instr(lower(d.display_name),?)>0 OR instr(u.username,?)>0
                ORDER BY d.created_at DESC,d.id""", (q.casefold(),)*3).fetchall()
            result = []
            for row in rows:
                device = self.identities.decorate_device(db,row)
                active = db.execute('SELECT username,disabled FROM auth_users WHERE id=?',(device['active_user_id'],)).fetchone()
                device['active_username'] = active['username'] if active else None
                device['active_user_enabled'] = bool(active and not active['disabled'])
                pref = db.execute('SELECT voice_paused FROM device_preferences WHERE user_id=? AND device_id=?',
                                  (device['active_user_id'], device['id'])).fetchone()
                device['voice_paused'] = bool(pref and pref[0]) if device['active_user_id'] else None
                device['revoked'] = device.pop('revoked_at') is not None
                device['device_type'] = 'cat' if device['id'].startswith('cat-') else 'unknown'
                result.append(device)
            return result

    def storage(self):
        with self.identities._transaction() as db:
            count = lambda table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
            size = lambda table,column: db.execute('SELECT COALESCE(SUM(length(CAST('+column+' AS BLOB))),0) FROM '+table).fetchone()[0]
            memory_bytes = sum(size(t,c) for t,c in (('messages','content'),('conversation_summaries','summary'),('memories','content')))
            documents = {'count':count('knowledge_documents'), 'bytes':db.execute('SELECT COALESCE(SUM(size_bytes),0) FROM knowledge_documents').fetchone()[0],
                         'statuses':dict(db.execute('SELECT status,COUNT(*) FROM knowledge_documents GROUP BY status')), 'chunks':count('knowledge_chunks')}
            memory = {key:count(table) for key,table in (('conversations','conversations'),('messages','messages'),('summaries','conversation_summaries'),('memories','memories'),('archives','memory_message_archives'))}
            memory.update(content_bytes=memory_bytes,archive_bytes=size('memory_message_archives','payload'),
                          archived_messages=db.execute('SELECT COALESCE(SUM(message_count),0) FROM memory_message_archives').fetchone()[0],
                          policies=dict(db.execute('SELECT mode,COUNT(*) FROM memory_policies GROUP BY mode')))
        return {'documents':documents,'memory':memory,'retention':{'temporary_archive_after_days':30,'automatic_deletion':False,'default_mode':'long_term'}}

    def counts(self):
        with self.identities._transaction() as db:
            total, disabled = db.execute('SELECT COUNT(*),COALESCE(SUM(disabled),0) FROM auth_users').fetchone()
            devices, bound = db.execute('SELECT COUNT(*),COUNT(owner_user_id) FROM devices').fetchone()
            return {'users':{'total':total,'enabled':total-disabled,'disabled':disabled,'admins':self._admin_count(db)},
                    'devices':{'total':devices,'bound':bound}}

    def audit(self, q, page, page_size):
        with self.identities._transaction() as db:
            return self._page(db,"""SELECT id,actor_id,actor_username,target_type,target_id,action,result,created_at FROM admin_audit
                WHERE instr(lower(actor_username),?)>0 OR instr(lower(target_id),?)>0 OR instr(lower(action),?)>0
                ORDER BY created_at DESC,id DESC""", (q.casefold(),)*3,page,page_size)
