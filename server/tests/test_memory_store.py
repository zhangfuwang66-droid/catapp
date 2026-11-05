"""聊天记忆存储和空闲总结调度的本地测试。"""

import asyncio
from pathlib import Path
import tempfile
import threading
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
    async def test_close_waits_for_database_write_already_in_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(Path(directory) / "memory.sqlite3")
            conversation = store.create_conversation("user-a", "cat-a")
            store.append_message(conversation, "user", "关闭时正在写入的模拟消息")
            started, release = threading.Event(), threading.Event()
            save_summary = store.save_summary

            def delayed_save(*args):
                started.set()
                if not release.wait(timeout=5):
                    raise TimeoutError("测试写入等待超时")
                save_summary(*args)

            store.save_summary = delayed_save

            async def summarize(messages):
                return SummaryResult(summary="关闭前的模拟摘要")

            manager = MemoryManager(store, summarize)
            task = asyncio.create_task(manager.summarize_now(conversation))
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            closing = asyncio.create_task(manager.close())
            await asyncio.sleep(0.01)
            self.assertFalse(closing.done())
            release.set()
            await closing
            self.assertTrue(task.cancelled())
            self.assertEqual(store.pending_messages(conversation), [])

    async def test_long_conversation_summarizes_all_messages_in_order(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(Path(directory) / "memory.sqlite3")
            conversation = store.create_conversation("user-a", "cat-a")
            await asyncio.to_thread(lambda: [store.append_message(conversation, "user", f"模拟消息 {i}") for i in range(75)])
            batches = []

            async def summarize(messages):
                batches.append([m["content"] for m in messages])
                return SummaryResult(summary="一批模拟摘要")

            manager = MemoryManager(store, summarize)
            self.assertTrue(await manager.summarize_now(conversation))
            self.assertEqual([len(batch) for batch in batches], [40, 35])
            self.assertEqual([item for batch in batches for item in batch], [f"模拟消息 {i}" for i in range(75)])
            self.assertEqual(store.pending_messages(conversation), [])
            await manager.close()

    async def test_failed_later_batch_remains_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(Path(directory) / "memory.sqlite3")
            conversation = store.create_conversation("user-a", "cat-a")
            ids = await asyncio.to_thread(lambda: [store.append_message(conversation, "user", f"消息 {i}") for i in range(50)])
            calls = 0

            async def summarize(messages):
                nonlocal calls
                calls += 1
                return SummaryResult(summary="第一批完成" if calls == 1 else "")

            manager = MemoryManager(store, summarize)
            self.assertTrue(await manager.summarize_now(conversation))
            self.assertEqual([m["id"] for m in store.pending_messages(conversation)], ids[40:])
            await manager.close()

    async def test_clear_cancels_inflight_summary_without_resurrecting_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(Path(directory) / "memory.sqlite3")
            conversation = store.create_conversation("user-a", "cat-a")
            store.append_message(conversation, "user", "将被主动清除的模拟消息")
            started = asyncio.Event()

            async def summarize(messages):
                started.set()
                await asyncio.Event().wait()
                return SummaryResult(summary="不应写回的内容")

            manager = MemoryManager(store, summarize)
            task = asyncio.create_task(manager.summarize_now(conversation))
            await started.wait()
            await manager.clear_owner("user-a", "cat-a")
            self.assertTrue(task.cancelled())
            self.assertEqual(store.context_for("user-a", "cat-a"), {"messages": [], "summaries": [], "memories": []})
            with self.assertRaises(ValueError):
                store.save_summary(conversation, 1, "旧回调", memories=["不能复活"])
            await manager.close()

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

    async def test_startup_scan_recovers_pending_conversation_without_waiting_for_idle(self):
        with tempfile.TemporaryDirectory(prefix="小喵 memory ") as directory:
            store = MemoryStore(Path(directory) / "memory.sqlite3")
            conversation = store.create_conversation("user-a", "cat-a")
            store.append_message(conversation, "user", "服务重启前的消息")
            calls = []

            async def summarize(messages):
                calls.append(messages[0]["content"])
                return SummaryResult(summary="服务重启前的对话摘要。")

            manager = MemoryManager(store, summarize, idle_seconds=300, poll_seconds=0.01)
            await manager.start()
            await asyncio.sleep(0.08)
            self.assertEqual(calls, ["服务重启前的消息"])
            self.assertEqual(store.pending_messages(conversation), [])
            await manager.close()

    async def test_finish_conversation_summarizes_immediately(self):
        with tempfile.TemporaryDirectory(prefix="小喵 memory ") as directory:
            store = MemoryStore(Path(directory) / "memory.sqlite3")
            calls = []

            async def summarize(messages):
                calls.append([item["content"] for item in messages])
                return SummaryResult(summary="设备断开前的对话摘要。")

            manager = MemoryManager(store, summarize, idle_seconds=300, poll_seconds=0.01)
            await manager.start()
            conversation = await manager.start_conversation("user-a", "cat-a")
            await manager.record_message(conversation, "user", "设备即将断开")
            await manager.finish_conversation(conversation)
            await asyncio.sleep(0.08)
            self.assertEqual(calls, [["设备即将断开"]])
            self.assertEqual(store.pending_messages(conversation), [])
            await manager.close()

    async def test_flush_pending_summarizes_before_close(self):
        with tempfile.TemporaryDirectory(prefix="小喵 memory ") as directory:
            store = MemoryStore(Path(directory) / "memory.sqlite3")
            calls = []

            async def summarize(messages):
                calls.append(messages[0]["content"])
                return SummaryResult(summary="服务正常关闭前的摘要。")

            manager = MemoryManager(store, summarize, idle_seconds=300, poll_seconds=0.01)
            await manager.start()
            conversation = await manager.start_conversation("user-a", "cat-a")
            await manager.record_message(conversation, "user", "服务准备正常关闭")
            await manager.flush_pending(timeout=1)
            self.assertEqual(calls, ["服务准备正常关闭"])
            self.assertEqual(store.pending_messages(conversation), [])
            await manager.close()


if __name__ == "__main__":
    unittest.main()
