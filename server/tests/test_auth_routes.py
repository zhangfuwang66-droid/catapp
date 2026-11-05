"""登录、同源/CSRF、会话撤销及本地兼容边界的 HTTP 集成测试。"""

from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from support import server_app
from access_control import AccessSettings, SESSION_COOKIE, is_local_client
from memory_store import MemoryStore, SummaryResult


async def summarize(messages):
    return SummaryResult(summary="模拟摘要")


class AuthRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="auth-routes-")
        self.app = server_app.create_app(
            store=MemoryStore(Path(self.temp.name) / "memory.sqlite3"), summarizer=summarize,
            access=AccessSettings("accounts", "https://cat.example"),
        )
        self.app.state.identities.create_user("alice", "test-login-password")
        self.client = TestClient(self.app, base_url="https://cat.example", client=("127.0.0.1", 12345))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def login(self):
        return self.client.post("/api/auth/login", json={"username": "alice", "password": "test-login-password"},
                                headers={"Origin": "https://cat.example"})

    def test_login_cookie_csrf_logout_and_replayed_session(self):
        result = self.login()
        self.assertEqual(result.status_code, 200)
        cookie = result.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=strict", cookie)
        token = self.client.cookies.get(SESSION_COOKIE)
        csrf = result.json()["csrf_token"]
        self.assertEqual(self.client.get("/api/auth/session").json()["user"]["username"], "alice")
        self.assertEqual(self.client.post("/api/auth/logout", headers={"Origin": "https://cat.example"}).status_code, 403)
        logout = self.client.post("/api/auth/logout", headers={"Origin": "https://cat.example", "X-CSRF-Token": csrf})
        self.assertEqual(logout.status_code, 200)
        self.assertIsNone(self.app.state.identities.session_user(token))
        self.assertEqual(self.client.get("/api/auth/session").status_code, 401)

    def test_cross_origin_login_and_write_are_rejected(self):
        bad = self.client.post("/api/auth/login", json={"username": "alice", "password": "test-login-password"},
                               headers={"Origin": "https://other.example"})
        self.assertEqual(bad.status_code, 403)
        self.assertEqual(self.client.post("/api/auth/login", json={"username": "alice", "password": "test-login-password"}).status_code, 403)
        csrf = self.login().json()["csrf_token"]
        self.assertEqual(self.client.post("/api/auth/logout", headers={"Origin": "https://other.example", "X-CSRF-Token": csrf}).status_code, 403)

    def test_validation_never_echoes_password_and_new_login_revokes_previous(self):
        secret = "sensitive-input-" * 20
        invalid = self.client.post("/api/auth/login", json={"username": "alice", "password": secret}, headers={"Origin": "https://cat.example"})
        self.assertEqual(invalid.status_code, 422)
        self.assertNotIn(secret, invalid.text)
        self.login()
        old = self.client.cookies.get(SESSION_COOKIE)
        self.login()
        self.assertIsNone(self.app.state.identities.session_user(old))
        self.assertEqual(self.client.get("/api/memory").status_code, 404)

    def test_local_mode_rejects_public_client(self):
        application = server_app.create_app(
            store=MemoryStore(Path(self.temp.name) / "local.sqlite3"), summarizer=summarize,
            access=AccessSettings("local"),
        )
        with TestClient(application, client=("8.8.8.8", 12345)) as public:
            self.assertEqual(public.get("/api/memory").status_code, 403)
        self.assertTrue(is_local_client("192.168.2.105"))
        self.assertTrue(is_local_client("::ffff:127.0.0.1"))
        self.assertFalse(is_local_client("8.8.8.8"))


if __name__ == "__main__":
    unittest.main()
