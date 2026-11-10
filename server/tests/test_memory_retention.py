"""可逆压缩、未总结保护、账号隔离及独立备份恢复。"""

from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from identity_store import IdentityStore
from memory_store import MemoryStore
from memory_retention import MemoryRetention
from tools.memory_database import inspect_database,backup_database,restore_database


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'memory.sqlite3'
        self.store = MemoryStore(self.path)
        self.ids = IdentityStore(self.path)
        self.users = {}
        for name in ('alice','bob'):
            user = self.ids.create_user(name,name+'-test-password')
            self.users[name] = user.id
            device = self.ids.register_device('cat-'+name,name)
            self.ids.claim_pairing(self.ids.request_pairing(device.device_id,device.token).code,user.id,client_key='fixture')
        self.now = datetime.now(timezone.utc).timestamp()
        self.retention = MemoryRetention(self.path,clock=lambda:self.now)
        self.store.retention_enabled = True
        self.content = '这是仅属于当前账号的模拟聊天原文。' * 100

    def tearDown(self):
        self.temp.cleanup()

    def seed(self,name='alice',pending=False):
        user = self.users[name]
        conversation = self.store.create_conversation(user,'cat-'+name)
        last = self.store.append_message(conversation,'user',self.content)
        if not pending:
            self.store.save_summary(conversation,last,'保留的摘要',['保留的重要事实'])
        return conversation,last

    def test_only_idle_temporary_and_summarized_text_is_compressed_and_restorable(self):
        ca,last = self.seed()
        self.seed('bob')
        self.retention.set_mode(self.users['alice'],'cat-alice','temporary')
        self.now += 29*86400
        self.assertEqual(self.retention.sweep(),0)
        self.now += 2*86400
        self.assertEqual(self.retention.sweep(),1)
        policy = self.retention.preview(self.users['alice'],'cat-alice')
        self.assertEqual(policy['compressed_messages'],1)
        context = self.store.context_for(self.users['alice'],'cat-alice')
        self.assertEqual(context['summaries'][0]['summary'],'保留的摘要')
        self.assertEqual(context['memories'][0]['content'],'保留的重要事实')
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT content FROM messages WHERE id=?',(last,)).fetchone()[0],'')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM messages WHERE content<>\'\'').fetchone()[0],1)
        self.assertEqual(self.retention.restore(self.users['bob'],'cat-alice'),0)
        self.assertEqual(self.retention.restore(self.users['alice'],'cat-alice'),1)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT content FROM messages WHERE id=?',(last,)).fetchone()[0],self.content)
        self.assertEqual(self.retention.sweep(),0)

    def test_pending_messages_are_never_blank_and_new_activity_leaves_archived_status(self):
        ca,last = self.seed(pending=True)
        self.retention.set_mode(self.users['alice'],'cat-alice','temporary')
        self.now += 31*86400
        self.assertEqual(self.retention.sweep(),0)
        self.store.save_summary(ca,last,'可以归档')
        self.assertEqual(self.retention.sweep(),1)
        self.store.append_message(ca,'user','再次聊天的新消息')
        self.assertIsNone(self.retention.preview(self.users['alice'],'cat-alice')['archived_at'])
        self.assertEqual(self.store.pending_messages(ca)[0]['content'],'再次聊天的新消息')

    def test_archives_survive_independent_backup_and_restore_and_corruption_is_atomic(self):
        self.seed()
        self.retention.set_mode(self.users['alice'],'cat-alice','temporary')
        self.now += 31*86400
        self.retention.sweep()
        report = backup_database(self.path,Path(self.temp.name)/'backup.sqlite3')
        restored = Path(self.temp.name)/'restored.sqlite3'
        restore_database(Path(report['database']),restored,report['sha256'],apply=True,offline=True)
        self.assertEqual(MemoryRetention(restored).restore(self.users['alice'],'cat-alice'),1)
        self.assertTrue(inspect_database(restored)['ok'])
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE memory_message_archives SET sha256='invalid'")
            db.commit()
        with self.assertRaises(ValueError):
            self.retention.restore(self.users['alice'],'cat-alice')
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages WHERE content=''").fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM memory_message_archives').fetchone()[0],1)
        with self.assertRaises(ValueError):
            inspect_database(self.path)


if __name__=='__main__':
    unittest.main()
