"""真实账号身份下的上下文加载、主动清除及服务重启恢复。"""

import asyncio
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from support import server_app
from access_control import AccessSettings, SESSION_COOKIE
from identity_store import csrf_for_session
from memory_store import MemoryStore, SummaryResult
from test_device_runtime import FakeRealtimeSession
from test_realtime_knowledge import until
import device_runtime


async def summarize(messages):
    return SummaryResult(summary="模拟摘要：" + str(messages[0]["content"]), open_topics=("可以继续的模拟话题",))


class MemorySession(FakeRealtimeSession):
    instances = []

    def __init__(self, **callbacks):
        super().__init__(**callbacks)
        self.instances.append(self)


class AccountMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="account-memory-")
        self.path = Path(self.temp.name) / "memory.sqlite3"
        self.store = MemoryStore(self.path)
        self.stack = ExitStack()
        self.open_app()
        self.users, self.sessions, self.devices = {}, {}, {}
        for name in ("alice", "bob"):
            identities = self.app.state.identities
            self.users[name] = identities.create_user(name, name + "-test-password")
            self.app.state.provider_settings.save(self.users[name].id, "simulated-" + name + "-key")
            session = identities.login(name, name + "-test-password", client_key="local")
            self.sessions[name] = {"Cookie": f"{SESSION_COOKIE}={session.token}", "Origin": "http://testserver",
                                   "X-CSRF-Token": csrf_for_session(session.token)}
            credential = identities.register_device("cat-" + name, name)
            pairing = identities.request_pairing(credential.device_id, credential.token)
            identities.claim_pairing(pairing.code, self.users[name].id, client_key="local")
            self.devices[name] = {"Authorization": "Bearer " + credential.token}
        MemorySession.instances.clear()

    def open_app(self):
        self.stack.enter_context(patch.object(device_runtime, "API_KEY", "simulated-model-only"))
        self.stack.enter_context(patch.object(device_runtime, "OmniRealtimeSession", MemorySession))
        self.app = server_app.create_app(store=self.store, summarizer=summarize, access=AccessSettings("accounts"))
        self.client = self.stack.enter_context(TestClient(self.app, client=("127.0.0.1", 12345)))

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()

    def seed(self, name, text):
        conversation = self.store.create_conversation(self.users[name].id, "cat-" + name)
        self.store.append_message(conversation, "user", text)
        return conversation

    def audio(self, name):
        return self.stack.enter_context(self.client.websocket_connect(
            "/ws/devices/cat-" + name + "/audio", headers=self.devices[name]))

    def test_realtime_reload_uses_bound_owner_and_open_topics(self):
        ca = self.seed("alice", "甲的私有记忆")
        self.seed("bob", "乙的私有记忆")
        self.store.save_summary(ca, self.store.pending_messages(ca)[-1]["id"], "甲的私有摘要", open_topics=["甲的待续话题"])
        legacy = self.store.create_conversation("test-user-001", "test-cat-001")
        self.store.append_message(legacy, "user", "旧测试身份内容")
        audio = self.audio("alice")
        audio.send_text("START")
        self.assertEqual(audio.receive_text(), "OK:STARTED")
        first = MemorySession.instances[-1].callbacks["system_prompt"]
        self.assertIn("甲的私有摘要", first)
        self.assertIn("甲的待续话题", first)
        self.assertNotIn("乙的私有记忆", first)
        self.assertNotIn("旧测试身份内容", first)
        self.seed("alice", "重建会话前的新消息")
        audio.send_text("START")
        self.assertEqual(audio.receive_text(), "OK:STARTED")
        self.assertIn("重建会话前的新消息", MemorySession.instances[-1].callbacks["system_prompt"])
        with patch.object(server_app, "MEMORY_DEBUG_API_ENABLED", False):
            result = self.client.get("/api/devices/cat-alice/memory", headers=self.sessions["alice"])
            self.assertEqual(result.status_code, 200)

    def test_memory_clear_requires_confirmation_ends_sessions_and_preserves_other_owner(self):
        self.seed("alice", "甲将被清除的消息")
        self.seed("bob", "乙应当保留的消息")
        audio = self.audio("alice")
        url = "/api/devices/cat-alice/memory"
        self.assertEqual(self.client.delete(url, headers=self.sessions["alice"]).status_code, 400)
        wrong = self.client.request("DELETE", url, headers=self.sessions["alice"], json={"confirm_device_id": "cat-bob"})
        self.assertEqual(wrong.status_code, 400)
        cleared = self.client.request("DELETE", url, headers=self.sessions["alice"], json={"confirm_device_id": "cat-alice"})
        self.assertEqual(cleared.status_code, 200)
        with self.assertRaises(WebSocketDisconnect) as closed:
            audio.receive_text()
        self.assertEqual(closed.exception.code, 1012)
        context = self.client.get(url, headers=self.sessions["alice"]).json()
        self.assertEqual(context["recent_messages"], [])
        self.assertEqual(context["summaries"], [])
        other = self.client.get("/api/devices/cat-bob/memory", headers=self.sessions["bob"]).json()
        self.assertEqual(other["recent_messages"][0]["content"], "乙应当保留的消息")
        replacement = self.audio("alice")
        replacement.send_text("START")
        self.assertEqual(replacement.receive_text(), "OK:STARTED")
        self.assertNotIn("甲将被清除的消息", MemorySession.instances[-1].callbacks["system_prompt"])

    def test_audio_callbacks_summary_and_context_survive_restart(self):
        audio = self.audio("alice")
        audio.send_text("START")
        self.assertEqual(audio.receive_text(), "OK:STARTED")
        audio.send_bytes("重启前聊到的模拟话题".encode())
        for expected in ("STOP", "TTS_START", "START"):
            self.assertEqual(audio.receive_text(), expected)
        # 先完成设备的关闭握手，再重启服务；避免 TestClient 提前强制取消会话。
        audio.send_text("STOP")
        for _ in range(5):
            if audio.receive_text() == "OK:STOPPED":
                break
        else:
            self.fail("设备会话未确认停止")
        audio.close()
        for _ in range(5):
            if audio.receive()["type"] == "websocket.close":
                break
        else:
            self.fail("设备连接未完成关闭握手")
        self.stack.close()
        self.stack = ExitStack()
        self.store = MemoryStore(self.path)
        self.open_app()
        replacement = self.audio("alice")
        replacement.send_text("START")
        self.assertEqual(replacement.receive_text(), "OK:STARTED")
        self.assertIn("重启前聊到的模拟话题", MemorySession.instances[-1].callbacks["system_prompt"])
        self.assertNotIn("重启前聊到的模拟话题", self.client.get(
            "/api/devices/cat-bob/memory", headers=self.sessions["bob"]).text)

    def test_corrected_transcript_precedes_retrieval_and_memory_and_ambiguous_dates_are_not_saved(self):
        self.app.state.speech.refine = AsyncMock(return_value={'original':'欧诶怎么请假','text':'OA怎么请假','candidate':'OA怎么请假',
            'changed':True,'needs_confirmation':False,'source':'fast'})
        audio=self.audio('alice'); audio.send_text('START')
        self.assertEqual(audio.receive_text(),'OK:STARTED')
        model=MemorySession.instances[-1]
        runtime=self.app.state.devices.devices['cat-alice']
        with patch.object(runtime,'knowledge_context',AsyncMock(return_value='')) as lookup:
            prompt=self.client.portal.call(model.callbacks['on_input_transcript'],'欧诶怎么请假')
            lookup.assert_awaited_once_with('OA怎么请假')
            self.assertIn('OA怎么请假',prompt)
        context=self.store.context_for(self.users['alice'].id,'cat-alice')
        self.assertEqual(context['messages'][-1]['content'],'OA怎么请假')
        self.app.state.speech.refine.return_value={'original':'周四请假','text':'周四请假','candidate':'周三请假',
            'changed':True,'needs_confirmation':True,'source':'accurate'}
        with patch.object(runtime,'knowledge_context',AsyncMock()) as lookup:
            prompt=self.client.portal.call(model.callbacks['on_input_transcript'],'周四请假')
            lookup.assert_not_awaited()
            self.assertIn('只用一句话请用户确认',prompt)
        self.client.portal.call(model.callbacks['on_output_text_delta'],'请确认日期')
        self.client.portal.call(model.callbacks['on_response_done'])
        context=self.store.context_for(self.users['alice'].id,'cat-alice')
        self.assertNotIn('周四请假',str(context)); self.assertNotIn('周三请假',str(context))

    def test_realtime_emotion_uses_current_account_key(self):
        audio = self.audio('alice')
        audio.send_text('START')
        self.assertEqual(audio.receive_text(),'OK:STARTED')
        model = MemorySession.instances[-1]
        with patch.object(device_runtime,'analyze_emotion',return_value='happy') as emotion:
            self.client.portal.call(model.callbacks['on_output_text_delta'],'这是一段足够长的模拟回答')
            self.client.portal.call(until,lambda: emotion.called)
            emotion.assert_called_once_with('这是一段足够长的模拟回答','simulated-alice-key')


if __name__ == "__main__":
    unittest.main()
