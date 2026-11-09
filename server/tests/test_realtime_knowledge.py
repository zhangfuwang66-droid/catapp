"""模拟服务端 ACK、VAD 打断和迟到事件，验证检索先于回答。"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import omni_realtime_client as realtime


class ModelSocket:
    def __init__(self, *, manual_supported=True):
        self.closed = False
        self.events = asyncio.Queue()
        self.sent = []
        self.config = {}
        self.auto_ack = True
        self.manual_supported = manual_supported
        self.emit("session.created")

    def emit(self, kind, **data):
        self.events.put_nowait(json.dumps({"type": kind, **data}))

    def ack(self, **overrides):
        self.emit("session.updated", session={**self.config, **overrides})

    async def send(self, raw):
        event = json.loads(raw)
        self.sent.append(event)
        if event["type"] == "session.update":
            self.config.update(event["session"])
            if not self.manual_supported:
                self.config["turn_detection"] = {"type": "server_vad", "create_response": True}
            if self.auto_ack:
                self.ack()

    async def close(self):
        self.closed = True
        self.events.put_nowait(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        event = await self.events.get()
        if event is None:
            raise StopAsyncIteration
        return event

    @property
    def responses(self):
        return [event for event in self.sent if event["type"] == "response.create"]


async def until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(.005)
    await asyncio.wait_for(wait(), timeout=2)


class RealtimeKnowledgeTests(unittest.IsolatedAsyncioTestCase):
    async def connected(self, callback, *, manual_supported=True):
        self.socket = ModelSocket(manual_supported=manual_supported)
        self.error = AsyncMock()
        noop = AsyncMock()
        self.session = realtime.OmniRealtimeSession(loop=asyncio.get_running_loop(), system_prompt="base",
            on_input_transcript=callback, on_output_text_delta=noop, on_output_audio=noop,
            on_response_started=noop, on_response_done=noop, on_speech_started=noop, on_error=self.error,
            manual_response=True)
        self.addAsyncCleanup(self.session.close)
        with patch.object(realtime, "API_KEY", "simulated-key"), \
                patch.object(realtime.websockets, "connect", AsyncMock(return_value=self.socket)):
            await self.session.ensure_connected()
        return self.session

    def utterance(self, item, text):
        self.socket.emit("input_audio_buffer.speech_started", item_id=item)
        self.socket.emit("conversation.item.input_audio_transcription.completed", item_id=item, transcript=text)

    async def test_response_waits_for_retrieval_and_matching_instruction_ack(self):
        searching, finish_search = asyncio.Event(), asyncio.Event()

        async def callback(text):
            searching.set()
            await finish_search.wait()
            await self.session.update_instructions("base + retrieved passage", wait_for_ack=True)

        await self.connected(callback)
        self.assertIs(self.socket.config["turn_detection"]["create_response"], False)
        self.socket.auto_ack = False
        self.utterance("one", "集合口令是什么")
        await searching.wait()
        self.assertEqual(len(self.socket.responses), 0)
        finish_search.set()
        await until(lambda: self.socket.config["instructions"] == "base + retrieved passage")
        self.socket.ack(instructions="old prompt")
        await asyncio.sleep(.02)
        self.assertEqual(len(self.socket.responses), 0)
        self.socket.ack()
        await until(lambda: len(self.socket.responses) == 1)
        self.assertEqual(self.socket.responses[0]["response"]["instructions"], "base + retrieved passage")
        self.socket.emit("conversation.item.input_audio_transcription.completed", item_id="one", transcript="重复事件")
        await asyncio.sleep(.02)
        self.assertEqual(len(self.socket.responses), 1)

    async def test_interruption_cancels_old_search_and_ignores_delayed_old_transcript(self):
        old_started, old_cancelled, late_return = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = []

        async def callback(text):
            calls.append(text)
            if text == "旧问题":
                old_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    # 即使依赖延迟返回，也不能为旧问题触发新回答。
                    old_cancelled.set()
                    await late_return.wait()
                return
            await self.session.update_instructions("new question, no old passage", wait_for_ack=True)

        await self.connected(callback)
        self.utterance("old", "旧问题")
        await old_started.wait()
        self.utterance("new", "新问题")
        await old_cancelled.wait()
        await until(lambda: len(self.socket.responses) == 1)
        late_return.set()
        self.socket.emit("conversation.item.input_audio_transcription.completed", item_id="old", transcript="旧转录迟到")
        await until(lambda: not self.session._turn_tasks)
        self.assertEqual(calls, ["旧问题", "新问题"])
        self.assertEqual(len(self.socket.responses), 1)

    async def test_close_cleans_preparation_waiting_for_ack(self):
        async def callback(text):
            await self.session.update_instructions("wait for ack", wait_for_ack=True)
        await self.connected(callback)
        self.socket.auto_ack = False
        self.utterance("one", "test")
        await until(lambda: self.session._instruction_waiter is not None)
        await asyncio.wait_for(self.session.close(), timeout=1)
        self.assertFalse(self.session._turn_tasks)
        self.assertIsNone(self.session._instruction_waiter)
        self.assertEqual(len(self.socket.responses), 0)

    async def test_unsupported_auto_response_control_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "未确认关闭自动回答"):
            await self.connected(AsyncMock(), manual_supported=False)
        self.assertTrue(self.socket.closed)
        self.assertTrue(self.session.is_fatal)

    async def test_context_switch_suppresses_response_without_cancelling_own_callback(self):
        done = asyncio.Event()

        async def callback(text):
            await self.session.cancel_response()
            done.set()
            return False

        await self.connected(callback)
        self.utterance("one", "进入翻译模式")
        await asyncio.wait_for(done.wait(), timeout=1)
        await until(lambda: not self.session._turn_tasks)
        self.assertEqual(len(self.socket.responses), 0)

    async def test_failed_preparation_does_not_answer_with_stale_context(self):
        await self.connected(AsyncMock(side_effect=TimeoutError))
        self.utterance("one", "test")
        await until(lambda: self.error.await_count > 0)
        self.assertEqual(len(self.socket.responses), 0)
        self.assertIn("TimeoutError", self.error.await_args.args[0])

    async def test_close_while_sender_waits_for_lock_does_not_send_to_closed_connection(self):
        await self.connected(AsyncMock())
        async with self.session._send_lock:
            pending = asyncio.create_task(self.session._send_event({"type": "response.create"}))
            await asyncio.sleep(0)
            await self.session.close()
        with self.assertRaisesRegex(RuntimeError, "WebSocket 已关闭"):
            await pending
        self.assertEqual(len(self.socket.responses), 0)


if __name__ == "__main__":
    unittest.main()
