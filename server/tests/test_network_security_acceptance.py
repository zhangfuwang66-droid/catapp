"""Independent network acceptance through the real HTTP and WebSocket routes.

Only databases, model credentials and hardware are isolated.  The application
routers and security middleware are never replaced by test routes or shims.
"""

from contextlib import ExitStack
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from access_control import AccessSettings, SESSION_COOKIE
from database_backups import BackupSettings
from identity_store import csrf_for_session
from memory_store import MemoryStore, SummaryResult
from support import server_app
import device_runtime


async def summarize(_messages):
    return SummaryResult(summary="simulated-network-acceptance")


class NetworkFixture(unittest.TestCase):
    mode = "local"
    origin = "http://127.0.0.1:8081"
    public_url = ""

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(
            tempfile.TemporaryDirectory(prefix="network-security-")))
        self.stack.enter_context(patch.dict(os.environ, {
            "CYBERMIAO_SECRET_KEY_FILE": str(self.root / "simulated-provider.key"),
            "CYBERMIAO_DATA_DIR": str(self.root),
            "PYTHON_DOTENV_DISABLED": "1",
        }))
        for name in ("HAND_TRACKING_ENABLED", "COVER_INTERACTION_ENABLED", "ENABLE_ESP32_SCREEN_PUSH"):
            self.stack.enter_context(patch.object(device_runtime, name, False))
        self.stack.enter_context(patch.object(device_runtime, "API_KEY", ""))
        self.stack.enter_context(patch.object(device_runtime, "RECORDS_DIR", str(self.root / "records")))
        self.stack.enter_context(patch.object(server_app, "RECORDS_DIR", str(self.root / "records")))
        self.stack.enter_context(patch("knowledge_service.KnowledgeService.start", new=AsyncMock()))
        self.app = server_app.create_app(
            store=MemoryStore(self.root / "network.sqlite3"), summarizer=summarize,
            access=AccessSettings(self.mode, self.public_url), backup_settings=BackupSettings())
        self.stack.enter_context(patch.object(
            self.app.state.providers, "key", side_effect=AssertionError("network checks must not read model keys")))
        self.client = self.stack.enter_context(TestClient(
            self.app, base_url=self.origin, client=("127.0.0.1", 23456),
            raise_server_exceptions=False))

    def assert_http_denied(self, response):
        self.assertIn(response.status_code, {400, 403}, response.text)
        self.assertNotIn("set-cookie", response.headers)

    def assert_websocket_denied(self, path, headers):
        with self.assertRaises(WebSocketDisconnect) as closed:
            with self.client.websocket_connect(path, headers=headers):
                self.fail("untrusted WebSocket handshake was accepted")
        self.assertIn(closed.exception.code, {1008, 4403})


class IndependentLocalNetworkSecurityTests(NetworkFixture):
    def test_loopback_host_remains_usable_without_browser_origin(self):
        for host in ("127.0.0.1:8081", "localhost:8081", "[::1]:8081"):
            with self.subTest(host=host):
                result = self.client.get("/api/health", headers={"Host": host})
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(result.text, "OK")

    def test_untrusted_dns_hosts_cannot_rebind_to_local_http(self):
        for host in ("attacker.example", "localhost.attacker.example", "127.0.0.1.attacker.example"):
            with self.subTest(host=host):
                self.assert_http_denied(self.client.get("/api/health", headers={"Host": host}))

    def test_forwarded_host_does_not_override_untrusted_direct_host(self):
        self.assert_http_denied(self.client.get("/api/health", headers={
            "Host": "attacker.example", "X-Forwarded-Host": "127.0.0.1:8081",
            "X-Forwarded-For": "127.0.0.1", "Forwarded": "host=127.0.0.1:8081;for=127.0.0.1"}))

    def test_cross_origin_json_cannot_change_local_voice_state(self):
        for path in ("/voice", "/api/devices/network-local/voice"):
            with self.subTest(path=path):
                before = self.client.get(path).json()["paused"]
                result = self.client.post(path, json={"paused": not before},
                                          headers={"Origin": "https://attacker.example"})
                after = self.client.get(path).json()["paused"]
                self.assertEqual(after, before, f"cross-origin {path} changed voice state; status={result.status_code}")
                self.assert_http_denied(result)

    def test_cross_site_fetch_metadata_cannot_change_local_voice_state(self):
        before = self.client.get("/voice").json()["paused"]
        result = self.client.post("/voice", json={"paused": not before}, headers={"Sec-Fetch-Site": "cross-site"})
        after = self.client.get("/voice").json()["paused"]
        self.assertEqual(after, before, f"cross-site fetch changed voice state; status={result.status_code}")
        self.assert_http_denied(result)

    def test_cross_site_legacy_get_mutations_are_rejected(self):
        paths = ("/servo?ch=3&angle=91", "/servo_raw?ch=3&angle=91", "/servo_raw_mode?active=0",
                 "/camera_setting?param=quality&value=12", "/tft_setting?param=brightness&value=1",
                 "/screen_mode?mode=0")
        for path in paths:
            with self.subTest(path=path):
                self.assert_http_denied(self.client.get(path, headers={
                    "Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site"}))

    def test_same_origin_local_write_remains_usable(self):
        before = self.client.get("/voice").json()["paused"]
        result = self.client.post("/voice", json={"paused": not before}, headers={"Origin": self.origin})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.client.get("/voice").json()["paused"], not before)

    def test_foreign_browser_origin_cannot_open_local_websocket(self):
        self.assert_websocket_denied("/ws/viewer", {"Origin": "https://attacker.example"})

    def test_untrusted_dns_host_cannot_open_local_websocket(self):
        self.assert_websocket_denied("/ws/viewer", {"Host": "attacker.example", "Origin": "http://attacker.example"})


