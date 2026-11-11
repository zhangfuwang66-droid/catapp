"""完整设备接口授权与长连接撤销，使用两个账号和两台模拟设备。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from support import server_app
from access_control import AccessSettings, SESSION_COOKIE
from identity_store import csrf_for_session
from memory_store import MemoryStore, SummaryResult
import device_runtime


async def summarize(messages):
    return SummaryResult(summary="模拟摘要")


class DeviceAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="device-auth-")
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(device_runtime, "HAND_TRACKING_ENABLED", False))
        self.stack.enter_context(patch.object(device_runtime, "COVER_INTERACTION_ENABLED", False))
        self.stack.enter_context(patch.object(device_runtime, "ENABLE_ESP32_SCREEN_PUSH", False))
        self.stack.enter_context(patch.object(device_runtime, "API_KEY", ""))
        self.store = MemoryStore(Path(self.temp.name) / "memory.sqlite3")
        self.app = server_app.create_app(store=self.store, summarizer=summarize, access=AccessSettings("accounts"))
        self.identities = self.app.state.identities
        self.credentials = {}
        self.users = {}
        self.headers = {}
        for name in ("alice", "bob"):
            user = self.identities.create_user(name, name + "-test-password")
            session = self.identities.login(name, name + "-test-password", client_key="local")
            device_id = "cat-" + name
            credential = self.identities.register_device(device_id, device_id)
            code = self.identities.request_pairing(device_id, credential.token)
            self.identities.claim_pairing(code.code, user.id, client_key="local")
            self.credentials[name] = {"Authorization": "Bearer " + credential.token}
            self.users[name] = user
            self.headers[name] = {"Cookie": f"{SESSION_COOKIE}={session.token}", "Origin": "http://testserver",
                                  "X-CSRF-Token": csrf_for_session(session.token)}
        self.client = self.stack.enter_context(TestClient(self.app, client=("127.0.0.1", 12345)))

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()

    def audio(self, name, headers=None):
        return self.stack.enter_context(self.client.websocket_connect(
            f"/ws/devices/cat-{name}/audio", headers=headers or self.credentials[name]))

    def test_voice_control_requires_owner_and_csrf_and_is_scoped_to_device(self):
        url = '/api/devices/cat-alice/voice'
        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.post(url, json={'paused': True}, headers=self.headers['bob']).status_code, 404)
        no_csrf = {key: value for key, value in self.headers['alice'].items() if key != 'X-CSRF-Token'}
        self.assertEqual(self.client.post(url, json={'paused': True}, headers=no_csrf).status_code, 403)
        response = self.client.post(url, json={'paused': True}, headers=self.headers['alice'])
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['paused'])
        self.assertTrue(self.client.get(url, headers=self.headers['alice']).json()['paused'])
        self.assertFalse(self.client.get('/api/devices/cat-bob/voice', headers=self.headers['bob']).json()['paused'])

    def camera(self, name):
        ws = self.stack.enter_context(self.client.websocket_connect(
            f"/ws/devices/cat-{name}/camera", headers=self.credentials[name]))
        self.assertTrue(ws.receive_text().startswith("SCRMODE:"))
        self.assertTrue(ws.receive_text().startswith("SET:FPS="))
        return ws

    def viewer(self, name):
        return self.stack.enter_context(self.client.websocket_connect(
            f"/ws/devices/cat-{name}/viewer", headers=self.headers[name]))

    def test_all_business_resources_reject_another_owner(self):
        resources = (
            ("GET", "/memory"), ("DELETE", "/memory"), ("GET", "/stream.wav"), ("GET", "/repeat.wav"),
            ("POST", "/servo"), ("POST", "/servo_raw"), ("POST", "/servo_raw_mode"),
            ("GET", "/screen_mode"), ("POST", "/screen_mode?mode=1"),
            ("GET", "/performance_mode"), ("POST", "/performance_mode?active=1"),
            ("POST", "/camera_setting"), ("POST", "/tft_setting"),
            ("POST", "/record/start"), ("POST", "/record/stop"), ("GET", "/record/state"),
            ("POST", "/record/ask"), ("POST", "/record/analyze"), ("GET", "/record/image/session/image.jpg"),
        )
        for method, suffix in resources:
            with self.subTest(method=method, path=suffix):
                response = self.client.request(method, "/api/devices/cat-alice" + suffix,
                                               json={"question": "模拟问题"}, headers=self.headers["bob"])
                self.assertEqual(response.status_code, 404, response.text)

    def test_legacy_anonymous_routes_are_closed_in_accounts_mode(self):
        for path in ("/api/memory", "/repeat.wav", "/stream.wav", "/screen_mode", "/record/state"):
            with self.subTest(registered_get=path):
                self.assertEqual(self.client.get(path, headers=self.headers["alice"]).status_code, 404)
        moved_writes = ("/servo?ch=3&angle=91", "/servo_raw?ch=3&angle=91", "/servo_raw_mode?active=0",
                        "/camera_setting?param=quality&value=12", "/tft_setting?param=brightness&value=1")
        with patch.object(self.app.state.devices, "get", side_effect=AssertionError("匿名旧接口不能创建 runtime")) as runtime_get:
            for path in moved_writes:
                with self.subTest(removed_get=path):
                    response = self.client.get(path, headers=self.headers["alice"])
                    self.assertEqual(response.status_code, 405, response.text)
                    self.assertIn("POST", response.headers["Allow"])
            for path in (*moved_writes, "/screen_mode?mode=0"):
                with self.subTest(correct_post=path):
                    response = self.client.post(path, headers=self.headers["alice"])
                    self.assertEqual(response.status_code, 404, response.text)
            runtime_get.assert_not_called()
        self.assertEqual(self.app.state.devices.devices, {})
        for path in ("/ws_ui", "/ws_audio", "/ws/camera", "/ws/viewer"):
            with self.assertRaises(WebSocketDisconnect):
                with self.client.websocket_connect(path, headers=self.headers["alice"]):
                    pass

    def test_hardware_and_browser_credentials_cannot_be_mixed(self):
        self.assertEqual(self.client.post("/api/devices/cat-alice/servo", headers=self.credentials["alice"]).status_code, 401)
        for path, headers in (
            ("/ws/devices/cat-alice/audio", self.headers["alice"]),
            ("/ws/devices/cat-bob/camera", self.credentials["alice"]),
            ("/ws/devices/cat-bob/viewer", self.headers["alice"]),
            ("/ws/devices/cat-alice/ui", {**self.headers["alice"], "Origin": "http://other.example"}),
        ):
            with self.assertRaises(WebSocketDisconnect):
                with self.client.websocket_connect(path, headers=headers):
                    pass

    def test_mutations_require_csrf_and_get_screen_is_readonly(self):
        headers = {key: value for key, value in self.headers["alice"].items() if key != "X-CSRF-Token"}
        self.assertEqual(self.client.post("/api/devices/cat-alice/servo", headers=headers).status_code, 403)
        self.assertEqual(self.client.get("/api/devices/cat-alice/servo", headers=self.headers["alice"]).status_code, 405)
        mode = self.client.get("/api/devices/cat-alice/screen_mode?mode=2", headers=self.headers["alice"])
        self.assertEqual(mode.json()["mode"], 0)
        self.assertEqual(mode.headers["cache-control"], "no-store")
        audio = self.audio("alice")
        result = self.client.post("/api/devices/cat-alice/servo?ch=3&angle=91", headers=self.headers["alice"])
        self.assertTrue(result.json()["ok"])
        self.assertEqual(audio.receive_text(), "SERVO:3,91")

    def test_logout_closes_only_that_login_and_keeps_hardware_online(self):
        audio = self.audio("alice")
        viewer_a = self.viewer("alice")
        camera_b, viewer_b = self.camera("bob"), self.viewer("bob")
        self.assertEqual(self.client.post("/api/auth/logout", headers=self.headers["alice"]).status_code, 200)
        with self.assertRaises(WebSocketDisconnect):
            viewer_a.receive_bytes()
        camera_b.send_bytes(b"bob-after-alice-logout")
        self.assertEqual(viewer_b.receive_bytes(), b"bob-after-alice-logout")
        audio.send_text("STOP")
        self.assertEqual(audio.receive_text(), "OK:STOPPED")
        self.assertEqual(self.client.get("/api/devices/cat-alice/memory", headers=self.headers["alice"]).status_code, 401)

    def test_device_revoke_closes_its_hardware_and_browser(self):
        audio_a, audio_b = self.audio("alice"), self.audio("bob")
        viewer_a = self.viewer("alice")
        result = self.client.post("/api/devices/cat-alice/revoke", headers=self.headers["alice"])
        self.assertEqual(result.status_code, 200)
        for ws in (audio_a, viewer_a):
            with self.assertRaises(WebSocketDisconnect):
                ws.receive_text()
        audio_b.send_text("STOP")
        self.assertEqual(audio_b.receive_text(), "OK:STOPPED")
        self.assertEqual(self.client.get("/api/devices/cat-alice/memory", headers=self.headers["alice"]).status_code, 404)

    def test_admin_credential_rotation_ends_old_connection(self):
        audio = self.audio("alice")
        rotated = self.identities.rotate_device_credential("cat-alice")
        with self.assertRaises(WebSocketDisconnect):
            audio.receive_text()
        replacement = self.audio("alice", {"Authorization": "Bearer " + rotated.token})
        replacement.send_text("STOP")
        self.assertEqual(replacement.receive_text(), "OK:STOPPED")

    def test_active_audio_stream_ends_when_device_is_revoked(self):
        async def wait_until_streaming():
            for _ in range(200):
                if self.app.state.connections.leases:
                    return
                await asyncio.sleep(0.01)
            raise TimeoutError("音频流未建立")

        with ThreadPoolExecutor(max_workers=1) as pool:
            stream = pool.submit(self.client.get, "/api/devices/cat-alice/stream.wav", headers=self.credentials["alice"])
            self.client.portal.call(wait_until_streaming)
            result = self.client.post("/api/devices/cat-alice/revoke", headers=self.headers["alice"])
            self.assertEqual(result.status_code, 200)
            response = stream.result(timeout=5)
            self.assertIn(response.status_code, {200, 401})
            self.assertFalse(self.app.state.connections.leases)


if __name__ == "__main__":
    unittest.main()
