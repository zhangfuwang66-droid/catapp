"""可逆的临时记忆归档：保留摘要与事实，只压缩已总结原文，不自动硬删除。"""

import asyncio
from contextlib import contextmanager
from datetime import datetime
import gzip
import hashlib
import json
import sqlite3
import time
import zlib

from async_helpers import run_blocking
from database_schema import validate_versions
from runtime_logging import log_event

MAX_ARCHIVE_BYTES = 8 * 1024**2


def unpack_archive(row):
    if not 0 < row['raw_bytes'] <= MAX_ARCHIVE_BYTES:
        raise ValueError('记忆归档大小无效')
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    raw = decoder.decompress(row['payload'], MAX_ARCHIVE_BYTES + 1)
    if (len(raw) != row['raw_bytes'] or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
            or hashlib.sha256(raw).hexdigest() != row['sha256']):
        raise ValueError('记忆归档校验失败，原数据库未被覆盖')
    items = json.loads(raw)
    if not isinstance(items,list) or len(items) != row['message_count']:
        raise ValueError('记忆归档消息计数无效')
    if any(not isinstance(item,dict) or type(item.get('id')) is not int or not isinstance(item.get('content'),str) for item in items):
        raise ValueError('记忆归档内容无效')
    if len({item['id'] for item in items}) != len(items):
        raise ValueError('记忆归档消息编号重复')
    return items


