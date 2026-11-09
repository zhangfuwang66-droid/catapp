"""真实 SQL 检索接入聊天提示词，模型和摄像头均模拟。"""

import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from support import server_app
import device_runtime
from device_registry import DeviceRegistry
from knowledge_service import KnowledgeService
from knowledge_store import KnowledgeStore, split_text
from memory_store import MemoryManager, MemoryStore, SummaryResult


async def summarize(messages):
    return SummaryResult(summary="模拟摘要")


class KeywordOnly:
    model = "offline-test"

    async def embed(self, texts, *, timeout=8):
        raise ConnectionError("offline test")

    async def close(self):
        pass


class KnowledgeChatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="knowledge-chat-")
        self.store = MemoryStore(Path(self.temp.name) / "memory.sqlite3")
        self.manager = MemoryManager(self.store, summarize)
        self.knowledge = KnowledgeService(KnowledgeStore(self.store.path), embedder=KeywordOnly())
        for user, code in (("alice", "ALICE-7492"), ("bob", "BOB-1836")):
            text = f"蓝星项目的集合口令为 {code}，集合地点是星港三号门。"
            self.knowledge.store.add_document(user, user + ".txt", text.encode())
            self.knowledge.store.finish(self.knowledge.store.claim_next(), split_text(text))
        self.devices = DeviceRegistry(self.manager, knowledge=self.knowledge)

    async def asyncTearDown(self):
        await self.devices.close()
        await self.manager.close()
        await self.knowledge.close()
        self.temp.cleanup()

    async def test_text_chat_uses_owner_documents_and_next_turn_drops_old_context(self):
        prompts = []

        async def stream(content, **options):
            prompts.append(options["system_prompt"])
            yield SimpleNamespace(text_delta="收到", audio_b64=None)

        with patch.object(device_runtime, "stream_chat", stream):
            for user in ("alice", "bob"):
                runtime = self.devices.get("cat-" + user, user)
                with patch.object(runtime, "capture_camera_frame_on_demand", AsyncMock(return_value=None)), \
                        patch.object(runtime, "reset_hand_tracking_state", AsyncMock()), \
                        patch.object(runtime, "ui_broadcast_raw", AsyncMock()) as events:
                    await runtime.start_ai_with_text("蓝星项目的集合口令是什么？")
                    await asyncio.wait_for(runtime.audio.current_ai_task, timeout=3)
                    sources = [call.args[0] for call in events.await_args_list if call.args[0].startswith("KNOWLEDGE:")]
                    self.assertIn(user + ".txt", sources[0])
                    self.assertIn("不可信参考", prompts[-1])
            self.assertIn("ALICE-7492", prompts[0])
            self.assertNotIn("BOB-1836", prompts[0])
            self.assertIn("BOB-1836", prompts[1])
            self.assertNotIn("ALICE-7492", prompts[1])
            runtime = self.devices.get("cat-alice", "alice")
            with patch.object(runtime, "capture_camera_frame_on_demand", AsyncMock(return_value=None)), \
                    patch.object(runtime, "reset_hand_tracking_state", AsyncMock()):
                await runtime.start_ai_with_text("今天的天气怎么样？")
                await asyncio.wait_for(runtime.audio.current_ai_task, timeout=3)
            self.assertNotIn("【本轮资料检索】", prompts[-1])
            self.assertNotIn("ALICE-7492", prompts[-1])
            memory = await self.manager.context_for("alice", "cat-alice")
            self.assertNotIn("ALICE-7492", str(memory))

    async def test_lookup_failure_and_cancellation_do_not_use_cached_passages(self):
        runtime = self.devices.get("cat-alice", "alice")
        with patch.object(self.knowledge, "search", AsyncMock(side_effect=ConnectionError)):
            self.assertIn("检索暂不可用", await runtime.knowledge_context("集合口令"))
        with patch.object(self.knowledge, "search", AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await runtime.knowledge_context("集合口令")


if __name__ == "__main__":
    unittest.main()
