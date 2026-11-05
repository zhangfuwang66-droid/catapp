"""双设备模拟验证：连接、控制、记录和记忆均不访问硬件或云模型。"""

import asyncio
from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

# app 的默认数据库也放在临时目录，避免测试碰到实际聊天数据。
_import_data = tempfile.TemporaryDirectory(prefix="cybermiao-runtime-import-")
with patch.dict(os.environ, {"CYBERMIAO_DATA_DIR": _import_data.name,
                             "DASHSCOPE_API_KEY": "simulated-model-only",
                             "CYBERMIAO_MEMORY_DB_PATH": str(Path(_import_data.name) / "default.sqlite3")}):
    import app as server_app
import device_runtime as runtime_module
from device_connections import DeviceConnectionSlot
from device_registry import DeviceRegistry
from memory_store import MemoryManager, MemoryStore, SummaryResult
from omni_realtime_client import OmniRealtimeSession


async def summarize(messages):
    return SummaryResult(summary="模拟对话摘要")


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="device-state-")
        self.patch = patch.object(runtime_module, "RECORDS_DIR", str(Path(self.temp.name) / "records"))
        self.patch.start()
        self.store = MemoryStore(Path(self.temp.name) / "memory.sqlite3")
        self.manager = MemoryManager(self.store, summarize, idle_seconds=300)
        self.devices = DeviceRegistry(self.manager)
        self.a = self.devices.get("cat-a", "owner-a")
        self.b = self.devices.get("cat-b", "owner-b")

    async def asyncTearDown(self):
        await self.devices.close()
        await self.manager.close()
        self.patch.stop()
        self.temp.cleanup()

    async def test_device_reset_does_not_stop_another_device_or_recognizer(self):
        class Recognizer:
            stopped = False

            def stop(self):
                self.stopped = True

        ra, rb = Recognizer(), Recognizer()
        await self.a.recognition.set_current(ra)
        await self.b.recognition.set_current(rb)
        marker_a, marker_b = asyncio.Event(), asyncio.Event()
        self.a.audio.set_playing_task_marker(marker_a)
        self.b.audio.set_playing_task_marker(marker_b)
        self.a.current_partial = "设备 A"
        self.b.current_partial = "设备 B"
        await self.a.full_system_reset("模拟重置")
        self.assertTrue(ra.stopped)
        self.assertFalse(rb.stopped)
        self.assertFalse(self.a.audio.is_playing_now())
        self.assertTrue(self.b.audio.is_playing_now())
        self.assertEqual(self.b.current_partial, "设备 B")

    async def test_record_files_and_memory_are_scoped_to_device(self):
        for device, image in ((self.a, b"image-a"), (self.b, b"image-b")):
            device.recording_active = True
            device.recording_session_id = "same-session"
            device.last_frames.append((runtime_module.time.time(), image))
        with patch.object(runtime_module, "describe_image_async", AsyncMock(return_value="模拟图片")):
            ea, eb = await asyncio.gather(self.a._record_tick_once(), self.b._record_tick_once())
        self.assertEqual(Path(ea["image_path"]).read_bytes(), b"image-a")
        self.assertEqual(Path(eb["image_path"]).read_bytes(), b"image-b")
        self.assertNotEqual(ea["image_path"], eb["image_path"])
        self.assertIn("/api/devices/cat-a/", ea["image_url"])
        with self.assertRaises(HTTPException):
            await self.b.record_image("..", Path(ea["image_path"]).name)
        conversation_a, _ = await self.a._ensure_ui_memory_context()
        conversation_b, _ = await self.b._ensure_ui_memory_context()
        await self.manager.record_message(conversation_a, "user", "A 的模拟记忆")
        await self.manager.record_message(conversation_b, "user", "B 的模拟记忆")
        context_a = await self.manager.context_for("owner-a", "cat-a")
        context_b = await self.manager.context_for("owner-b", "cat-b")
        self.assertEqual([m["content"] for m in context_a["messages"]], ["A 的模拟记忆"])
        self.assertEqual([m["content"] for m in context_b["messages"]], ["B 的模拟记忆"])

    async def test_concurrent_ui_initialization_uses_one_conversation(self):
        contexts = await asyncio.gather(*(self.a._ensure_ui_memory_context() for _ in range(8)))
        self.assertEqual(len({context[0] for context in contexts}), 1)

    async def test_close_cancels_owned_tasks_and_rejects_invalid_device_paths(self):
        task = self.a.spawn_background(asyncio.Event().wait())
        self.a.ensure_screen_sender_task()
        self.a.audio.set_playing_task_marker(asyncio.Event())
        await self.a.close()
        self.assertTrue(task.done())
        self.assertTrue(self.a.screen_sender_task.done())
        self.assertFalse(self.a.audio.is_playing_now())
        for identifier in ("..", "a/b", "a\\b", "", "a" * 65):
            with self.assertRaises(ValueError):
                self.devices.get(identifier, "owner-a")
        with self.assertRaises(ValueError):
            self.devices.get("cat-b", "owner-a")


