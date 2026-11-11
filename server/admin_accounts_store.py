"""管理员账号业务；不迁移表，不读取凭证或私人正文。"""

import sqlite3


class AccountMissing(LookupError):
    pass


class AccountConflict(ValueError):
    pass


BLOCKER_LABELS = {
    'current_account': '当前管理员账号', 'enabled': '账号仍启用',
    'last_admin': '最后一名启用管理员', 'device_ownership': '设备归属',
    'device_history': '设备历史关联', 'device_loans': '设备借用记录',
    'device_preferences': '设备偏好', 'documents': '私人资料',
    'memory': '对话或记忆', 'memory_policies': '记忆保留设置',
    'key_configured': '已配置的模型凭证', 'speech_settings': '语音设置',
}


class AdminAccountsStore:
    def __init__(self, admin_store):
        self.admin = admin_store
        self.identities = admin_store.identities

    @staticmethod
    def _user(db, user_id):
        row = db.execute("""SELECT u.id,u.username,u.created_at,u.disabled,
            EXISTS(SELECT 1 FROM admin_roles a WHERE a.user_id=u.id) AS is_admin,
            EXISTS(SELECT 1 FROM account_provider_keys k WHERE k.user_id=u.id) AS key_configured
            FROM auth_users u WHERE u.id=?""", (user_id,)).fetchone()
        if not row:
            raise AccountMissing()
        result = dict(row)
        for key in ('disabled', 'is_admin', 'key_configured'):
            result[key] = bool(result[key])
        return result

    def _blockers(self, db, user, actor_id):
        user_id = user['id']
        blockers = []
        if user_id == actor_id:
            blockers.append('current_account')
        if not user['disabled']:
            blockers.append('enabled')
        if self.admin._is_admin(db, user_id) and self.admin._admin_count(db) <= 1:
            blockers.append('last_admin')
        checks = (
            ('device_ownership', 'SELECT 1 FROM devices WHERE owner_user_id=?'),
            ('device_history', 'SELECT 1 FROM device_memberships WHERE user_id=? UNION ALL SELECT 1 FROM device_pairings WHERE claimed_by=?'),
            ('device_loans', 'SELECT 1 FROM device_loans WHERE owner_user_id=? OR guest_user_id=?'),
            ('device_preferences', 'SELECT 1 FROM device_preferences WHERE user_id=?'),
            ('documents', 'SELECT 1 FROM knowledge_documents WHERE user_id=? UNION ALL SELECT 1 FROM knowledge_chunks WHERE user_id=?'),
            ('memory', 'SELECT 1 FROM conversations WHERE user_id=? UNION ALL SELECT 1 FROM memories WHERE user_id=?'),
            ('memory_policies', 'SELECT 1 FROM memory_policies WHERE user_id=?'),
            ('key_configured', 'SELECT 1 FROM account_provider_keys WHERE user_id=?'),
            ('speech_settings', 'SELECT 1 FROM account_speech_settings WHERE user_id=?'),
        )
        for key, query in checks:
            if db.execute(query + ' LIMIT 1', (user_id,) * query.count('?')).fetchone():
                blockers.append(key)
        return blockers

    def detail(self, actor_id, session_token, user_id):
        with self.identities._transaction() as db:
            self.admin.require_writer(db, actor_id, session_token)
            user = self._user(db, user_id)
            active_sessions = db.execute("""SELECT COUNT(*) FROM auth_sessions
                WHERE user_id=? AND revoked_at IS NULL AND expires_at>?""",
                (user_id, self.identities.clock())).fetchone()[0] if not user['disabled'] else 0
            devices = {'owned': [], 'borrowed': [], 'history_count': db.execute(
                'SELECT COUNT(DISTINCT device_id) FROM device_memberships WHERE user_id=?',
                (user_id,)).fetchone()[0]}
            rows = db.execute("""WITH current_loans AS (
                SELECT device_id,owner_user_id,guest_user_id,expires_at,
                    ROW_NUMBER() OVER (PARTITION BY device_id ORDER BY created_at DESC,id DESC) AS position
                FROM device_loans WHERE ended_at IS NULL AND expires_at>?)
                SELECT d.id,d.display_name,d.owner_user_id,
                    COALESCE(l.guest_user_id,d.owner_user_id) AS active_user_id,
                    d.revoked_at IS NOT NULL AS revoked,
                    l.guest_user_id IS NOT NULL AS loan_active,
                    (l.device_id IS NOT NULL AND l.guest_user_id IS NULL) AS loan_pending,
                    l.expires_at AS loan_expires_at
                FROM devices d LEFT JOIN current_loans l ON l.device_id=d.id AND l.position=1
                    AND l.owner_user_id=d.owner_user_id AND d.revoked_at IS NULL
                WHERE d.owner_user_id=? OR l.guest_user_id=? ORDER BY d.created_at,d.id""",
                (self.identities.clock(), user_id, user_id))
            for row in rows:
                device = dict(row)
                for key in ('revoked', 'loan_active', 'loan_pending'):
                    device[key] = bool(device[key])
                prefix, separator, _ = device['id'].partition('-')
                device['device_type'] = prefix if separator and prefix in ('cat', 'deer') else 'unknown'
                devices['owned' if device['owner_user_id'] == user_id else 'borrowed'].append(device)
            count, size = db.execute('SELECT COUNT(*),COALESCE(SUM(size_bytes),0) FROM knowledge_documents WHERE user_id=?',
                                     (user_id,)).fetchone()
            documents = {'count': count, 'bytes': size, 'statuses': dict(db.execute(
                'SELECT status,COUNT(*) FROM knowledge_documents WHERE user_id=? GROUP BY status', (user_id,))),
                'chunks': db.execute('SELECT COUNT(*) FROM knowledge_chunks WHERE user_id=?', (user_id,)).fetchone()[0]}
            memory = {}
            for key, table in (('conversations', 'conversations'), ('memories', 'memories'), ('policies', 'memory_policies')):
                memory[key] = db.execute('SELECT COUNT(*) FROM ' + table + ' WHERE user_id=?', (user_id,)).fetchone()[0]
            for key, table in (('messages', 'messages'), ('summaries', 'conversation_summaries'), ('archives', 'memory_message_archives')):
                memory[key] = db.execute('SELECT COUNT(*) FROM ' + table + ' m JOIN conversations c ON c.id=m.conversation_id WHERE c.user_id=?',
                                         (user_id,)).fetchone()[0]
            sizes = db.execute("""SELECT
                (SELECT COALESCE(SUM(length(CAST(m.content AS BLOB))),0) FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE c.user_id=?)
                + (SELECT COALESCE(SUM(length(CAST(s.summary AS BLOB))),0) FROM conversation_summaries s JOIN conversations c ON c.id=s.conversation_id WHERE c.user_id=?)
                + (SELECT COALESCE(SUM(length(CAST(content AS BLOB))),0) FROM memories WHERE user_id=?) AS content_bytes,
                (SELECT COALESCE(SUM(length(a.payload)),0) FROM memory_message_archives a JOIN conversations c ON c.id=a.conversation_id WHERE c.user_id=?) AS archive_bytes,
                (SELECT COALESCE(SUM(a.message_count),0) FROM memory_message_archives a JOIN conversations c ON c.id=a.conversation_id WHERE c.user_id=?) AS archived_messages""", (user_id,) * 5).fetchone()
            memory.update(dict(sizes))
            blockers = self._blockers(db, user, actor_id)
            return {'user': user, 'active_sessions': active_sessions, 'devices': devices,
                    'documents': documents, 'memory': memory,
                    'deletion': {'allowed': not blockers, 'blockers': blockers}}

    def create(self, actor_id, session_token, user_id, username, password):
        username = self.identities.normalize_username(username)
        self.identities.validate_password(password)
        password_hash = self.identities.hasher.hash(password)
        with self.identities._transaction(write=True) as db:
            self.admin.require_writer(db, actor_id, session_token)
            try:
                db.execute('INSERT INTO auth_users(id,username,password_hash,created_at) VALUES(?,?,?,?)',
                           (user_id, username, password_hash, self.identities.clock()))
            except sqlite3.IntegrityError:
                if db.execute('SELECT 1 FROM auth_users WHERE username=?', (username,)).fetchone():
                    raise AccountConflict('账号已存在，请选择其他账号名') from None
                raise
            return self._user(db, user_id)

    def rename(self, actor_id, session_token, user_id, username, confirm_username):
        username = self.identities.normalize_username(username)
        with self.identities._transaction(write=True) as db:
            self.admin.require_writer(db, actor_id, session_token)
            target = self._user(db, user_id)
            if user_id == actor_id:
                raise AccountConflict('不能修改当前管理员自己的账号名')
            if target['username'] != confirm_username:
                raise AccountConflict('目标账号名已变化或确认不符，请刷新后准确输入当前账号名')
            try:
                db.execute('UPDATE auth_users SET username=? WHERE id=?', (username, user_id))
            except sqlite3.IntegrityError:
                raise AccountConflict('账号已存在，请选择其他账号名') from None
            db.execute('UPDATE auth_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL',
                       (self.identities.clock(), user_id))
            return self._user(db, user_id)

    def _check_delete(self, db, user_id, actor_id, confirm_username):
        target = self._user(db, user_id)
        if target['username'] != confirm_username:
            raise AccountConflict('目标账号名已变化或确认不符，请刷新后准确输入当前账号名')
        blockers = self._blockers(db, target, actor_id)
        if blockers:
            labels = '、'.join(BLOCKER_LABELS[key] for key in blockers)
            raise AccountConflict('不能删除账号：' + labels + '；请先处理后再删除，现有数据已保留')

    def check_delete(self, actor_id, session_token, user_id, confirm_username):
        with self.identities._transaction() as db:
            self.admin.require_writer(db, actor_id, session_token)
            self._check_delete(db, user_id, actor_id, confirm_username)

    def delete(self, actor_id, session_token, user_id, confirm_username):
        with self.identities._transaction(write=True) as db:
            self.admin.require_writer(db, actor_id, session_token)
            self._check_delete(db, user_id, actor_id, confirm_username)
            try:
                db.execute('DELETE FROM auth_sessions WHERE user_id=?', (user_id,))
                db.execute('DELETE FROM admin_roles WHERE user_id=?', (user_id,))
                db.execute('DELETE FROM auth_users WHERE id=?', (user_id,))
            except sqlite3.IntegrityError:
                raise AccountConflict('账号仍有关联数据，请先处理后再删除，现有数据已保留') from None
