"""模拟设备验证暂停、重连、迟到回调和恢复，不连接真实模型或硬件。"""
import asyncio
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch

from support import server_app  # 隔离配置导入
import device_runtime as runtime_module
from device_registry import DeviceRegistry
from memory_store import MemoryManager, MemoryStore, SummaryResult
from starlette.websockets import WebSocketState


async def summarize(messages):
    return SummaryResult(summary="模拟摘要")


async def until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(.005)
    await asyncio.wait_for(wait(), 2)


class AudioSocket:
    client_state = WebSocketState.CONNECTED

    def __init__(self):
        self.incoming = asyncio.Queue()
        self.commands = []

    async def accept(self):
        pass

    async def receive(self):
        return await self.incoming.get()

    async def send_text(self, text):
        self.commands.append(text)

    def send(self, text):
        self.incoming.put_nowait({"text": text})


class Model:
    instances = []

    def __init__(self, **callbacks):
        self.callbacks = callbacks
        self.closed = False
        self.is_fatal = False
        self.audio = []
        self.instances.append(self)

    async def ensure_connected(self):
        pass

    async def append_audio(self, data):
        self.audio.append(data)

    async def close(self):
        self.closed = True

    async def cancel_response(self):
        pass


class VoiceControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = MemoryManager(MemoryStore(Path(self.temp.name) / 'memory.sqlite3'), summarize)
        self.devices = DeviceRegistry(self.manager)
        self.runtime = self.devices.get('cat-a', 'owner-a')
        Model.instances = []
        self.models = patch.object(runtime_module, 'OmniRealtimeSession', Model)
        self.key = patch.object(runtime_module, 'API_KEY', 'simulated-only')
        self.models.start()
        self.key.start()
        self.connections = []

    async def asyncTearDown(self):
        for socket, task in self.connections:
            if not task.done():
                socket.incoming.put_nowait({'type': 'websocket.disconnect'})
            await asyncio.wait_for(task, 3)
        await self.devices.close()
        await self.manager.close()
        self.key.stop()
        self.models.stop()
        self.temp.cleanup()

    async def connect(self, runtime=None):
        runtime = runtime or self.runtime
        socket = AudioSocket()
        task = asyncio.create_task(runtime.ws_audio(socket))
        self.connections.append((socket, task))
        socket.send('START')
        await until(lambda: 'OK:STARTED' in socket.commands)
        return socket

    async def test_pause_stops_model_playback_and_upload_but_keeps_other_connections(self):
        socket = await self.connect()
        other = self.devices.get('cat-b', 'owner-b')
        other_socket = await self.connect(other)
        camera = self.runtime.esp32_camera_ws = AudioSocket()
        ui = self.runtime.ui_clients[1] = type('UI', (), {'send_text': AsyncMock()})()
        model, other_model = Model.instances
        await model.callbacks['on_response_started']()
        await model.callbacks['on_output_audio'](b'\x01\x00' * 24)
        self.assertTrue(self.runtime.audio.recent_pcm_chunks)
        state = await self.runtime.set_voice_paused(True)
        self.assertTrue(state['paused'])
        self.assertFalse(state['active'])
        self.assertTrue(model.closed)
        self.assertFalse(self.runtime.audio.recent_pcm_chunks)
        self.assertIs(self.runtime.esp32_camera_ws, camera)
        self.assertIs(self.runtime.ui_clients[1], ui)
        self.assertEqual(socket.commands[-2:], ['TTS_STOP', 'STOP'])
        before = list(socket.commands)
        await model.callbacks['on_output_audio'](b'late')
        await model.callbacks['on_response_done']()
        self.assertEqual(socket.commands, before)
        socket.incoming.put_nowait({'bytes': b'paused audio'})
        other_socket.incoming.put_nowait({'bytes': b'other audio'})
        await until(lambda: other_model.audio)
        self.assertEqual(model.audio, [])
        self.assertEqual(other_model.audio, [b'other audio'])
        self.assertFalse(other.voice_paused)
        self.assertTrue(ui.send_text.await_args.args[0].startswith('VOICE:'))

    async def test_reconnect_keeps_pause_and_resume_opens_model_before_start(self):
        socket = await self.connect()
        await self.runtime.set_voice_paused(True)
        socket.incoming.put_nowait({'type': 'websocket.disconnect'})
        await self.connections[0][1]
        replacement = await self.connect()
        self.assertEqual(len(Model.instances), 1)
        self.assertEqual(replacement.commands[:2], ['TTS_STOP', 'STOP'])
        state = await self.runtime.set_voice_paused(False)
        self.assertTrue(state['active'])
        self.assertEqual(len(Model.instances), 2)
        self.assertEqual(replacement.commands[-1], 'START')
        replacement.incoming.put_nowait({'bytes': b'new audio'})
        await until(lambda: Model.instances[-1].audio)

    async def test_already_running_done_callback_cannot_restart_after_pause_and_resume(self):
        socket = await self.connect()
        model = Model.instances[-1]
        await model.callbacks['on_response_started']()
        done = asyncio.create_task(model.callbacks['on_response_done']())
        await asyncio.sleep(.02)  # 进入播放尾部等待，此时暂停并创建新会话。
        await self.runtime.set_voice_paused(True)
        await self.runtime.set_voice_paused(False)
        before = list(socket.commands)
        await done
        self.assertEqual(socket.commands, before)
        self.assertIs(self.runtime.current_realtime_session, Model.instances[-1])

    async def test_performance_mode_does_not_override_manual_pause(self):
        await self.connect()
        await self.runtime.set_voice_paused(True)
        await self.runtime.set_performance_mode(True)
        await self.runtime.set_performance_mode(False)
        self.assertTrue(self.runtime.voice_paused)
        self.assertIsNone(self.runtime.current_realtime_session)
        await self.runtime.set_voice_paused(False)
        self.assertIsNotNone(self.runtime.current_realtime_session)

    async def test_pause_during_model_connection_does_not_leave_session_open(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def connecting(model):
            entered.set()
            await release.wait()

        with patch.object(Model, 'ensure_connected', connecting):
            connecting_task = asyncio.create_task(self.connect())
            await entered.wait()
            pausing = asyncio.create_task(self.runtime.set_voice_paused(True))
            await until(lambda: self.runtime.voice_paused)
            release.set()
            await connecting_task
            await pausing
        self.assertTrue(Model.instances[0].closed)
        self.assertIsNone(self.runtime.current_realtime_session)

    async def test_slow_emotion_analysis_does_not_block_text_or_pcm(self):
        await self.connect()
        model = Model.instances[-1]
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()

        def slow_emotion(text, api_key=None):
            loop.call_soon_threadsafe(entered.set)
            release.wait(2)
            return 'happy'

        try:
            with patch.object(runtime_module, 'analyze_emotion', slow_emotion):
                await model.callbacks['on_response_started']()
                await asyncio.wait_for(model.callbacks['on_output_text_delta']('这是足够触发情绪分析的模拟回复'), .3)
                await asyncio.wait_for(entered.wait(), 1)
                await asyncio.wait_for(model.callbacks['on_output_audio'](b'\x01\x00' * 240), .3)
                self.assertTrue(self.runtime.audio.recent_pcm_chunks)
                self.assertFalse(release.is_set())
                release.set()
                await until(lambda: self.runtime.current_emotion == 'happy')
        finally:
            release.set()

    async def test_pause_closes_even_an_upload_that_was_reconnecting(self):
        socket = await self.connect()
        model = Model.instances[-1]
        entered, release = asyncio.Event(), asyncio.Event()

        async def reconnecting_upload(current, data):
            entered.set()
            await release.wait()
            current.closed = False  # 模拟 ensure_connected 完成较慢的上游重连。

        with patch.object(Model, 'append_audio', reconnecting_upload):
            socket.incoming.put_nowait({'bytes': b'old audio'})
            await asyncio.wait_for(entered.wait(), 1)
            pausing = asyncio.create_task(self.runtime.set_voice_paused(True))
            await until(lambda: 'TTS_STOP' in socket.commands)
            release.set()
            await asyncio.wait_for(pausing, 1)
        self.assertTrue(model.closed)
        self.assertIsNone(self.runtime.current_realtime_session)

    async def test_old_emotion_cannot_override_next_response_or_resumed_session(self):
        await self.connect()
        loop = asyncio.get_running_loop()
        for replace_session in (False, True):
            with self.subTest(replace_session=replace_session):
                entered, returned, release = asyncio.Event(), asyncio.Event(), threading.Event()

                def slow_emotion(text, api_key=None):
                    loop.call_soon_threadsafe(entered.set)
                    release.wait(2)
                    loop.call_soon_threadsafe(returned.set)
                    return 'angry'

                self.runtime.current_emotion = 'neutral'
                model = Model.instances[-1]
                try:
                    with patch.object(runtime_module, 'analyze_emotion', slow_emotion):
                        await model.callbacks['on_response_started']()
                        await model.callbacks['on_output_text_delta']('旧一轮回复正在分析情绪')
                        await asyncio.wait_for(entered.wait(), 1)
                        if replace_session:
                            await self.runtime.set_voice_paused(True)
                            await self.runtime.set_voice_paused(False)
                        else:
                            await model.callbacks['on_response_started']()
                        release.set()
                        await asyncio.wait_for(returned.wait(), 1)
                        await asyncio.sleep(.02)
                        self.assertEqual(self.runtime.current_emotion, 'neutral')
                finally:
                    release.set()


if __name__ == '__main__':
    unittest.main()
