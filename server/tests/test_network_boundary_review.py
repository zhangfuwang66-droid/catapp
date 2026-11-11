"""Independent real-app boundary checks with temporary storage and simulated runtimes."""

import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from support import server_app
from access_control import AccessSettings, SESSION_COOKIE
from identity_store import csrf_for_session
from memory_store import MemoryStore
from network_security import NetworkLimits


class NetworkBoundaryReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="network-boundary-review-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def application(self, mode="accounts", origin="https://198.51.100.27:8443", **limits):
        with patch.dict(os.environ, {"CYBERMIAO_SECRET_KEY_FILE": str(self.root / "provider.key")}):
            return server_app.create_app(store=MemoryStore(self.root / "memory.sqlite3"),
                access=AccessSettings(mode, origin), network_limits=NetworkLimits(**limits))

    def client(self, app, origin, peer="127.0.0.1"):
        client = TestClient(app, base_url=origin, client=(peer, 12345), raise_server_exceptions=False)
        self.addCleanup(client.close)
        return client

    def login_headers(self, app, origin):
        ids = app.state.identities
        user = ids.create_user("network-review", "simulated-network-review-password")
        grant = ids.login(user.username, "simulated-network-review-password", client_key="review")
        return user, {"Cookie": f"{SESSION_COOKIE}={grant.token}", "Origin": origin,
                      "X-CSRF-Token": csrf_for_session(grant.token)}

    def test_local_attack_attempts_cannot_reach_mutators_and_valid_post_still_works(self):
        origin = "http://127.0.0.1:8081"
        app = self.application("local", "")
        runtime = SimpleNamespace(servo_control=AsyncMock(return_value={"ok": True}))
        app.dependency_overrides[server_app.get_device_runtime] = lambda: runtime
        client = self.client(app, origin)
        self.assertEqual(client.get("/api/health", headers={"Host": "rebinding.example"}).status_code, 400)
        for headers in ({"Origin": "https://attacker.example"},
                        {"Origin": "null", "X-CyberMiao-Request": "1"},
                        {"Sec-Fetch-Site": "same-site", "X-CyberMiao-Request": "1"}, {}):
            self.assertEqual(client.post("/servo?angle=31", headers=headers).status_code, 403)
        self.assertEqual(client.get("/servo?angle=31").status_code, 405)
        self.assertEqual(client.get("/screen_mode?mode=1").status_code, 405)
        runtime.servo_control.assert_not_called()
        response = client.post("/servo?angle=31", headers={"Origin": origin})
        self.assertEqual(response.status_code, 200, response.text)
        runtime.servo_control.assert_awaited_once_with(ch=0, angle=31)

    def test_cross_site_navigation_exception_does_not_allow_private_api_reads(self):
        app = self.application("local", "")
        client = self.client(app, "http://localhost:8081")
        headers = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"}
        self.assertEqual(client.get("/", headers=headers).status_code, 200)
        self.assertEqual(client.get("/api/health", headers=headers).status_code, 403)
        self.assertEqual(client.get("/api/health", headers={"Host": "localhost:8081"}).status_code, 200)

    def test_public_ipv4_port_docs_and_forwarded_host_boundary(self):
        origin = "https://198.51.100.27:8443"
        app = self.application(origin=origin)
        client = self.client(app, origin, peer="203.0.113.9")
        self.assertEqual(client.get("/api/health").status_code, 200)
        for path in ("/docs", "/redoc", "/openapi.json"):
            response = client.get(path)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.headers.get("cache-control"), "no-store")
            self.assertEqual(response.headers.get("x-content-type-options"), "nosniff")
        for host in ("attacker.example", "198.51.100.27", "127.0.0.1:8081"):
            response = client.get("/api/health", headers={"Host": host,
                "X-Forwarded-Host": "198.51.100.27:8443", "X-Forwarded-For": "127.0.0.1"})
            self.assertEqual(response.status_code, 400)
        local = self.client(app, "http://127.0.0.1:8081")
        self.assertEqual(local.get("/api/health").status_code, 200)
        self.assertEqual(local.get("/api/auth/session").status_code, 400)

    def test_streaming_upload_auth_precedes_body_and_overflow_releases_upload_slot(self):
        origin = "https://198.51.100.27:8443"
        app = self.application(origin=origin, upload_bytes=8)
        client = self.client(app, origin)
        consumed = []
        def chunks():
            consumed.append(True)
            yield b"abcde"
            yield b"fghi"
        response = client.post("/api/knowledge/documents", content=chunks(), headers={"X-File-Name": "file.txt"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(consumed, [])
        user, headers = self.login_headers(app, origin)
        response = client.post("/api/knowledge/documents", content=chunks(), headers={**headers, "X-File-Name": "file.txt"})
        self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(app.state.knowledge.store.list_documents(user.id)["documents"], [])
        response = client.post("/api/knowledge/documents", content=b"abcdefgh", headers={**headers, "X-File-Name": "file.txt"})
        self.assertEqual(response.status_code, 201, response.text)

    def test_native_bearer_and_browser_origin_are_retained_with_ws_size_limits(self):
        origin = "https://198.51.100.27:8443"
        app = self.application(origin=origin, websocket_text_bytes=4, websocket_audio_bytes=8)
        user, headers = self.login_headers(app, origin)
        ids = app.state.identities
        credential = ids.register_device("cat-review", "simulated device")
        code = ids.request_pairing("cat-review", credential.token)
        ids.claim_pairing(code.code, user.id, client_key="review")

        async def echo(ws, _channel=None):
            await ws.accept()
            message = await ws.receive_text()
            await ws.send_text(message)
            await ws.close()
        async def run_socket(ws, handler):
            await handler(ws)
        fake = SimpleNamespace(audio_connection=SimpleNamespace(run=run_socket), ws_audio=echo,
                               serve_client=echo, ws_ui=set())
        client = self.client(app, origin)
        socket_origin = origin.replace("https://", "wss://")
        with patch.object(app.state.devices, "get", return_value=fake) as get_runtime:
            with self.assertRaises(WebSocketDisconnect) as denied:
                with client.websocket_connect(socket_origin + "/ws/devices/cat-review/audio"):
                    pass
            self.assertEqual(denied.exception.code, 4401)
            get_runtime.assert_not_called()
            bearer = {"Authorization": "Bearer " + credential.token}
            with client.websocket_connect(socket_origin + "/ws/devices/cat-review/audio", headers=bearer) as ws:
                ws.send_text("STOP")
                self.assertEqual(ws.receive_text(), "STOP")
            with client.websocket_connect(socket_origin + "/ws/devices/cat-review/audio", headers=bearer) as ws:
                ws.send_text("猫猫")
                with self.assertRaises(WebSocketDisconnect) as oversized:
                    ws.receive_text()
                self.assertEqual(oversized.exception.code, 1009)
            for source in (None, "https://attacker.example"):
                supplied = {"Cookie": headers["Cookie"]}
                if source:
                    supplied["Origin"] = source
                with self.assertRaises(WebSocketDisconnect) as denied:
                    with client.websocket_connect(socket_origin + "/ws/devices/cat-review/ui", headers=supplied):
                        pass
                self.assertEqual(denied.exception.code, 4403)
            with client.websocket_connect(socket_origin + "/ws/devices/cat-review/ui", headers=headers) as ws:
                ws.send_text("OK")
                self.assertEqual(ws.receive_text(), "OK")


if __name__ == "__main__":
    unittest.main()
