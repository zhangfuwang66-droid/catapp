"""聊天记忆存储和空闲总结调度的本地测试。"""

import asyncio
from pathlib import Path
import tempfile
import unittest

from memory_store import MemoryManager, MemoryStore, SummaryResult


class MemoryStoreTests(unittest.TestCase):
    def test_persists_messages_summaries_and_owner_isolation(self):
        with tempfile.TemporaryDirectory(prefix="小喵 memory ") as directory:
            path = Path(directory) / "memory.sqlite3"
            store = MemoryStore(path)
            first = store.create_conversation("user-a", "cat-a")
            second = store.create_conversation("user-b", "cat-b")
            first_user = store.append_message(first, "user", "我下周要考试")
            store.append_message(first, "assistant", "别紧张，我陪你准备。")
            store.append_message(second, "user", "这是另一个人的内容")

            store.save_summary(
                first,
                first_user + 1,
                "用户下周要参加考试，最近有些紧张。",
                memories=["用户下周要参加考试", "用户最近有些紧张"],
                open_topics=["考试准备"],
            )
            new_message = store.append_message(first, "user", "我今天复习了数学")

            context = store.context_for("user-a", "cat-a")
            self.assertEqual([item["content"] for item in context["messages"]], ["我今天复习了数学"])
            self.assertEqual(len(context["summaries"]), 1)
            self.assertEqual(context["summaries"][0]["open_topics"], ["考试准备"])
            self.assertEqual(
                {item["content"] for item in context["memories"]},
                {"用户下周要参加考试", "用户最近有些紧张"},
            )
            self.assertEqual([item["content"] for item in store.context_for("user-b", "cat-b")["messages"]], ["这是另一个人的内容"])
            self.assertEqual(store.pending_messages(first)[0]["id"], new_message)

            reopened = MemoryStore(path)
            reopened_context = reopened.context_for("user-a", "cat-a")
            self.assertEqual(len(reopened_context["summaries"]), 1)
            self.assertEqual(reopened_context["messages"][0]["content"], "我今天复习了数学")

    def test_rejects_invalid_message_and_owner_parts(self):
        with tempfile.TemporaryDirectory(prefix="小喵 memory ") as directory:
            store = MemoryStore(Path(directory) / "memory.sqlite3")
            with self.assertRaises(ValueError):
                store.create_conversation("", "cat-a")
            conversation = store.create_conversation("user-a", "cat-a")
            with self.assertRaises(ValueError):
                store.append_message(conversation, "user", "")
            with self.assertRaises(ValueError):
                store.append_message(conversation, "unknown", "内容")


class MemoryManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_summary_and_follow_up_message(self):
        with tempfile.TemporaryDirectory(prefix="小喵 memory ") as directory:
            store = MemoryStore(Path(directory) / "memory.sqlite3")
            calls = []

            async def summarize(messages):
                calls.append([item["content"] for item in messages])
                return SummaryResult(
                    summary="用户正在准备考试。",
                    memories=("用户正在准备考试",),
                    open_topics=("考试准备",),
                )

            manager = MemoryManager(store, summarize, idle_seconds=0.03, poll_seconds=0.01)
            await manager.start()
            conversation = await manager.start_conversation("user-a", "cat-a")
            await manager.record_message(conversation, "user", "我正在准备考试")
            await manager.record_message(conversation, "assistant", "我可以陪你复习")
            await asyncio.sleep(0.12)
            self.assertEqual(calls, [["我正在准备考试", "我可以陪你复习"]])
            self.assertEqual(store.pending_messages(conversation), [])

            await manager.record_message(conversation, "user", "今天先复习数学")
            await asyncio.sleep(0.12)
            self.assertEqual(calls[-1], ["今天先复习数学"])
            self.assertEqual(len(store.context_for("user-a", "cat-a")["summaries"]), 2)
            await manager.close()

    async def test_startup_scan_recovers_idle_conversation(self):
        with tempfile.TemporaryDirectory(prefix="小喵 memory ") as directory:
            store = MemoryStore(Path(directory) / "memory.sqlite3")
            conversation = store.create_conversation("user-a", "cat-a")
            store.append_message(conversation, "user", "服务重启前的消息")
            calls = []

            async def summarize(messages):
                calls.append(messages[0]["content"])
                return SummaryResult(summary="服务重启前的对话摘要。")

            manager = MemoryManager(store, summarize, idle_seconds=0, poll_seconds=0.01)
            await manager.start()
            await asyncio.sleep(0.08)
            self.assertEqual(calls, ["服务重启前的消息"])
            self.assertEqual(store.pending_messages(conversation), [])
            await manager.close()


if __name__ == "__main__":
    unittest.main()