class MemoryRetention:
    def __init__(self, path, *, clock=time.time):
        self.path = path
        self.clock = clock
        self.task = None
        with self.connection(write=True) as db:
            if not validate_versions(db).get('retention'):
                db.execute("""CREATE TABLE memory_policies (
                    user_id TEXT NOT NULL REFERENCES auth_users(id), device_id TEXT NOT NULL REFERENCES devices(id),
                    mode TEXT NOT NULL CHECK(mode IN ('long_term','temporary')), updated_at REAL NOT NULL,
                    archived_at REAL, PRIMARY KEY(user_id,device_id))""")
                db.execute("""CREATE TABLE memory_message_archives (
                    id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    payload BLOB NOT NULL, raw_bytes INTEGER NOT NULL, message_count INTEGER NOT NULL,
                    sha256 TEXT NOT NULL, created_at REAL NOT NULL)""")
                db.execute('CREATE INDEX idx_memory_archive_conversation ON memory_message_archives(conversation_id)')
                db.execute("INSERT INTO schema_migrations VALUES('retention',1)")

    @contextmanager
    def connection(self, *, write=False):
        db = sqlite3.connect(self.path,timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
                yield db
        finally:
            db.close()

    def ensure_policy(self,user_id,device_id,mode='long_term'):
        with self.connection(write=True) as db:
            db.execute('INSERT OR IGNORE INTO memory_policies VALUES(?,?,?,?,NULL)',(user_id,device_id,mode,self.clock()))

    def set_mode(self,user_id,device_id,mode):
        if mode not in ('long_term','temporary'):
            raise ValueError('记忆模式无效')
        with self.connection(write=True) as db:
            db.execute("""INSERT INTO memory_policies VALUES(?,?,?,?,NULL) ON CONFLICT(user_id,device_id)
                DO UPDATE SET mode=excluded.mode,updated_at=excluded.updated_at,archived_at=NULL""",(user_id,device_id,mode,self.clock()))
        return self.preview(user_id,device_id)

    def _preview(self,db,user_id,device_id):
        policy = db.execute('SELECT * FROM memory_policies WHERE user_id=? AND device_id=?',(user_id,device_id)).fetchone()
        now = self.clock()
        mode = policy['mode'] if policy else 'long_term'
        latest = db.execute('SELECT MAX(last_activity_at) FROM conversations WHERE user_id=? AND device_id=?',(user_id,device_id)).fetchone()[0]
        activity = datetime.fromisoformat(latest).timestamp() if latest else 0
        since = max(activity, policy['updated_at'] if policy else now)
        pending = db.execute("""SELECT COUNT(*) FROM messages m JOIN conversations c ON c.id=m.conversation_id
            WHERE c.user_id=? AND c.device_id=? AND m.id>c.summarized_through_message_id""",(user_id,device_id)).fetchone()[0]
        compressed = db.execute("""SELECT COALESCE(SUM(a.message_count),0),COALESCE(SUM(length(a.payload)),0)
            FROM memory_message_archives a JOIN conversations c ON c.id=a.conversation_id WHERE c.user_id=? AND c.device_id=?""",(user_id,device_id)).fetchone()
        return {'mode':mode,'idle_days':max(0,int((now-since)//86400)), 'archive_after_days':30 if mode=='temporary' else None,
                'archived_at':policy['archived_at'] if policy else None, 'pending_messages':pending,
                'compressed_messages':compressed[0], 'compressed_bytes':compressed[1],
                'eligible':mode=='temporary' and now-since>=30*86400 and not pending,
                'automatic_deletion':False}

    def preview(self,user_id,device_id):
        with self.connection() as db:
            return self._preview(db,user_id,device_id)

    def archive(self,user_id,device_id):
        with self.connection(write=True) as db:
            if not self._preview(db,user_id,device_id)['eligible']:
                return 0
            count = 0
            conversations = db.execute('SELECT id,summarized_through_message_id FROM conversations WHERE user_id=? AND device_id=?',(user_id,device_id)).fetchall()
            for conversation in conversations:
                cursor = db.execute("SELECT id,content FROM messages WHERE conversation_id=? AND id<=? AND content<>'' ORDER BY id",tuple(conversation))
                while rows := cursor.fetchmany(200):
                    raw = json.dumps([dict(row) for row in rows],ensure_ascii=False,separators=(',',':')).encode()
                    if len(raw)>MAX_ARCHIVE_BYTES:
                        continue
                    payload = gzip.compress(raw,mtime=0)
                    # 小片段压缩后反而变大时，仍保留原文。
                    if len(payload) + 256 >= sum(len(row['content'].encode()) for row in rows):
                        continue
                    db.execute('INSERT INTO memory_message_archives(conversation_id,payload,raw_bytes,message_count,sha256,created_at) VALUES(?,?,?,?,?,?)',
                               (conversation[0],payload,len(raw),len(rows),hashlib.sha256(raw).hexdigest(),self.clock()))
                    db.executemany("UPDATE messages SET content='' WHERE id=? AND conversation_id=?",((row['id'],conversation[0]) for row in rows))
                    count += len(rows)
            db.execute('UPDATE memory_policies SET archived_at=? WHERE user_id=? AND device_id=?',(self.clock(),user_id,device_id))
            return count

    def restore(self,user_id,device_id):
        count = 0
        with self.connection(write=True) as db:
            rows = db.execute("""SELECT a.* FROM memory_message_archives a JOIN conversations c ON c.id=a.conversation_id
                WHERE c.user_id=? AND c.device_id=? ORDER BY a.id""",(user_id,device_id)).fetchall()
            for row in rows:
                for item in unpack_archive(row):
                    changed = db.execute("UPDATE messages SET content=? WHERE id=? AND conversation_id=? AND content=''",(item['content'],item['id'],row['conversation_id'])).rowcount
                    if changed != 1:
                        raise ValueError('归档与消息不一致，已取消恢复')
                    count += 1
                db.execute('DELETE FROM memory_message_archives WHERE id=?',(row['id'],))
            db.execute('UPDATE memory_policies SET archived_at=NULL,updated_at=? WHERE user_id=? AND device_id=?',(self.clock(),user_id,device_id))
        return count

    def reset(self,user_id,device_id):
        with self.connection(write=True) as db:
            db.execute('UPDATE memory_policies SET archived_at=NULL,updated_at=? WHERE user_id=? AND device_id=?',(self.clock(),user_id,device_id))

    def sweep(self):
        with self.connection() as db:
            owners = db.execute("SELECT user_id,device_id FROM memory_policies WHERE mode='temporary' AND archived_at IS NULL").fetchall()
        return sum(self.archive(*owner) for owner in owners)

    async def _loop(self):
        while True:
            try:
                await run_blocking(self.sweep)
            except Exception as exc:
                log_event(f'[MEMORY] retention will retry: {type(exc).__name__}')
            await asyncio.sleep(3600)

    async def start(self):
        self.task = asyncio.create_task(self._loop(),name='memory-retention')

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)
            self.task = None
