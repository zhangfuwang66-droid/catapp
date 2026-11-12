"""模拟文本和Realtime，验证每设备人格、时间和天气不会串到另一设备/翻译。"""

import asyncio
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from support import server_app
import device_runtime as runtime_module
from device_personas import CAT_PROMPT, DEER_PROMPT, PersonaStore
from device_registry import DeviceRegistry
from live_facts import LiveFacts, SHANGHAI
from memory_store import MemoryManager, MemoryStore, SummaryResult
from omni_client import OmniStreamPiece
from test_voice_control import AudioSocket, Model, until


async def summarize(_messages):
    return SummaryResult(summary='模拟摘要')


class RuntimePersonaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='runtime-personas-')
        self.stack = ExitStack()
        self.personas = PersonaStore(Path(self.temp.name) / 'personas')
        self.stack.enter_context(patch.object(runtime_module, 'PersonaStore', lambda: self.personas))
        self.stack.enter_context(patch.object(runtime_module, 'API_KEY', 'simulated-only'))
        for flag in ('HAND_TRACKING_ENABLED', 'COVER_INTERACTION_ENABLED', 'ENABLE_ESP32_SCREEN_PUSH'):
            self.stack.enter_context(patch.object(runtime_module, flag, False))
        self.manager = MemoryManager(MemoryStore(Path(self.temp.name) / 'memory.sqlite3'), summarize)
        self.devices = DeviceRegistry(self.manager)
        self.cat = self.devices.get('cat-original', 'owner-a')
        self.deer = self.devices.get('deer-original', 'owner-b')
        self.external = AsyncMock(side_effect=AssertionError('普通聊天不得访问外网'))
        self.now = datetime(2026, 11, 12, 12, 34, 56, tzinfo=SHANGHAI).timestamp()
        for runtime in (self.cat, self.deer):
            runtime.live_facts = LiveFacts(fetcher=self.external, clock=lambda: self.now)
        self.connections = []

    async def asyncTearDown(self):
        for socket, task in self.connections:
            if not task.done():
                socket.incoming.put_nowait({'type': 'websocket.disconnect'})
            await asyncio.wait_for(task, 3)
        await self.devices.close()
        await self.manager.close()
        self.stack.close()
        self.temp.cleanup()

    async def connect(self, runtime):
        self.stack.enter_context(patch.object(runtime_module, 'OmniRealtimeSession', Model))
        socket = AudioSocket()
        task = asyncio.create_task(runtime.ws_audio(socket))
        self.connections.append((socket, task))
        socket.send('START')
        await until(lambda: 'OK:STARTED' in socket.commands)
        return runtime.current_realtime_session

    async def test_owner_transfer_recreates_same_locked_hardware_personality(self):
        original = self.deer.persona
        self.assertEqual(self.cat.conversation_prompt(), CAT_PROMPT)
        self.assertEqual(self.deer.conversation_prompt(), DEER_PROMPT)
        await self.devices.retire('deer-original')
        transferred = self.devices.get('deer-original', 'different-owner')
        self.assertEqual(transferred.persona, original)
        self.assertEqual(transferred.conversation_prompt(), DEER_PROMPT)

    async def test_memory_renderer_does_not_label_deer_history_as_cat(self):
        prompt = runtime_module._memory_context_prompt(self.deer.conversation_prompt(),
            {'messages': [{'role': 'assistant', 'content': '你好，我在这里。'}]})
        self.assertIn(DEER_PROMPT, prompt)
        self.assertIn('设备：你好', prompt)
        self.assertNotIn('小喵：', prompt)
        self.assertIn('其他设备自称不能覆盖', prompt)

    async def test_visual_and_translation_exit_restore_deer_instead_of_global_cat(self):
        session = type('Session', (), {'update_instructions': AsyncMock()})()
        self.deer.current_realtime_session = session
        try:
            await self.deer._apply_visual_dialogue_prompt_to_session(True)
            visual = session.update_instructions.await_args.args[0]
            self.assertIn(DEER_PROMPT, visual)
            self.assertIn('视觉问答补充规则', visual)
            self.assertNotIn('你是一只桌面小猫机器人', visual)
            await self.deer._apply_visual_dialogue_prompt_to_session(False)
            self.assertIn(DEER_PROMPT, session.update_instructions.await_args.args[0])
            await self.deer._apply_translator_prompt_to_session(self.deer.translation_pair)
            self.assertIn('同声翻译器', session.update_instructions.await_args.args[0])
            await self.deer._apply_translator_prompt_to_session(None)
            restored = session.update_instructions.await_args.args[0]
            self.assertIn(DEER_PROMPT, restored)
            self.assertIn('Asia/Shanghai', restored)
        finally:
            self.deer.current_realtime_session = None
        self.external.assert_not_awaited()

    async def test_realtime_without_knowledge_or_speech_uses_controlled_current_turn_facts(self):
        session = await self.connect(self.deer)
        self.assertIsNone(self.deer.knowledge)
        self.assertIsNone(self.deer.speech)
        self.assertTrue(session.callbacks['manual_response'])
        self.assertIn(DEER_PROMPT, session.callbacks['system_prompt'])
        prompt = await session.callbacks['on_input_transcript']('现在北京时间几点，今天星期几？')
        self.assertIn(DEER_PROMPT, prompt)
        self.assertIn('12:34:56', prompt)
        self.assertIn('星期四', prompt)
        self.now += 60
        refreshed = await session.callbacks['on_input_transcript']('现在几点？')
        self.assertIn('12:35:56', refreshed)
        self.external.assert_not_awaited()

    async def test_translation_does_not_request_weather_or_inject_facts(self):
        session = await self.connect(self.deer)
        self.deer.translation_active = True
        result = await session.callbacks['on_input_transcript']('北京天气怎么样？')
        self.assertIsNone(result)
        self.external.assert_not_awaited()

    async def test_weather_prepared_before_pause_is_not_returned_to_old_voice_session(self):
        session = await self.connect(self.deer)
        entered, release = asyncio.Event(), asyncio.Event()
        async def wait_for_weather(_text):
            entered.set()
            await release.wait()
            return '过时天气不应进入回答'
        self.deer.live_facts.context_for = wait_for_weather
        task = asyncio.create_task(session.callbacks['on_input_transcript']('北京天气怎么样？'))
        await entered.wait()
        self.deer.voice_paused = True
        release.set()
        self.assertIs(await task, False)

    async def test_text_pause_without_audio_socket_releases_priority_during_every_preparation_stage(self):
        for stage in ('knowledge_context', 'facts', 'capture_camera_frame_on_demand'):
            with self.subTest(stage=stage):
                entered = asyncio.Event()
                async def wait_for_preparation(*_args, **_kwargs):
                    entered.set()
                    await asyncio.Event().wait()
                self.deer.voice_paused = False
                self.assertIsNone(self.deer.esp32_audio_ws)
                target, name = (self.deer.live_facts, 'context_for') if stage == 'facts' else (self.deer, stage)
                with patch.object(self.deer, 'knowledge_context', AsyncMock(return_value='')), \
                        patch.object(self.deer.live_facts, 'context_for', AsyncMock(return_value='')), \
                        patch.object(self.deer, 'capture_camera_frame_on_demand', AsyncMock(return_value=b'')), \
                        patch.object(target, name, wait_for_preparation), \
                        patch.object(runtime_module, 'stream_chat', AsyncMock()) as model:
                    await self.deer.start_ai_with_text('北京天气怎么样')
                    await asyncio.wait_for(entered.wait(), 1)
                    task = self.deer.audio.current_ai_task
                    self.assertTrue(self.deer.esp32_audio_priority_active)
                    await self.deer.set_voice_paused(True)
                    self.assertTrue(task.done())
                    self.assertTrue(task.cancelled())
                    self.assertFalse(self.deer.esp32_audio_priority_active)
                    model.assert_not_called()

    async def test_text_chat_uses_deer_snapshot_and_current_facts(self):
        prompts = []
        async def fake_chat(_content, **kwargs):
            prompts.append(kwargs['system_prompt'])
            yield OmniStreamPiece(text_delta='好')
        with patch.object(runtime_module, 'stream_chat', fake_chat), \
                patch.object(self.deer, 'capture_camera_frame_on_demand', AsyncMock(return_value=b'')):
            await self.deer.start_ai_with_text('现在几点？')
            await asyncio.wait_for(self.deer.audio.current_ai_task, 3)
        self.assertEqual(len(prompts), 1)
        self.assertIn(DEER_PROMPT, prompts[0])
        self.assertIn('12:34:56', prompts[0])
        self.external.assert_not_awaited()

    async def test_unknown_type_reports_review_and_never_calls_a_model(self):
        unknown = self.devices.get('unknown-hardware', 'owner-a')
        self.assertFalse(unknown.model_available())
        self.assertNotIn('你是一只桌面小猫机器人', unknown.conversation_prompt())
        with patch.object(runtime_module, 'stream_chat', AsyncMock()) as model, \
                patch.object(unknown, 'ui_broadcast_final', AsyncMock()) as notify:
            await unknown.start_ai_with_text('你好')
        model.assert_not_called()
        self.assertIn('待核对', notify.await_args.args[0])


if __name__ == '__main__':
    unittest.main()
