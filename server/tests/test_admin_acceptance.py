"""Black-box admin acceptance checks with an isolated database and simulated users."""

from contextlib import ExitStack, closing
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from fastapi.testclient import TestClient

from access_control import AccessSettings, SESSION_COOKIE
from identity_store import csrf_for_session
from memory_store import MemoryStore, SummaryResult
from provider_settings import ProviderSettings
from support import server_app
from tools.memory_database import backup_database, file_sha256, inspect_database, restore_database


async def summarize(_messages):
    return SummaryResult(summary="simulated")


class AdminAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="admin-acceptance-")
        self.stack = ExitStack()
        self.database = Path(self.temp.name) / "memory.sqlite3"
        self.app = server_app.create_app(
            store=MemoryStore(self.database), summarizer=summarize,
            access=AccessSettings("accounts", "https://cat.example"),
        )
        self.identities = self.app.state.identities
        self.users = {}
        self.headers = {}
        for name in ("owner", "alice", "bob"):
            self.users[name] = self.identities.create_user(name, name + "-test-password")
            session = self.identities.login(name, name + "-test-password", client_key=name)
            self.headers[name] = {
                "Cookie": f"{SESSION_COOKIE}={session.token}",
                "Origin": "https://cat.example",
                "X-CSRF-Token": csrf_for_session(session.token),
            }
        credential = self.identities.register_device("cat-alice", "Alice's cat")
        pairing = self.identities.request_pairing("cat-alice", credential.token)
        self.identities.claim_pairing(pairing.code, self.users["alice"].id, client_key="local")
        self.device_headers = {"Authorization": "Bearer " + credential.token}
        self.client = self.stack.enter_context(TestClient(
            self.app, base_url="https://cat.example", client=("127.0.0.1", 12345)))

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()

    def admin_cli(self, action, username):
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[1] / "admin_cli.py"),
             "--database", str(self.database), action, "--username", username],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def request(self, method, path, *, actor=None, headers=None, json=None):
        return self.client.request(method, path, json=json,
                                   follow_redirects=False,
                                   headers=headers if headers is not None else self.headers.get(actor, {}))

    def test_explicit_grant_revoke_and_all_read_boundaries(self):
        paths = ("/admin", "/api/admin/session", "/api/admin/overview", "/api/admin/users",
                 "/api/admin/devices", "/api/admin/storage", "/api/admin/backups", "/api/admin/audit")
        for path in paths:
            for actor, headers in (("anonymous", {}), ("ordinary", self.headers["owner"]),
                                   ("device", self.device_headers)):
                with self.subTest(path=path, actor=actor):
                    response = self.request("GET", path, headers=headers)
                    if path == '/admin' and actor in ('anonymous','device'):
                        self.assertEqual(response.status_code,303)
                        self.assertEqual(response.headers['location'],'/?notice=login')
                    else:
                        self.assertIn(response.status_code, (401, 403))
        self.admin_cli("grant", "owner")
        for path in paths:
            with self.subTest(path=path, actor="admin"):
                response = self.request("GET", path, actor="owner")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.headers.get("cache-control"), "no-store")
        self.admin_cli("grant", "alice")
        self.admin_cli("revoke", "owner")
        for path in paths:
            self.assertEqual(self.request("GET", path, actor="owner").status_code, 403)

    def test_write_origin_csrf_confirmation_and_session_revoke(self):
        self.admin_cli("grant", "owner")
        path = f"/api/admin/users/{self.users['bob'].id}/revoke-sessions"
        body = {"confirm_username": "bob"}
        self.assertEqual(self.request("POST", path, actor="alice", json=body).status_code, 403)
        self.assertEqual(self.request("POST", path, headers={**self.headers["owner"], "Origin": "https://evil.example"}, json=body).status_code, 403)
        self.assertEqual(self.request("POST", path, headers={k: v for k, v in self.headers["owner"].items() if k != "X-CSRF-Token"}, json=body).status_code, 403)
        self.assertEqual(self.request("POST", path, actor="owner", json={"confirm_username": "alice"}).status_code, 409)
        response = self.request("POST", path, actor="owner", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.request("GET", "/api/auth/session", actor="bob").status_code, 401)
        self.assertEqual(self.request("GET", "/api/auth/session", actor="alice").status_code, 200)

    def test_admin_self_lock_and_disabled_user(self):
        self.admin_cli("grant", "owner")
        own = f"/api/admin/users/{self.users['owner'].id}/status"
        response = self.request("POST", own, actor="owner", json={"disabled": True, "confirm_username": "owner"})
        self.assertEqual(response.status_code, 409, response.text)
        target = f"/api/admin/users/{self.users['alice'].id}/status"
        response = self.request("POST", target, actor="owner", json={"disabled": True, "confirm_username": "alice"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.request("GET", "/api/auth/session", actor="alice").status_code, 401)
        self.assertEqual(self.request("GET", "/api/devices/cat-alice/voice", actor="alice").status_code, 401)
        self.assertEqual(self.request("GET", "/api/admin/overview", actor="owner").status_code, 200)

    def test_read_is_aggregated_and_does_not_create_runtime(self):
        self.admin_cli("grant", "owner")
        secret = "private-memory-marker-acceptance"
        conversation = self.app.state.memory_manager.store.create_conversation(self.users["alice"].id, "cat-alice")
        self.app.state.memory_manager.store.append_message(conversation, "user", secret)
        self.assertEqual(self.app.state.devices.devices, {})
        for path in ("/api/admin/overview", "/api/admin/users", "/api/admin/devices", "/api/admin/storage", "/api/admin/backups", "/api/admin/audit"):
            response = self.request("GET", path, actor="owner")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn(secret, response.text)
            self.assertNotIn("password_hash", response.text)
            self.assertNotIn("token_hash", response.text)
            self.assertNotIn("ciphertext", response.text)
        self.assertEqual(self.app.state.devices.devices, {})
        self.assertTrue(inspect_database(self.database)["ok"])

    def test_query_bounds_and_search_are_validated(self):
        self.admin_cli("grant", "owner")
        for path in ("users", "devices", "audit"):
            for query in ("page=0", "page_size=0", "page_size=101", "q=" + "x" * 81):
                with self.subTest(path=path, query=query):
                    response = self.request("GET", f"/api/admin/{path}?{query}", actor="owner")
                    self.assertEqual(response.status_code, 422, response.text)
        response = self.request("GET", "/api/admin/users?q=%27%20OR%201%3D1--", actor="owner")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 0)

    def test_device_response_has_only_contract_fields(self):
        self.admin_cli("grant", "owner")
        response = self.request("GET", "/api/admin/devices", actor="owner")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(set(response.json()["items"][0]), {
            "id", "display_name", "device_type", "owner_user_id", "owner_username",
            "active_user_id", "active_username", "loan_active", "loan_pending",
            "loan_expires_at", "revoked", "created_at", "audio_online",
            "camera_online", "online", "voice_paused",
        })

    def test_inspect_rejects_missing_admin_role_table(self):
        self.admin_cli("grant", "owner")
        damaged = Path(self.temp.name) / "damaged.sqlite3"
        backup_database(self.database, damaged)
        with closing(sqlite3.connect(damaged)) as db:
            db.execute("DROP TABLE admin_roles")
            db.commit()
        with self.assertRaises(ValueError):
            inspect_database(damaged)

    def test_voice_pause_and_unbind_preserve_hardware_and_history(self):
        self.admin_cli("grant", "owner")
        secret = "historical-private-memory-marker"
        conversation = self.app.state.memory_manager.store.create_conversation(self.users["alice"].id, "cat-alice")
        self.app.state.memory_manager.store.append_message(conversation, "user", secret)
        path = "/api/admin/devices/cat-alice/voice"
        self.assertEqual(self.request("POST", path, actor="owner", json={"paused": True, "confirm_device_id": "wrong"}).status_code, 409)
        response = self.request("POST", path, actor="owner", json={"paused": True, "confirm_device_id": "cat-alice"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(self.identities.voice_preference(self.users["alice"].id, "cat-alice"))
        self.assertTrue(self.request("GET", "/api/devices/cat-alice/voice", actor="alice").json()["paused"])
        response = self.request("POST", "/api/admin/devices/cat-alice/unbind", actor="owner",
                                json={"confirm_device_id": "cat-alice"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.request("GET", "/api/devices/cat-alice/voice", actor="alice").status_code, 404)
        self.assertTrue(self.request("GET", "/api/devices/cat-alice/binding", headers=self.device_headers).json()["bound"] is False)
        self.assertIn(secret, self.request("GET", "/api/account/memories/cat-alice", actor="alice").text)
        self.assertTrue(inspect_database(self.database)["ok"])

    def test_backup_restore_preserves_admin_role_and_encrypted_key(self):
        self.admin_cli("grant", "owner")
        simulated_key = "sk-simulated-admin-acceptance"
        self.app.state.provider_settings.save(self.users["alice"].id, simulated_key)
        source = self.database
        backup = Path(self.temp.name) / "pre-restore.sqlite3"
        restored = Path(self.temp.name) / "restored.sqlite3"
        before = inspect_database(source)
        backup_database(source, backup)
        plan = restore_database(backup, restored, file_sha256(backup))
        self.assertFalse(plan["applied"])
        self.assertFalse(restored.exists())
        applied = restore_database(backup, restored, file_sha256(backup), apply=True, offline=True)
        self.assertTrue(applied["applied"])
        after = inspect_database(restored)
        self.assertEqual(before["schema_versions"], after["schema_versions"])
        self.assertEqual(before["identity_row_counts"]["auth_users"], after["identity_row_counts"]["auth_users"])
        self.assertEqual(before["identity_row_counts"]["devices"], after["identity_row_counts"]["devices"])
        self.assertEqual(ProviderSettings(restored).get(self.users["alice"].id), simulated_key)
        self.assertEqual(self.request("GET", "/api/admin/overview", actor="owner").status_code, 200)
        restored_app = server_app.create_app(
            store=MemoryStore(restored), summarizer=summarize,
            access=AccessSettings("accounts", "https://cat.example"),
        )
        new_session = restored_app.state.identities.login("owner", "owner-test-password", client_key="restore")
        self.assertIsNotNone(new_session)
        headers = {"Cookie": f"{SESSION_COOKIE}={new_session.token}"}
        with TestClient(restored_app, base_url="https://cat.example") as client:
            response = client.get("/api/admin/overview", headers=headers)
            self.assertEqual(response.status_code, 200, response.text)


if __name__ == "__main__":
    unittest.main()
