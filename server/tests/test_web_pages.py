"""账号入口、设备控制页权限及本地兼容网页。"""

from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from support import server_app
from access_control import AccessSettings
from memory_store import MemoryStore, SummaryResult


async def summarize(messages):
    return SummaryResult(summary="模拟摘要")


class WebPageTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.path = Path(directory)
        self.app = server_app.create_app(store=MemoryStore(self.path / "memory.sqlite3"), summarizer=summarize,
                                         access=AccessSettings("accounts"))
        identities = self.app.state.identities
        for name in ("alice", "bob"):
            user = identities.create_user(name, name + "-test-password")
            credential = identities.register_device("cat-" + name, name)
            code = identities.request_pairing(credential.device_id, credential.token)
            identities.claim_pairing(code.code, user.id, client_key="local")
        self.client = self.stack.enter_context(TestClient(self.app, client=("127.0.0.1", 12345)))

    def test_portal_is_public_but_console_checks_login_and_owner(self):
        portal = self.client.get("/")
        self.assertEqual(portal.status_code, 200)
        self.assertIn('id="loginForm"', portal.text)
        self.assertNotIn("cat-alice", portal.text)
        self.assertEqual(self.client.get("/console/cat-alice", follow_redirects=False).status_code, 303)
        self.client.post("/api/auth/login", headers={"Origin":"http://testserver"},
                         json={"username":"alice", "password":"alice-test-password"})
        console = self.client.get("/console/cat-alice")
        self.assertEqual(console.status_code, 200)
        self.assertIn('"device_id": "cat-alice"', console.text)
        self.assertNotIn("__CYBERMIAO_", console.text)
        self.assertIn('class="auth-pending"', console.text)
        self.assertEqual(self.client.get("/console/cat-bob").status_code, 404)
        self.app.state.identities.revoke_device("cat-alice")
        self.assertEqual(self.client.get("/console/cat-alice").status_code, 404)

    def test_local_entry_keeps_console_and_resources_are_available(self):
        app = server_app.create_app(store=MemoryStore(self.path / "local.sqlite3"), summarizer=summarize,
                                    access=AccessSettings("local"))
        with TestClient(app, client=("127.0.0.1", 12345)) as client:
            console = client.get("/")
            self.assertIn('"mode": "local"', console.text)
            self.assertNotIn("__CYBERMIAO_", console.text)
            for resource in ("portal.css", "portal.js", "console_access.js"):
                response = client.get("/web/" + resource)
                self.assertEqual(response.status_code, 200)
                self.assertIn("no-store", response.headers["cache-control"])
            self.assertEqual(client.get("/web/../app.py").status_code, 404)
            self.assertEqual(client.get("/console/cat-alice", follow_redirects=False).status_code, 303)


if __name__ == "__main__":
    unittest.main()
