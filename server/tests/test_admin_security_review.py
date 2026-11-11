"""Independent admin security checks using only simulated accounts and devices."""

from pathlib import Path
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Mount

from access_control import AccessSettings, SESSION_COOKIE
from admin_store import AdminStore
from identity_store import IdentityStore
from identity_store import csrf_for_session
from memory_store import MemoryStore, SummaryResult
from support import server_app
from tools.memory_database import backup_database, restore_database


async def summarize(messages):
    return SummaryResult(summary="Fictional summary")


class DisabledAccountBoundaryTests(unittest.TestCase):
    def test_disabled_current_user_invalidates_device_credential(self):
        with tempfile.TemporaryDirectory(prefix="admin-security-") as directory:
            identities = IdentityStore(Path(directory) / "memory.sqlite3")
            user = identities.create_user("fictional_alice", "fictional-password-123")
            credential = identities.register_device("cat-fictional-a", "Fictional A")
            pairing = identities.request_pairing("cat-fictional-a", credential.token)
            identities.claim_pairing(pairing.code, user.id, client_key="fictional-local")
            self.assertEqual(
                identities.credential_device("cat-fictional-a", credential.token)["active_user_id"],
                user.id,
            )

            with identities._transaction(write=True) as db:
                db.execute("UPDATE auth_users SET disabled=1 WHERE id=?", (user.id,))

            self.assertIsNone(identities.credential_device("cat-fictional-a", credential.token))

    def test_unknown_admin_schema_version_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="admin-schema-") as directory:
            path = Path(directory) / "memory.sqlite3"
            MemoryStore(path)
            identities = IdentityStore(path)
            with closing(sqlite3.connect(path)) as db:
                with db:
                    db.execute("CREATE TABLE admin_schema (version INTEGER PRIMARY KEY)")
                    db.execute("INSERT INTO admin_schema VALUES (2)")

            with self.assertRaises(ValueError):
                AdminStore(identities)

    def test_concurrent_cross_disable_keeps_one_enabled_admin(self):
        with tempfile.TemporaryDirectory(prefix="admin-race-") as directory:
            path = Path(directory) / "memory.sqlite3"
            identities = IdentityStore(path)
            first = identities.create_user("fictional_one", "fictional-password-123")
            second = identities.create_user("fictional_two", "fictional-password-123")
            left = AdminStore(identities)
            right = AdminStore(IdentityStore(path))
            left.set_role(first.username, True)
            right.set_role(second.username, True)
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(left.change_user, first.id, second.id, True),
                           pool.submit(right.change_user, second.id, first.id, True)]
                errors = []
                for future in futures:
                    try:
                        future.result()
                    except (PermissionError, ValueError) as exc:
                        errors.append(exc)
            self.assertEqual(len(errors), 1)
            self.assertEqual(sum(left.is_admin(user.id) for user in (first, second)), 1)


class AdminAuthorizationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="admin-boundary-")
        path = Path(self.temp.name) / "memory.sqlite3"
        self.app = server_app.create_app(
            store=MemoryStore(path), summarizer=summarize,
            access=AccessSettings("accounts", "https://fictional.example"),
        )
        self.ids = self.app.state.identities
        self.admin = self.ids.create_user("fictional_admin", "fictional-password-123")
        self.member = self.ids.create_user("fictional_member", "fictional-password-123")
        self.app.state.admin_store.set_role(self.admin.username, True)
        self.credential = self.ids.register_device("cat-fictional-a", "Fictional A")
        pairing = self.ids.request_pairing("cat-fictional-a", self.credential.token)
        self.ids.claim_pairing(pairing.code, self.member.id, client_key="fictional-local")
        self.client = TestClient(self.app, base_url="https://fictional.example")
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def test_admin_html_cannot_be_loaded_through_static_mount(self):
        response = self.client.get("/admin", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['location'], '/admin/login')
        self.assertIn(self.client.get("/web/admin/index.html").status_code, (401, 403))
        session = self.ids.login(self.member.username, "fictional-password-123", client_key="fictional-local")
        self.client.cookies.set(SESSION_COOKIE, session.token)
        self.assertEqual(self.client.get("/admin").status_code, 403)
        self.assertEqual(self.client.get("/web/admin/index.html").status_code, 403)

    def test_public_static_mount_cannot_resolve_admin_files(self):
        # Exercise the filesystem boundary directly, independently of route matching.
        public_files = next(route.app for route in self.app.routes if getattr(route, "path", None) == "/web")
        with TestClient(Starlette(routes=[Mount("/web", app=public_files)])) as client:
            for asset in ("index.html", "admin.js", "admin.css"):
                with self.subTest(asset=asset):
                    self.assertEqual(client.get("/web/admin/" + asset).status_code, 404)
            self.assertEqual(client.get("/web/portal.css").status_code, 200)

    def test_admin_static_windows_aliases_never_use_public_mount(self):
        aliases = (
            "/web/ADMIN/index.html", "/web/admin%5cindex.html",
            "/web/Admin/admin.js", "/web/ADMIN/admin.css",
            "/web/ADMIN/INDEX.HTML", "/web/admin./index.html",
            "/web/%41DMIN/index.html", "/web/%61dmin%5cadmin.js",
        )
        member_session = self.ids.login(self.member.username, "fictional-password-123",
                                        client_key="fictional-local")
        admin_session = self.ids.login(self.admin.username, "fictional-password-123",
                                       client_key="fictional-local")
        for actor, token in (("anonymous", None), ("member", member_session.token),
                             ("admin", admin_session.token)):
            headers = {"Cookie": f"{SESSION_COOKIE}={token}"} if token else {}
            for path in aliases:
                with self.subTest(actor=actor, path=path):
                    self.assertEqual(self.client.get(path, headers=headers).status_code, 404)
        for token, expected in ((None, 401), (member_session.token, 403), (admin_session.token, 200)):
            headers = {"Cookie": f"{SESSION_COOKIE}={token}"} if token else {}
            for path in ("/web/admin/index.html", "/web/admin/admin.js", "/web/admin/admin.css"):
                with self.subTest(path=path, expected=expected):
                    response = self.client.get(path, headers=headers)
                    self.assertEqual(response.status_code, expected)
                    self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_local_mode_blocks_admin_aliases_and_keeps_user_assets(self):
        app = server_app.create_app(
            store=MemoryStore(Path(self.temp.name) / "local.sqlite3"), summarizer=summarize,
            access=AccessSettings("local"),
        )
        with TestClient(app, client=("127.0.0.1", 12345)) as client:
            for path in ("/web/ADMIN/index.html", "/web/admin%5cindex.html",
                         "/web/Admin/admin.js", "/web/admin/index.html"):
                with self.subTest(path=path):
                    self.assertEqual(client.get(path).status_code, 404)
            for path in ("/", "/web/portal.css", "/web/portal.js", "/web/console_access.js"):
                with self.subTest(path=path):
                    self.assertEqual(client.get(path).status_code, 200)

    def test_member_cannot_impersonate_admin_or_use_device_credential(self):
        session = self.ids.login(self.member.username, "fictional-password-123", client_key="fictional-local")
        forged = {
            "Cookie": f"{SESSION_COOKIE}={session.token}",
            "Origin": "https://fictional.example",
            "X-CSRF-Token": csrf_for_session(session.token),
            "X-User-Id": self.admin.id,
            "X-Admin": "true",
            "Authorization": f"Bearer {self.credential.token}",
        }
        reads = ("session", "overview", "users", "devices", "storage", "backups", "audit")
        for route in reads:
            with self.subTest(route=route):
                response = self.client.get(f"/api/admin/{route}?user_id={self.admin.id}", headers=forged)
                self.assertEqual(response.status_code, 403, response.text)
        writes = (
            (f"users/{self.admin.id}/status", {"disabled": True, "confirm_username": self.admin.username}),
            (f"users/{self.admin.id}/revoke-sessions", {"confirm_username": self.admin.username}),
            ("devices/cat-fictional-a/voice", {"paused": False, "confirm_device_id": "cat-fictional-a"}),
            ("devices/cat-fictional-a/unbind", {"confirm_device_id": "cat-fictional-a"}),
        )
        for route, payload in writes:
            with self.subTest(route=route):
                response = self.client.post(f"/api/admin/{route}?user_id={self.admin.id}",
                                            headers=forged, json=payload)
                self.assertEqual(response.status_code, 403, response.text)
        self.assertIsNotNone(self.ids.credential_device("cat-fictional-a", self.credential.token))
        self.assertTrue(self.app.state.admin_store.is_admin(self.admin.id))

    def test_validation_and_read_responses_hide_private_values(self):
        self.app.state.provider_settings.save(self.member.id, "sk-fictional-private-key")
        session = self.ids.login(self.admin.username, "fictional-password-123", client_key="fictional-local")
        headers = {"Cookie": f"{SESSION_COOKIE}={session.token}", "Origin": "https://fictional.example",
                   "X-CSRF-Token": csrf_for_session(session.token)}
        response = self.client.post(f"/api/admin/users/{self.member.id}/status", headers=headers,
                                    json={"disabled": "sk-fictional-private-key", "confirm_username": self.member.username})
        self.assertEqual(response.status_code, 422)
        bodies = [response.text]
        for route in ("session", "overview", "users", "devices", "storage", "backups", "audit"):
            result = self.client.get(f"/api/admin/{route}", headers=headers)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.headers.get("cache-control"), "no-store")
            bodies.append(result.text)
        serialized = "".join(bodies)
        for secret in ("sk-fictional-private-key", "fictional-password-123", self.credential.token,
                       "password_hash", "ciphertext", "token_hash"):
            self.assertNotIn(secret, serialized)

    def test_failed_cleanup_does_not_misreport_committed_account_change(self):
        session = self.ids.login(self.admin.username, "fictional-password-123", client_key="fictional-local")
        headers = {"Cookie": f"{SESSION_COOKIE}={session.token}", "Origin": "https://fictional.example",
                   "X-CSRF-Token": csrf_for_session(session.token)}
        with patch.object(self.app.state.account_operations, "cancel_user",
                          new=AsyncMock(side_effect=RuntimeError("fictional cleanup failure"))):
            response = self.client.post(f"/api/admin/users/{self.member.id}/status", headers=headers,
                                        json={"disabled": True, "confirm_username": self.member.username})
        self.assertEqual(response.status_code, 503)
        disabled = self.app.state.admin_store.user(self.member.id)["disabled"]
        audit = self.client.get("/api/admin/audit", headers=headers).json()["items"][0]
        self.assertTrue((not disabled and audit["result"] == "failed")
                        or (disabled and audit["result"] == "started"))
        self.assertIn("已生效", response.json()["detail"])

    def test_backup_restore_preserves_admin_role_and_rejects_old_session(self):
        root = Path(self.temp.name)
        database = root / "memory.sqlite3"
        backup = root / "backup.sqlite3"
        restored = root / "restored.sqlite3"
        session = self.ids.login(self.admin.username, "fictional-password-123", client_key="fictional-local")
        report = backup_database(database, backup)
        result = restore_database(backup, restored, report["sha256"], apply=True, offline=True)
        self.assertTrue(result["applied"])
        recovered = IdentityStore(restored)
        self.assertIsNone(recovered.session_user(session.token))
        self.assertTrue(AdminStore(recovered).is_admin(self.admin.id))
        with closing(sqlite3.connect(restored)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM admin_audit").fetchone()[0], 1)

    def admin_headers(self):
        session = self.ids.login(self.admin.username, "fictional-password-123", client_key="fictional-local")
        return {"Cookie": f"{SESSION_COOKIE}={session.token}", "Origin": "https://fictional.example",
                "X-CSRF-Token": csrf_for_session(session.token)}

    def latest_device_audit(self, headers):
        rows = self.client.get("/api/admin/audit", headers=headers).json()["items"]
        return next(row for row in rows if row["target_type"] == "device")

    def test_unbind_cleanup_failure_can_be_retried_after_commit(self):
        headers = self.admin_headers()
        runtime = self.client.portal.call(self.app.state.devices.resolve, "cat-fictional-a", self.member.id)
        before = self.ids.credential_device("cat-fictional-a", self.credential.token)["access_epoch"]
        path = "/api/admin/devices/cat-fictional-a/unbind"
        payload = {"confirm_device_id": "cat-fictional-a"}
        with patch.object(self.app.state.connections, "revoke_device",
                          new=AsyncMock(side_effect=RuntimeError("fictional revoke failure"))):
            response = self.client.post(path, headers=headers, json=payload)
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIsNone(self.ids.credential_device("cat-fictional-a", self.credential.token)["owner_user_id"])
        self.assertEqual(self.latest_device_audit(headers)["result"], "started")
        self.assertIn("已生效", response.json()["detail"])
        self.assertNotIn("fictional revoke failure", response.text)
        self.assertIs(self.app.state.devices.devices["cat-fictional-a"], runtime)

        # A second cleanup failure must still be retryable after the binding is gone.
        with patch.object(runtime, "close", new=AsyncMock(side_effect=RuntimeError("fictional close failure"))):
            response = self.client.post(path, headers=headers, json=payload)
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(self.latest_device_audit(headers)["result"], "started")
        response = self.client.post(path, headers=headers, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.latest_device_audit(headers)["result"], "success")
        self.assertNotIn("cat-fictional-a", self.app.state.devices.devices)
        self.assertEqual(self.ids.credential_device("cat-fictional-a", self.credential.token)["access_epoch"], before + 1)

    def test_voice_sync_failure_preserves_committed_progress_and_retries(self):
        headers = self.admin_headers()
        runtime = self.client.portal.call(self.app.state.devices.resolve, "cat-fictional-a", self.member.id)
        path = "/api/admin/devices/cat-fictional-a/voice"
        for paused in (True, False):
            with self.subTest(paused=paused):
                payload = {"confirm_device_id": "cat-fictional-a", "paused": paused}
                with patch.object(runtime, "_sync_voice_control",
                                  new=AsyncMock(side_effect=RuntimeError("fictional sync failure"))):
                    response = self.client.post(path, headers=headers, json=payload)
                self.assertEqual(response.status_code, 503, response.text)
                self.assertEqual(self.ids.voice_preference(self.member.id, "cat-fictional-a"), paused)
                self.assertEqual(runtime.voice_paused, paused)
                self.assertEqual(self.latest_device_audit(headers)["result"], "started")
                self.assertIn("已生效", response.json()["detail"])
                self.assertNotIn("fictional sync failure", response.text)
                with patch.object(runtime, "_sync_voice_control", new=AsyncMock()) as synchronize:
                    response = self.client.post(path, headers=headers, json=payload)
                self.assertEqual(response.status_code, 200, response.text)
                synchronize.assert_awaited_once()
                self.assertEqual(self.latest_device_audit(headers)["result"], "success")

    def test_voice_write_failure_before_commit_is_recorded_failed(self):
        headers = self.admin_headers()
        path = "/api/admin/devices/cat-fictional-a/voice"
        with patch.object(self.ids, "save_voice_preference", side_effect=RuntimeError("fictional database failure")):
            response = self.client.post(path, headers=headers,
                                        json={"confirm_device_id": "cat-fictional-a", "paused": True})
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(self.latest_device_audit(headers)["result"], "failed")
        self.assertFalse(self.ids.voice_preference(self.member.id, "cat-fictional-a"))
        self.assertNotIn("已生效", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
