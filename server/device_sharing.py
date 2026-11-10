"""设备使用权和历史归属。长期主人与当前使用者分开计算。"""

import hashlib
import re
import secrets
import uuid


def code_hash(code):
    return hashlib.sha256(re.sub(r"[\s-]", "", code.upper()).encode()).hexdigest()


class DeviceSharing:
    def history_devices(self, user_id):
        with self._transaction() as db:
            return [dict(row) for row in db.execute("""SELECT d.id,d.display_name,MAX(m.started_at) AS last_used_at
                FROM device_memberships m JOIN devices d ON d.id=m.device_id WHERE m.user_id=?
                GROUP BY d.id ORDER BY last_used_at DESC""", (user_id,))]

    def historical_device(self, device_id, user_id):
        with self._transaction() as db:
            return bool(db.execute("SELECT 1 FROM device_memberships WHERE device_id=? AND user_id=? LIMIT 1", (device_id,user_id)).fetchone())

    def decorate_device(self, db, row, viewer=None):
        result = dict(row)
        loan = db.execute("""SELECT * FROM device_loans WHERE device_id=? AND ended_at IS NULL AND expires_at>?
            ORDER BY created_at DESC LIMIT 1""", (result['id'], self.clock())).fetchone()
        guest = loan['guest_user_id'] if loan else None
        result.update(active_user_id=guest or result['owner_user_id'], loan_active=guest is not None,
                      loan_pending=bool(loan and not guest), loan_expires_at=loan['expires_at'] if loan else None)
        if viewer is not None:
            result.update(role='owner' if viewer == result['owner_user_id'] else 'guest',
                          control_allowed=result['active_user_id'] == viewer)
        return result

    def accessible_device(self, device_id, user_id):
        with self._transaction() as db:
            row = db.execute("SELECT * FROM devices WHERE id=? AND revoked_at IS NULL", (device_id,)).fetchone()
            if row is None:
                return None
            result = self.decorate_device(db, row, user_id)
            return result if result['active_user_id'] == user_id else None

    def list_accessible_devices(self, user_id):
        with self._transaction() as db:
            rows = db.execute("""SELECT DISTINCT d.* FROM devices d LEFT JOIN device_loans l ON d.id=l.device_id
                WHERE d.owner_user_id=? OR (l.guest_user_id=? AND l.ended_at IS NULL AND l.expires_at>?)
                ORDER BY d.created_at,d.id""", (user_id,user_id,self.clock())).fetchall()
            return [self.decorate_device(db,row,user_id) for row in rows]

    def _end_loans(self, db, device_id):
        now = self.clock()
        db.execute("UPDATE device_loans SET ended_at=? WHERE device_id=? AND ended_at IS NULL", (now,device_id))
        db.execute("UPDATE device_memberships SET ended_at=? WHERE device_id=? AND kind='guest' AND ended_at IS NULL", (now,device_id))

    def unbind_device(self, device_id, user_id):
        now = self.clock()
        with self._transaction(write=True) as db:
            row = db.execute("SELECT * FROM devices WHERE id=? AND owner_user_id=? AND revoked_at IS NULL", (device_id,user_id)).fetchone()
            if row is None:
                raise ValueError('设备不存在或无权解绑')
            self._end_loans(db,device_id)
            db.execute("UPDATE devices SET owner_user_id=NULL,bound_at=NULL,access_epoch=access_epoch+1 WHERE id=?", (device_id,))
            db.execute("UPDATE device_memberships SET ended_at=? WHERE device_id=? AND ended_at IS NULL", (now,device_id))
            db.execute("UPDATE device_pairings SET invalidated_at=? WHERE device_id=? AND claimed_at IS NULL", (now,device_id))
            db.execute("DELETE FROM auth_rate_limits WHERE bucket=?", ('pair-request:'+device_id,))

    def create_loan(self, device_id, user_id, hours):
        if hours not in (1,4,24,168):
            raise ValueError('请选择 1 小时、4 小时、1 天或 7 天')
        now = self.clock()
        raw = ''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(8))
        with self._transaction(write=True) as db:
            row = db.execute("SELECT * FROM devices WHERE id=? AND owner_user_id=? AND revoked_at IS NULL", (device_id,user_id)).fetchone()
            if row is None:
                raise ValueError('设备不存在或无权出借')
            current = self.decorate_device(db,row)
            if current['loan_active']:
                raise ValueError('设备正在借用中，请先结束借用')
            self._end_loans(db,device_id)
            db.execute("""INSERT INTO device_loans(id,device_id,owner_user_id,invitation_hash,created_at,claim_until,expires_at)
                VALUES(?,?,?,?,?,?,?)""", (uuid.uuid4().hex,device_id,user_id,code_hash(raw),now,now+300,now+hours*3600))
        return {'code':raw[:4]+'-'+raw[4:], 'claim_expires_at':now+300, 'expires_at':now+hours*3600}

    def claim_loan(self, code, user_id, *, client_key):
        self.consume_rate_limit('loan-claim-user:'+user_id, limit=10, window=300)
        self.consume_rate_limit('loan-claim-ip:'+code_hash(client_key), limit=30, window=300)
        now = self.clock()
        with self._transaction(write=True) as db:
            loan = db.execute("""SELECT l.* FROM device_loans l JOIN devices d ON d.id=l.device_id
                JOIN auth_users u ON u.id=? AND u.disabled=0
                WHERE l.invitation_hash=? AND l.claim_until>? AND l.expires_at>? AND l.ended_at IS NULL
                AND l.guest_user_id IS NULL AND d.owner_user_id=l.owner_user_id AND d.revoked_at IS NULL""",
                (user_id,code_hash(code),now,now)).fetchone()
            if loan is None or loan['owner_user_id'] == user_id:
                raise ValueError('借用码无效、已过期，或当前账号就是主人')
            db.execute("UPDATE device_loans SET guest_user_id=? WHERE id=?", (user_id,loan['id']))
            db.execute("UPDATE devices SET access_epoch=access_epoch+1 WHERE id=?", (loan['device_id'],))
            db.execute("INSERT INTO device_memberships VALUES(?,?,?,?,?,NULL)", (uuid.uuid4().hex,loan['device_id'],user_id,'guest',now))
            row = db.execute("SELECT * FROM devices WHERE id=?", (loan['device_id'],)).fetchone()
            return self.decorate_device(db,row,user_id)

    def loan_target(self,code):
        with self._transaction() as db:
            row = db.execute('SELECT device_id FROM device_loans WHERE invitation_hash=?',(code_hash(code),)).fetchone()
            return row[0] if row else None

    def end_loan(self, device_id, user_id):
        with self._transaction(write=True) as db:
            loan = db.execute("""SELECT l.* FROM device_loans l JOIN devices d ON d.id=l.device_id
                WHERE l.device_id=? AND l.ended_at IS NULL AND d.revoked_at IS NULL
                AND (l.owner_user_id=? OR l.guest_user_id=?) ORDER BY l.created_at DESC LIMIT 1""", (device_id,user_id,user_id)).fetchone()
            if loan is None:
                raise ValueError('没有可结束的借用，或无权操作')
            self._end_loans(db,device_id)
            db.execute("UPDATE devices SET access_epoch=access_epoch+1 WHERE id=?", (device_id,))

    def expire_loans(self):
        with self._transaction(write=True) as db:
            rows = db.execute("SELECT DISTINCT device_id FROM device_loans WHERE ended_at IS NULL AND (expires_at<=? OR (guest_user_id IS NULL AND claim_until<=?))", (self.clock(),self.clock())).fetchall()
            for row in rows:
                self._end_loans(db,row[0])
                db.execute("UPDATE devices SET access_epoch=access_epoch+1 WHERE id=?", (row[0],))
            return [row[0] for row in rows]

    def voice_preference(self, user_id, device_id):
        with self._transaction() as db:
            row = db.execute("SELECT voice_paused FROM device_preferences WHERE user_id=? AND device_id=?", (user_id,device_id)).fetchone()
            return bool(row and row[0])

    def save_voice_preference(self, user_id, device_id, paused):
        with self._transaction(write=True) as db:
            db.execute("""INSERT INTO device_preferences(user_id,device_id,voice_paused) VALUES(?,?,?)
                ON CONFLICT(user_id,device_id) DO UPDATE SET voice_paused=excluded.voice_paused""", (user_id,device_id,int(paused)))