class IndependentAccountsNetworkSecurityTests(NetworkFixture):
    mode = "accounts"
    origin = "https://network-security.example"
    public_url = origin
    password = "simulated-network-only-password"

    def setUp(self):
        super().setUp()
        self.user = self.app.state.identities.create_user("network-member", self.password)
        self.grant = self.app.state.identities.login(self.user.username, self.password, client_key="qa-fixture")
        self.headers = {"Cookie": f"{SESSION_COOKIE}={self.grant.token}", "Origin": self.origin,
                        "X-CSRF-Token": csrf_for_session(self.grant.token)}

    def test_configured_public_origin_does_not_allow_foreign_http_host(self):
        self.assert_http_denied(self.client.get("/api/health", headers={"Host": "attacker.example"}))

    def test_foreign_login_origin_and_cross_site_metadata_issue_no_session(self):
        for origin, fetch_site in (("https://attacker.example", "same-origin"), (self.origin, "cross-site"), ("null", "cross-site")):
            with self.subTest(origin=origin, fetch_site=fetch_site):
                result = self.client.post("/api/auth/login", json={"username": self.user.username, "password": self.password},
                                          headers={"Origin": origin, "Sec-Fetch-Site": fetch_site})
                self.assertEqual(result.status_code, 403, result.text)
                self.assertNotIn("set-cookie", result.headers)
                self.assertNotIn(self.password, result.text)

    def test_csrf_rejection_preserves_session_and_authorized_logout_revokes_it(self):
        missing = {key: value for key, value in self.headers.items() if key != "X-CSRF-Token"}
        result = self.client.post("/api/auth/logout", headers=missing)
        self.assertEqual(result.status_code, 403, result.text)
        self.assertIsNotNone(self.app.state.identities.session_user(self.grant.token))
        self.assertEqual(self.client.post("/api/auth/logout", headers=self.headers).status_code, 200)
        self.assertIsNone(self.app.state.identities.session_user(self.grant.token))
        self.assertEqual(self.client.get("/api/auth/session", headers=self.headers).status_code, 401)

    def test_validation_and_internal_failure_do_not_echo_secrets_or_database_path(self):
        sentinel = "network-secret-sentinel-" * 12
        for path in ("/api/auth/login", "/api/admin/auth/login"):
            with self.subTest(path=path):
                result = self.client.post(path, json={"username": self.user.username, "password": sentinel},
                                          headers={"Origin": self.origin})
                self.assertEqual(result.status_code, 422, result.text)
                self.assertNotIn(sentinel, result.text)
                self.assertNotIn(str(self.root), result.text)
        with patch.object(self.app.state.identities, "session_user", side_effect=RuntimeError(sentinel + str(self.root))):
            result = self.client.get("/api/auth/session", headers=self.headers)
        self.assertEqual(result.status_code, 500, result.text)
        self.assertNotIn(sentinel, result.text)
        self.assertNotIn(str(self.root), result.text)

    def test_static_traversal_and_windows_aliases_cannot_expose_protected_files(self):
        paths = ("/web/%2e%2e/app.py", "/web/%2e%2e/access_control.py", "/web/Admin/index.html",
                 "/web/admin%5cindex.html", "/web/admin./index.html", "/web/admin%20/index.html",
                 "/web/admin%2findex.html", "/web/admin/admin.js")
        for path in paths:
            with self.subTest(path=path):
                result = self.client.get(path, follow_redirects=False)
                self.assertIn(result.status_code, {401, 403, 404}, result.text[:300])
                self.assertNotIn("cybermiao_session =", result.text)
                self.assertNotIn(str(self.root), result.text)


if __name__ == "__main__":
    unittest.main()