class FakeSocket:
    def __init__(self):
        self.close_code = None

    async def close(self, code):
        self.close_code = code


class ConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_realtime_close_handles_its_own_callback_task(self):
        callback = AsyncMock()
        session = OmniRealtimeSession(
            loop=asyncio.get_running_loop(), system_prompt="模拟模型",
            on_input_transcript=callback, on_output_text_delta=callback, on_output_audio=callback,
            on_response_started=callback, on_response_done=callback, on_speech_started=callback,
            on_error=callback,
        )
        session._message_task = asyncio.current_task()
        await session.close()
        self.assertFalse(asyncio.current_task().cancelling())
        child = asyncio.create_task(asyncio.Event().wait())
        session._message_task = child
        await session.close()
        self.assertTrue(child.cancelled())

    async def test_replacement_waits_for_old_cleanup(self):
        slot = DeviceConnectionSlot()
        old_started, cleaning, allow_cleanup, new_started, finish_new = (asyncio.Event() for _ in range(5))

        async def old_handler(ws):
            old_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await allow_cleanup.wait()

        async def new_handler(ws):
            new_started.set()
            await finish_new.wait()

        old = asyncio.create_task(slot.run(FakeSocket(), old_handler))
        await old_started.wait()
        new = asyncio.create_task(slot.run(FakeSocket(), new_handler))
        await cleaning.wait()
        self.assertFalse(new_started.is_set())
        allow_cleanup.set()
        await asyncio.wait_for(new_started.wait(), timeout=1)
        finish_new.set()
        await asyncio.gather(old, new)
        await slot.close()

    async def test_cleanup_timeout_rejects_new_connection(self):
        slot = DeviceConnectionSlot(cleanup_timeout=0.02)
        started, allow_cleanup = asyncio.Event(), asyncio.Event()

        async def handler(ws):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await allow_cleanup.wait()

        old = asyncio.create_task(slot.run(FakeSocket(), handler))
        await started.wait()
        rejected = FakeSocket()
        await slot.run(rejected, AsyncMock())
        self.assertEqual(rejected.close_code, 1013)
        allow_cleanup.set()
        await old
        await slot.close()


class FakeRealtimeSession:
    def __init__(self, **callbacks):
        self.callbacks = callbacks
        self.closed = False
        self.is_fatal = False

    async def ensure_connected(self):
        pass

    async def append_audio(self, data):
        await self.callbacks["on_input_transcript"](data.decode("utf-8"))
        await self.callbacks["on_response_started"]()
        await self.callbacks["on_output_text_delta"]("模拟答复")
        await self.callbacks["on_output_audio"](b"\x01\x00" * 10)
        await self.callbacks["on_response_done"]()

    async def close(self):
        self.closed = True


class DeviceEndpointsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="device-endpoints-")
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(runtime_module, "RECORDS_DIR", str(Path(self.temp.name) / "records")))
        self.stack.enter_context(patch.object(runtime_module, "HAND_TRACKING_ENABLED", False))
        self.stack.enter_context(patch.object(runtime_module, "COVER_INTERACTION_ENABLED", False))
        self.stack.enter_context(patch.object(runtime_module, "ENABLE_ESP32_SCREEN_PUSH", False))
        self.stack.enter_context(patch.object(runtime_module, "API_KEY", "simulated-model-only"))
        self.stack.enter_context(patch.object(runtime_module, "OmniRealtimeSession", FakeRealtimeSession))
        self.application = server_app.create_app(store=MemoryStore(Path(self.temp.name) / "memory.sqlite3"), summarizer=summarize)
        self.client = self.stack.enter_context(TestClient(self.application))

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()

    def camera(self, name):
        ws = self.stack.enter_context(self.client.websocket_connect(f"/ws/devices/{name}/camera"))
        self.assertTrue(ws.receive_text().startswith("SCRMODE:"))
        self.assertTrue(ws.receive_text().startswith("SET:FPS="))
        return ws

    def audio(self, name):
        return self.stack.enter_context(self.client.websocket_connect(f"/ws/devices/{name}/audio"))

    def test_camera_frames_and_reconnection_stay_with_device(self):
        camera_a, camera_b = self.camera("cat-a"), self.camera("cat-b")
        viewer_a = self.stack.enter_context(self.client.websocket_connect("/ws/devices/cat-a/viewer"))
        viewer_b = self.stack.enter_context(self.client.websocket_connect("/ws/devices/cat-b/viewer"))
        camera_a.send_bytes(b"frame-a")
        camera_b.send_bytes(b"frame-b")
        self.assertEqual(viewer_a.receive_bytes(), b"frame-a")
        self.assertEqual(viewer_b.receive_bytes(), b"frame-b")
        replacement = self.camera("cat-a")
        replacement.send_bytes(b"new-a")
        camera_b.send_bytes(b"still-b")
        self.assertEqual(viewer_a.receive_bytes(), b"new-a")
        self.assertEqual(viewer_b.receive_bytes(), b"still-b")

    def test_control_and_audio_replacement_do_not_cross_devices(self):
        audio_a, audio_b = self.audio("cat-a"), self.audio("cat-b")
        self.assertTrue(self.client.get("/api/devices/cat-a/servo?ch=3&angle=91").json()["ok"])
        self.assertEqual(audio_a.receive_text(), "SERVO:3,91")
        replacement_a = self.audio("cat-a")
        self.assertTrue(self.client.get("/api/devices/cat-a/servo?ch=3&angle=92").json()["ok"])
        self.assertEqual(replacement_a.receive_text(), "SERVO:3,92")
        self.assertTrue(self.client.get("/api/devices/cat-b/servo?ch=3&angle=93").json()["ok"])
        self.assertEqual(audio_b.receive_text(), "SERVO:3,93")

    def test_audio_model_callbacks_write_separate_device_memories(self):
        for name, text in (("cat-a", "模拟话题甲"), ("cat-b", "模拟话题乙")):
            ws = self.audio(name)
            ws.send_text("START")
            self.assertEqual(ws.receive_text(), "OK:STARTED")
            ws.send_bytes(text.encode("utf-8"))
            self.assertEqual(ws.receive_text(), "STOP")
            self.assertEqual(ws.receive_text(), "TTS_START")
            self.assertEqual(ws.receive_text(), "START")
        memory_a = self.client.get("/api/devices/cat-a/memory").json()["recent_messages"]
        memory_b = self.client.get("/api/devices/cat-b/memory").json()["recent_messages"]
        self.assertEqual([m["content"] for m in memory_a], ["模拟话题甲", "模拟答复"])
        self.assertEqual([m["content"] for m in memory_b], ["模拟话题乙", "模拟答复"])


if __name__ == "__main__":
    unittest.main()
