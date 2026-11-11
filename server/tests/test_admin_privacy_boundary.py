"""Independent content sentinels for every administrator read boundary."""

from contextlib import ExitStack, closing
from pathlib import Path
import os
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from access_control import AccessSettings, SESSION_COOKIE
from admin_accounts_store import AdminAccountsStore
from admin_fleet import FleetStore
from database_backups import BackupSettings
from identity_store import csrf_for_session
from memory_retention import unpack_archive
from memory_store import MemoryStore, SummaryResult
from support import server_app


async def summarize(_messages):
    return SummaryResult(summary="simulated-summary-only")


class AdminPrivacyBoundaryTests(unittest.TestCase):
    origin = "https://privacy.example"
    device_id = "cat-private-owner"
    password = "simulated-privacy-password"
    provider_key = "sk-SENTINEL_PROVIDER_KEY_PRIVATE_73d9"
    forbidden_fields = {
        "content", "original", "filename", "payload", "open_topics", "open_topics_json",
        "keywords", "aliases", "embedding", "error_code", "password", "password_hash",
        "api_key", "token", "token_hash", "ciphertext", "masked", "revision",
        "_credential_version", "download_url", "path", "directory", "receipt", "sha256",
        "source_id", "file",
    }

    def marker(self, owner, field):
        return "SENTINEL_PRIVATE_" + owner.upper() + "_" + field.upper() + "_73d9"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="admin-private-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = self.root / "private-database.sqlite3"
        with patch.dict(os.environ, {"CYBERMIAO_SECRET_KEY_FILE": str(self.root / "private-provider.key")}):
            self.app = server_app.create_app(
                store=MemoryStore(self.database), summarizer=summarize,
                access=AccessSettings("accounts", self.origin),
                backup_settings=BackupSettings(self.root / "private-backups", min_free_bytes=0),
            )
        self.ids = self.app.state.identities
        self.admins = self.app.state.admin_store
        self.users, self.headers, self.tokens = {}, {}, {}
        for name in ("admin", "owner", "guest"):
            user = self.ids.create_user("privacy-" + name, self.password)
            self.users[name] = user
            grant = self.ids.login(user.username, self.password, client_key="privacy-" + name)
            self.tokens[name] = grant.token
            self.headers[name] = {"Cookie": f"{SESSION_COOKIE}={grant.token}", "Origin": self.origin,
                                  "X-CSRF-Token": csrf_for_session(grant.token)}
        self.admins.set_role(self.users["admin"].username, True)
        self.credential = self.ids.register_device(self.device_id, "Simulated private device")
        pairing = self.ids.request_pairing(self.device_id, self.credential.token)
        self.ids.claim_pairing(pairing.code, self.users["owner"].id, client_key="privacy-owner")
        loan = self.ids.create_loan(self.device_id, self.users["owner"].id, 1)
        self.ids.claim_loan(loan["code"], self.users["guest"].id, client_key="privacy-guest")
        self.documents = {}
        self.private_values = [self.password, self.provider_key, self.credential.token, self.root.name,
                               *self.tokens.values()]
        memory = self.app.state.memory_manager.store
        retention = self.app.state.memory_retention
        now = time.time()
        for name in ("owner", "guest"):
            user_id = self.users[name].id
            conversation = memory.create_conversation(user_id, self.device_id)
            archive = self.marker(name, "archive")
            last = memory.append_message(conversation, "user", archive * 128)
            memory.save_summary(conversation, last, self.marker(name, "summary"),
                                memories=[self.marker(name, "fact")], open_topics=[self.marker(name, "topic")])
            with self.ids._transaction(write=True) as db:
                db.execute("UPDATE conversations SET last_activity_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                           (conversation,))
            retention.clock = lambda: now - 31 * 86400
            retention.set_mode(user_id, self.device_id, "temporary")
            retention.clock = lambda: now
            self.assertEqual(retention.archive(user_id, self.device_id), 1)
            memory.append_message(conversation, "user", self.marker(name, "message"))
            document = self.app.state.knowledge.store
            filename = self.marker(name, "filename") + ".txt"
            original = self.marker(name, "document")
            doc, _ = document.add_document(user_id, filename, original.encode())
            self.assertTrue(document.finish(document.claim_next(), [self.marker(name, "chunk")],
                                            error=self.marker(name, "document_error")))
            self.assertTrue(document.save_profile(user_id, doc["id"], {
                "summary": self.marker(name, "document_summary"),
                "keywords": ["PRIVATE_" + name + "_keyword"],
                "aliases": ["PRIVATE_" + name + "_alias"],
            }))
            self.documents[name] = doc["id"]
            for field in ("archive", "summary", "fact", "topic", "message", "filename", "document",
                          "chunk", "document_error", "document_summary"):
                self.private_values.append(self.marker(name, field))
            self.private_values.extend(("PRIVATE_" + name + "_keyword", "PRIVATE_" + name + "_alias"))
        masked = self.app.state.provider_settings.save(self.users["owner"].id, self.provider_key)["masked"]
        self.private_values.append(masked)
        self.client = TestClient(self.app, base_url=self.origin, client=("127.0.0.1", 12345),
                                 raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        guards = ExitStack()
        self.addCleanup(guards.close)
        for target, method in ((self.app.state.providers, "key"), (self.app.state.provider_settings, "get"),
                               (self.app.state.devices, "get"), (self.app.state.devices, "resolve")):
            guards.enter_context(patch.object(target, method, side_effect=AssertionError(
                "Administrator metadata must not read credentials or create a device runtime")))

    def request(self, method, path, *, actor="admin", headers=None, **kwargs):
        return self.client.request(method, path, headers=self.headers.get(actor, {}) if headers is None else headers,
                                   follow_redirects=False, **kwargs)

    def read_paths(self):
        return ("/api/admin/session", "/api/admin/overview", "/api/admin/dashboard", "/api/admin/users",
                "/api/admin/users/" + self.users["owner"].id,
                "/api/admin/users/" + self.users["guest"].id,
                "/api/admin/devices", "/api/admin/devices/" + self.device_id,
                "/api/admin/storage", "/api/admin/audit", "/api/admin/backups", "/api/admin/backups/history")

    def assert_private(self, response):
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        for value in self.private_values:
            self.assertNotIn(value, response.text)
        if response.headers.get("content-type", "").startswith("application/json"):
            def visit(value):
                if isinstance(value, dict):
                    self.assertFalse(self.forbidden_fields.intersection(value), value.keys())
                    for child in value.values():
                        visit(child)
                elif isinstance(value, list):
                    for child in value:
                        visit(child)
            visit(response.json())

    def assert_counts(self, value):
        for child in value.values():
            if isinstance(child, dict):
                self.assert_counts(child)
            else:
                self.assertIs(type(child), int)
                self.assertGreaterEqual(child, 0)

    def test_every_management_read_exposes_metadata_and_counts_without_private_payload(self):
        expected_routes = {"/api/admin/" + suffix for suffix in (
            "session", "overview", "dashboard", "users", "users/{user_id}", "devices",
            "devices/{device_id}", "storage", "audit", "backups", "backups/history")}
        actual_routes = {path for path, methods in self.app.openapi()["paths"].items()
                         if path.startswith("/api/admin/") and "get" in methods}
        self.assertEqual(actual_routes, expected_routes, "New administrator reads require an explicit privacy review")
        for path in self.read_paths():
            with self.subTest(path=path):
                response = self.request("GET", path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assert_private(response)
        storage = self.request("GET", "/api/admin/storage").json()
        self.assert_counts(storage["documents"])
        self.assert_counts(storage["memory"])
        self.assertEqual(storage["documents"]["count"], 2)
        self.assertEqual(storage["memory"]["archives"], 2)
        self.assertEqual(storage["memory"]["archived_messages"], 2)
        self.assertGreater(storage["memory"]["archive_bytes"], 0)
        self.assertGreater(storage["memory"]["content_bytes"], 0)
        for name in ("owner", "guest"):
            detail = self.request("GET", "/api/admin/users/" + self.users[name].id).json()
            self.assert_counts(detail["documents"])
            self.assert_counts(detail["memory"])
            self.assertEqual(detail["memory"]["archives"], 1)
        fleet = self.request("GET", "/api/admin/devices/" + self.device_id).json()
        self.assertEqual(fleet["history"]["conversations"], 2)
        self.assertEqual(self.app.state.devices.devices, {})

    def test_all_management_reads_reject_member_hardware_and_revoked_role(self):
        forged = {**self.headers["owner"], "X-User-Id": self.users["admin"].id, "X-Admin": "true",
                  "Authorization": "Bearer " + self.credential.token}
        for path in self.read_paths():
            for headers, expected in (({}, 401), (forged, 403),
                                      ({"Authorization": "Bearer " + self.credential.token}, 401)):
                with self.subTest(path=path, expected=expected):
                    response = self.request("GET", path, headers=headers, params={"user_id": self.users["admin"].id})
                    self.assertEqual(response.status_code, expected, response.text)
                    self.assert_private(response)
        self.admins.set_role(self.users["guest"].username, True)
        self.admins.set_role(self.users["admin"].username, False)
        for path in self.read_paths():
            response = self.request("GET", path)
            self.assertEqual(response.status_code, 403, response.text)
            self.assert_private(response)

    def test_administrator_role_cannot_read_another_accounts_memory_or_documents(self):
        for name in ("owner", "guest"):
            response = self.request("GET", "/api/account/memories/" + self.device_id, actor=name)
            self.assertEqual(response.status_code, 200, response.text)
            for field in ("message", "summary", "fact", "topic"):
                self.assertIn(self.marker(name, field), response.text)
            response = self.request("GET", "/api/knowledge/documents/" + self.documents[name] + "/download", actor=name)
            self.assertEqual(response.content, self.marker(name, "document").encode())
            response = self.request("GET", "/api/knowledge", actor=name)
            self.assertIn(self.marker(name, "filename"), response.text)
            self.assertIn(self.marker(name, "document_summary"), response.text)
        with self.ids._transaction() as db:
            archives = db.execute("SELECT * FROM memory_message_archives ORDER BY id").fetchall()
        self.assertEqual(len(archives), 2)
        self.assertTrue(all(any(self.marker(name, "archive") in item["content"]
                                for row in archives for item in unpack_archive(row)) for name in ("owner", "guest")))
        for path in ("/api/account/memories/" + self.device_id, "/api/devices/" + self.device_id + "/memory",
                     *("/api/knowledge/documents/" + doc + "/download" for doc in self.documents.values())):
            response = self.request("GET", path, params={"user_id": self.users["owner"].id})
            self.assertEqual(response.status_code, 404, response.text)
            self.assert_private(response)
            self.assertNotIn("content-disposition", response.headers)
        for path, key in (("/api/account/memories", "devices"), ("/api/knowledge", "documents")):
            response = self.request("GET", path, params={"user_id": self.users["owner"].id})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()[key], [])
            self.assert_private(response)
        response = self.request("GET", "/api/account/model", params={"user_id": self.users["owner"].id})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["configured"])
        for value in self.private_values:
            self.assertNotIn(value, response.text)

    def test_read_failures_never_expose_exception_details_or_partial_private_data(self):
        error = " ".join(self.private_values)
        cases = ((self.admins, "counts", "/api/admin/overview"),
                 (self.admins, "users", "/api/admin/users"),
                 (self.admins, "devices", "/api/admin/devices"),
                 (self.admins, "storage", "/api/admin/storage"),
                 (self.admins, "audit", "/api/admin/audit"),
                 (AdminAccountsStore, "detail", "/api/admin/users/" + self.users["owner"].id),
                 (FleetStore, "detail", "/api/admin/devices/" + self.device_id),
                 (self.app.state.database_backups.repository, "verified_entries", "/api/admin/backups/history"))
        for target, method, path in cases:
            with self.subTest(path=path), patch.object(target, method, side_effect=sqlite3.OperationalError(error)):
                response = self.request("GET", path)
                self.assertEqual(response.status_code, 503, response.text)
                self.assertEqual(set(response.json()), {"detail"})
                self.assert_private(response)
        with patch("admin_dashboard.dashboard_metadata", side_effect=OSError(error)):
            response = self.request("GET", "/api/admin/dashboard")
        self.assertEqual(response.status_code, 503, response.text)
        self.assert_private(response)

    def test_validation_and_failed_write_audit_do_not_copy_private_values(self):
        response = self.request("POST", "/api/admin/users/" + self.users["owner"].id + "/status", json={
            "confirm_username": self.users["owner"].username, "disabled": self.provider_key})
        self.assertEqual(response.status_code, 422, response.text)
        self.assert_private(response)
        response = self.request("POST", "/api/admin/users", json={
            "username": "../" + self.marker("owner", "filename"), "password": self.provider_key,
            "confirm_username": self.users["owner"].username})
        self.assertEqual(response.status_code, 422, response.text)
        self.assert_private(response)
        with patch.object(self.ids, "save_voice_preference", side_effect=OSError(" ".join(self.private_values))):
            response = self.request("POST", "/api/admin/devices/" + self.device_id + "/voice", json={
                "confirm_device_id": self.device_id, "paused": True})
        self.assertEqual(response.status_code, 503, response.text)
        self.assert_private(response)
        for path in ("/api/admin/audit", "/api/admin/dashboard"):
            response = self.request("GET", path)
            self.assertEqual(response.status_code, 200, response.text)
            self.assert_private(response)
        rows = self.request("GET", "/api/admin/audit", params={"q": "pause-voice"}).json()["items"]
        self.assertEqual(rows[0]["result"], "failed")
        self.assertEqual(set(rows[0]), {"id", "actor_id", "actor_username", "target_type", "target_id",
                                        "action", "result", "created_at"})

    def test_backup_with_private_payload_only_returns_verified_size_time_and_state(self):
        response = self.request("POST", "/api/admin/backups/run", json={"confirm": "检查并备份"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["created"])
        self.assert_private(response)
        entries, skipped = self.app.state.database_backups.repository.verified_entries()
        self.assertEqual((len(entries), skipped), (1, 0))
        _, database, receipt = entries[0]
        with closing(sqlite3.connect(database)) as db:
            self.assertEqual(db.execute("SELECT original FROM knowledge_documents WHERE id=?",
                                        (self.documents["owner"],)).fetchone()[0], self.marker("owner", "document").encode())
        response = self.request("GET", "/api/admin/backups/history")
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_private(response)
        self.assertEqual(set(response.json()["items"][0]), {"created_at", "bytes", "verified"})
        self.assertEqual(response.json()["items"][0]["bytes"], database.stat().st_size)
        for filename in (database.name, receipt.name, self.database.name, "private-provider.key"):
            for path in ("/api/admin/backups/" + filename + "/download", "/web/" + filename,
                         "/web/admin/" + filename, "/web/%2e%2e/" + filename):
                with self.subTest(path=path):
                    result = self.request("GET", path)
                    self.assertEqual(result.status_code, 404, result.text)
                    self.assert_private(result)
        with patch.object(self.app.state.database_backups.repository, "verified_entries",
                          side_effect=OSError(" ".join(self.private_values))):
            response = self.request("POST", "/api/admin/backups/run", json={"confirm": "检查并备份"})
        self.assertEqual(response.status_code, 503, response.text)
        self.assert_private(response)
        for path in ("/api/admin/backups", "/api/admin/audit", "/api/admin/dashboard"):
            response = self.request("GET", path)
            self.assertEqual(response.status_code, 200, response.text)
            self.assert_private(response)

    def test_protected_static_resources_and_aliases_cannot_expose_private_data(self):
        for actor, expected in ((None, 401), ("owner", 403), ("admin", 200)):
            for asset in ("index.html", "admin.js", "admin.css"):
                response = self.request("GET", "/web/admin/" + asset, actor=actor)
                self.assertEqual(response.status_code, expected, response.text[:200])
                self.assert_private(response)
        for actor in (None, "owner", "admin"):
            for path in ("/web/ADMIN/index.html", "/web/admin%5cindex.html", "/web/admin./admin.js",
                         "/web/ADMIN/admin.css", "/web/admin/../../" + self.database.name,
                         "/web/admin/login.html", "/records/" + self.device_id + "/private.jpg"):
                response = self.request("GET", path, actor=actor)
                self.assertIn(response.status_code, (401, 403, 404), response.text[:200])
                self.assert_private(response)
        response = self.request("GET", "/admin")
        self.assertEqual(response.status_code, 200, response.text[:200])
        self.assert_private(response)
        self.assertEqual(self.app.state.devices.devices, {})


if __name__ == "__main__":
    unittest.main()
