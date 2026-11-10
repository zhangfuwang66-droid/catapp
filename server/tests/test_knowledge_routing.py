"""验证闲聊零向量调用、候选文档隔离、追问和旧元数据迁移。"""

import asyncio
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from knowledge_routing import route_documents
from knowledge_service import KnowledgeService
from knowledge_store import KnowledgeStore, split_text
from memory_store import MemoryStore
from test_knowledge import FakeEmbeddings
from tools.memory_database import inspect_database


class CountingEmbeddings(FakeEmbeddings):
    def __init__(self):
        self.calls = []

    async def embed(self, texts, *, timeout=8):
        self.calls.append(texts)
        return await super().embed(texts, timeout=timeout)


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'memory.sqlite3'
        MemoryStore(self.path)
        self.store = KnowledgeStore(self.path)
        self.embedder = CountingEmbeddings()
        self.service = KnowledgeService(self.store, embedder=self.embedder)
        self.leave = self.seed('alice', 'OA休假.txt', '申请年假需提前三天，在OA中选择请假审批。', ['请假','OA'], ['休假'])
        self.expense = self.seed('alice', '费用.txt', '报销需提交发票。', ['报销'], ['差旅'])
        self.secret = self.seed('bob', '私密.txt', '请假审批代号不应跨账号出现。', ['请假'], [])

    async def asyncTearDown(self):
        await self.service.close()
        self.temp.cleanup()

    def seed(self, user, filename, text, keywords, aliases):
        doc, _ = self.store.add_document(user, filename, text.encode())
        self.store.finish(self.store.claim_next(), split_text(text), [[1.,0.,0.]], model=self.embedder.model,
                          profile={'summary':text, 'keywords':keywords, 'aliases':aliases})
        return doc['id']

    async def test_smalltalk_skips_embedding_and_matching_restricts_sql_candidates(self):
        for query in ('你好', '谢谢', '你叫什么', '今天的天气怎么样', '你喜欢什么颜色'):
            hits, topic, reason = await self.service.chat_search('alice', query)
            self.assertEqual(hits, [])
        self.assertEqual(self.embedder.calls, [])
        hits, topic, reason = await self.service.chat_search('alice', '年假应该怎么申请？')
        self.assertEqual(reason, 'keyword')
        self.assertTrue(hits)
        self.assertEqual({hit['document_id'] for hit in hits}, {self.leave})
        self.assertEqual(len(self.embedder.calls), 1)
        hits, _, reason = await self.service.chat_search('alice', '那要多久？', previous=topic)
        self.assertEqual(reason, 'followup')
        self.assertEqual({hit['document_id'] for hit in hits}, {self.leave})
        hits, _, reason = await self.service.chat_search('alice', '今天的天气怎么样？', previous=topic)
        self.assertEqual(hits, [])
        self.assertEqual(len(self.embedder.calls), 2)

    async def test_deleted_document_and_expired_followup_cannot_reuse_old_scope(self):
        _, topic, _ = await self.service.chat_search('alice', '请假怎么审批？')
        topic['at'] -= 121
        self.assertEqual((await self.service.chat_search('alice','那要多久？',previous=topic))[0], [])
        topic['at'] += 121
        self.store.delete('alice', self.leave)
        self.assertEqual((await self.service.chat_search('alice','那要多久？',previous=topic))[0], [])
        self.assertEqual(self.store.search('alice', '请假', vector=[1,0,0], model=self.embedder.model, document_ids=[self.secret]), [])

    async def test_manual_profile_survives_rebuild_and_old_job_cannot_restore_deleted_file(self):
        self.store.save_profile('alice', self.leave, {'summary':'手工简介', 'keywords':['特殊假期'], 'aliases':[]})
        self.store.retry('alice', self.leave)
        job = self.store.claim_next()
        self.store.finish(job, ['重建内容'], profile={'summary':'自动简介','keywords':['自动关键词']})
        result = next(doc for doc in self.store.profiles('alice') if doc['id'] == self.leave)
        self.assertEqual(result['summary'], '手工简介')
        self.store.retry('alice', self.leave)
        stale = self.store.claim_next()
        self.store.delete('alice', self.leave)
        self.assertFalse(self.store.finish(stale, ['迟到内容'], profile={'summary':'不应复活'}))

    async def test_version_one_migrates_preserving_original_and_backup_inspection(self):
        with closing(sqlite3.connect(self.path)) as db:
            for column in ('summary','keywords_json','aliases_json','profile_source','profile_version'):
                db.execute('ALTER TABLE knowledge_documents DROP COLUMN ' + column)
            db.execute("UPDATE schema_migrations SET version=1 WHERE component='knowledge'")
            db.commit()
        self.assertEqual(inspect_database(self.path)['schema_versions']['knowledge'], 1)
        migrated = KnowledgeStore(self.path)
        self.assertIn('提前三天', migrated.download('alice', self.leave)['original'].decode())
        self.assertEqual(inspect_database(self.path)['schema_versions']['knowledge'], 2)
        self.assertTrue(migrated.profiles('alice')[0]['summary'])


if __name__ == '__main__':
    unittest.main()
