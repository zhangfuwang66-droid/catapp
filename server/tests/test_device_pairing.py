"""设备凭证、一次性配对码与两个账号的绑定隔离。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from fastapi.testclient import TestClient

from support import server_app
from access_control import AccessSettings, SESSION_COOKIE
from identity_store import IdentityStore, RateLimited, token_digest
from memory_store import MemoryStore, SummaryResult
from tools.identity_admin import main as admin_main


async def summarize(messages):
    return SummaryResult(summary="模拟摘要")


class PairingStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pairing-store-")
        self.path = Path(self.temp.name) / "memory.sqlite3"
        self.now = 1800000000.0
        self.store = IdentityStore(self.path, clock=lambda: self.now)
        self.alice = self.store.create_user("alice", "pairing-password-a")
        self.bob = self.store.create_user("bob", "pairing-password-b")
        self.credential = self.store.register_device("cat-a", "模拟小猫 A")

    def tearDown(self):
        self.temp.cleanup()

    def test_credentials_are_scoped_hashed_and_pairing_is_once_only(self):
        self.assertIsNone(self.store.credential_device("cat-b", self.credential.token))
        self.assertIsNotNone(self.store.credential_device("cat-a", self.credential.token))
        self.assertNotIn(self.credential.token, repr(self.credential))
        grant = self.store.request_pairing("cat-a", self.credential.token)
        self.assertNotIn(grant.code, repr(grant))
        bound = self.store.claim_pairing(grant.code.lower(), self.alice.id, client_key="local")
        self.assertEqual(bound["owner_user_id"], self.alice.id)
        self.assertIsNone(self.store.owned_device("cat-a", self.bob.id))
        self.assertEqual(self.store.list_devices(self.bob.id), [])
        with self.assertRaises(ValueError):
            self.store.claim_pairing(grant.code, self.bob.id, client_key="local")
        self.assertNotIn(self.credential.token.encode(), self.path.read_bytes())
        self.assertNotIn(grant.code.replace("-", "").encode(), self.path.read_bytes())

    def test_expiration_new_code_rotation_and_revocation(self):
        first = self.store.request_pairing("cat-a", self.credential.token)
        self.now += 31
        second = self.store.request_pairing("cat-a", self.credential.token)
        with self.assertRaises(ValueError):
            self.store.claim_pairing(first.code, self.alice.id, client_key="local")
        self.now += 301
        with self.assertRaises(ValueError):
            self.store.claim_pairing(second.code, self.alice.id, client_key="local")
        third = self.store.request_pairing("cat-a", self.credential.token)
        rotated = self.store.rotate_device_credential("cat-a")
        self.assertIsNone(self.store.credential_device("cat-a", self.credential.token))
        self.assertIsNotNone(self.store.credential_device("cat-a", rotated.token))
        with self.assertRaises(ValueError):
            self.store.claim_pairing(third.code, self.alice.id, client_key="local")
        self.store.revoke_device("cat-a")
        self.assertIsNone(self.store.credential_device("cat-a", rotated.token))

    def test_parallel_claims_from_independent_connections_have_one_winner(self):
        grant = self.store.request_pairing("cat-a", self.credential.token)
        other = IdentityStore(self.path, clock=lambda: self.now)
        barrier = threading.Barrier(2)

        def claim(store, user):
            barrier.wait(timeout=5)
            try:
                return store.claim_pairing(grant.code, user.id, client_key=user.id)["owner_user_id"]
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = (pool.submit(claim, self.store, self.alice), pool.submit(claim, other, self.bob))
            result = [task.result(timeout=5) for task in tasks]
        self.assertEqual(sum(item is not None for item in result), 1)

    def test_pair_request_and_guess_rate_limits(self):
        self.store.request_pairing("cat-a", self.credential.token)
        with self.assertRaises(RateLimited):
            self.store.request_pairing("cat-a", self.credential.token)
        for _ in range(10):
            with self.assertRaises(ValueError):
                self.store.claim_pairing("invalid", self.alice.id, client_key="local")
        with self.assertRaises(RateLimited):
            self.store.claim_pairing("invalid", self.alice.id, client_key="local")

    def test_admin_keeps_device_token_in_private_file_and_refuses_overwrite(self):
        output = io.StringIO()
        private = Path(self.temp.name) / "cat-b.secret.json"
        arguments = ["--database", str(self.path), "device-add", "--device-id", "cat-b", "--credential-output", str(private)]
        with redirect_stdout(output):
            self.assertEqual(admin_main(arguments), 0)
        token = json.loads(private.read_text(encoding="utf-8"))["device_token"]
        self.assertNotIn(token, output.getvalue())
        original = private.read_bytes()
        with redirect_stderr(io.StringIO()):
            self.assertEqual(admin_main(arguments), 1)
        self.assertEqual(private.read_bytes(), original)
        self.assertIsNotNone(self.store.credential_device("cat-b", token))


class PairingRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pairing-routes-")
        self.app = server_app.create_app(store=MemoryStore(Path(self.temp.name) / "memory.sqlite3"),
                                         summarizer=summarize, access=AccessSettings("accounts"))
        self.store = self.app.state.identities
        self.alice = self.store.create_user("alice", "pairing-password-a")
        self.bob = self.store.create_user("bob", "pairing-password-b")
        self.credential = self.store.register_device("cat-a", "模拟小猫 A")
        self.client = TestClient(self.app, client=("127.0.0.1", 12345))
        self.client.__enter__()
        self.a = self.headers("alice", "pairing-password-a")
        self.client.cookies.clear()
        self.b = self.headers("bob", "pairing-password-b")

    def headers(self, username, password):
        result = self.client.post("/api/auth/login", json={"username": username, "password": password},
                                  headers={"Origin": "http://testserver"})
        return {"Cookie": f"{SESSION_COOKIE}={self.client.cookies.get(SESSION_COOKIE)}",
                "Origin": "http://testserver", "X-CSRF-Token": result.json()["csrf_token"]}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def test_pairing_uses_session_owner_and_limits_visibility(self):
        device_headers = {"Authorization": "Bearer " + self.credential.token}
        result = self.client.post("/api/devices/cat-a/pairing", headers=device_headers)
        self.assertEqual(result.status_code, 200)
        self.assertIn("no-store", result.headers["cache-control"])
        self.assertGreater(result.json()["expires_in"], 0)
        self.assertLessEqual(result.json()["expires_in"], 300)
        code = result.json()["code"]
        pair = self.client.post("/api/devices/pair", json={"code": code, "user_id": self.bob.id}, headers=self.a)
        self.assertEqual(pair.status_code, 200)
        self.assertEqual(pair.json()["device"]["owner_user_id"], self.alice.id)
        self.assertEqual(len(self.client.get("/api/devices", headers=self.a).json()["devices"]), 1)
        self.assertEqual(self.client.get("/api/devices", headers=self.b).json()["devices"], [])
        self.assertEqual(self.client.post("/api/devices/pair", json={"code": code}, headers=self.b).status_code, 400)
        binding = self.client.get("/api/devices/cat-a/binding", headers=device_headers).json()
        self.assertTrue(binding["bound"])
        self.assertNotIn("owner_user_id", binding)
        self.assertEqual(self.client.post("/api/devices/cat-a/revoke", headers=self.b).status_code, 404)
        self.assertEqual(self.client.post("/api/devices/cat-a/revoke", headers=self.a).status_code, 200)
        self.assertEqual(self.client.get("/api/devices/cat-a/binding", headers=device_headers).status_code, 401)

    def test_credentials_are_required_and_not_accepted_from_query(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.post("/api/devices/cat-a/pairing").status_code, 401)
        self.assertEqual(self.client.post("/api/devices/cat-a/pairing", params={"token": self.credential.token}).status_code, 401)
        self.assertEqual(self.client.post("/api/devices/cat-b/pairing", headers={"Authorization": "Bearer " + self.credential.token}).status_code, 401)
        self.assertEqual(self.client.post("/api/devices/pair", json={"code": "ABCD-EFGH"}).status_code, 401)

    def test_pairing_lifetime_uses_server_clock(self):
        from unittest.mock import patch
        from identity_store import PairingGrant

        with patch.object(self.store, "clock", return_value=1800000000), \
             patch.object(self.store, "request_pairing", return_value=PairingGrant("cat-a", "ABCD-EFGH", 1800000123)):
            result = self.client.post("/api/devices/cat-a/pairing",
                                      headers={"Authorization": "Bearer " + self.credential.token})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["expires_in"], 123)


if __name__ == "__main__":
    unittest.main()
