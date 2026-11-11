"""独立管理登录入口、真实运行时长和私人正文边界；不修改现有夹具。"""

from contextlib import closing
import sqlite3
import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from access_control import AccessSettings, SESSION_COOKIE
from memory_store import MemoryStore
from support import server_app
import test_admin_round2_acceptance as fixture


class IndependentAdminExperienceTests(unittest.TestCase):
    tearDown = fixture.AdminRound2AcceptanceTests.tearDown
    request = fixture.AdminRound2AcceptanceTests.request

    def setUp(self):
        # 哨兵文档由测试同步建好，后台索引不得抢走任务或尝试模型调用。
        worker = patch("knowledge_service.KnowledgeService.start", new=AsyncMock())
        worker.start()
        self.addCleanup(worker.stop)
        fixture.AdminRound2AcceptanceTests.setUp(self)

    def test_failed_admin_login_does_not_issue_sessions_or_revoke_existing_browser_session(self):
        with closing(sqlite3.connect(self.database)) as db:
            before = db.execute("SELECT token_hash,revoked_at FROM auth_sessions ORDER BY token_hash").fetchall()
        for username, password, expected in (("owner", "incorrect-test-password", 401),
                                             ("missing-experience-user", "incorrect-test-password", 401),
                                             ("alice", "alice-test-password", 403)):
            with self.subTest(username=username):
                response = self.request("POST", "/api/admin/auth/login", headers={"Origin": "https://cat.example"},
                                        json={"username": username, "password": password})
                self.assertEqual(response.status_code, expected, response.text)
                self.assertNotIn("set-cookie", response.headers)
                self.assertEqual(response.headers.get("cache-control"), "no-store")
                self.assertNotIn(password, response.text)
        # 已登录浏览器输入普通账号也不能丢失原来的合法管理会话。
        response = self.request("POST", "/api/admin/auth/login", actor="owner",
                                json={"username": "alice", "password": "alice-test-password"})
        self.assertEqual(response.status_code, 403, response.text)
        self.assertNotIn("set-cookie", response.headers)
        with closing(sqlite3.connect(self.database)) as db:
            after = db.execute("SELECT token_hash,revoked_at FROM auth_sessions ORDER BY token_hash").fetchall()
        self.assertEqual(after, before)
        self.assertEqual(self.request("GET", "/api/admin/session", actor="owner").status_code, 200)

    def test_admin_login_sets_http_only_cookie_and_allows_console_without_redirect_field(self):
        response = self.request("POST", "/api/admin/auth/login", headers={"Origin": "https://cat.example"},
                                json={"username": "owner", "password": "owner-test-password"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertNotIn("redirect", body)
        self.assertEqual(body["user"]["id"], self.users["owner"].id)
        self.assertTrue(body["csrf_token"])
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        cookie = response.headers.get("set-cookie", "")
        for attribute in ("HttpOnly", "SameSite=strict", "Path=/", "Secure"):
            self.assertIn(attribute.lower(), cookie.lower())
        self.assertNotIn(response.cookies[SESSION_COOKIE], response.text)
        self.assertEqual(self.request("GET", "/admin").status_code, 200)
        self.assertEqual(self.request("GET", "/api/admin/session").status_code, 200)

    def test_revoked_admin_can_logout_and_old_cookie_no_longer_authenticates(self):
        self.app.state.admin_store.set_role("bob", True)
        self.app.state.admin_store.set_role("owner", False)
        self.assertEqual(self.request("GET", "/api/admin/session", actor="owner").status_code, 403)
        response = self.request("POST", "/api/admin/auth/logout", actor="owner")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        self.assertIn("max-age=0", response.headers.get("set-cookie", "").lower())
        self.assertEqual(self.request("GET", "/api/auth/session", actor="owner").status_code, 401)
        self.assertEqual(self.request("GET", "/api/admin/session", actor="owner").status_code, 401)

    def test_anonymous_admin_entry_uses_dedicated_login_and_private_admin_stays_denied(self):
        response = self.request("GET", "/admin")
        self.assertEqual(response.status_code, 303, response.text)
        self.assertEqual(response.headers["location"], "/admin/login")
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        login = self.request("GET", "/admin/login")
        self.assertEqual(login.status_code, 200, login.text)
        self.assertEqual(login.headers.get("cache-control"), "no-store")
        self.assertIn("text/html", login.headers.get("content-type", ""))
        self.assertEqual(self.request("GET", "/admin", actor="alice").status_code, 403)
        self.assertEqual(self.request("GET", "/api/admin/dashboard", actor="alice").status_code, 403)
        self.assertEqual(self.request("GET", "/api/admin/dashboard", headers=self.device_headers).status_code, 401)

    def test_existing_admin_goes_directly_to_console_and_revoked_role_cannot_read_metadata(self):
        response = self.request("GET", "/admin/login", actor="owner")
        self.assertEqual(response.status_code, 303, response.text)
        self.assertEqual(response.headers["location"], "/admin")
        self.assertEqual(self.request("GET", "/admin", actor="owner").status_code, 200)
        self.app.state.admin_store.set_role("bob", True)
        self.app.state.admin_store.set_role("owner", False)
        for path in ("/admin", "/api/admin/dashboard", "/api/admin/users", "/api/admin/storage"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path, actor="owner").status_code, 403)

    def test_dedicated_login_is_absent_in_local_mode(self):
        app = server_app.create_app(store=MemoryStore(self.database.with_name("local.sqlite3")),
                                    access=AccessSettings("local"))
        with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345),
                        headers={"Origin": "http://127.0.0.1"}) as client:
            self.assertEqual(client.get("/admin/login", follow_redirects=False).status_code, 404)
            self.assertEqual(client.get("/admin", follow_redirects=False).status_code, 404)
            self.assertEqual(client.post("/api/admin/auth/login", json={
                "username": "owner", "password": "owner-test-password"}).status_code, 404)
            self.assertEqual(client.post("/api/admin/auth/logout").status_code, 404)

    def test_uptime_reports_actual_elapsed_seconds_instead_of_minute_buckets(self):
        self.app.state.admin_started_at = time.monotonic() - 65.25
        started = time.monotonic()
        first_response = self.request("GET", "/api/admin/dashboard", actor="owner")
        self.assertEqual(first_response.status_code, 200, first_response.text)
        first = first_response.json()["summary"]["uptime_seconds"]
        self.assertGreaterEqual(first, 65.25)
        self.assertLess(first, 65.25 + (time.monotonic() - started) + .2)
        time.sleep(.15)
        second_response = self.request("GET", "/api/admin/overview", actor="owner")
        self.assertEqual(second_response.status_code, 200, second_response.text)
        second = second_response.json()["uptime_seconds"]
        self.assertGreater(second - first, .1)
        self.assertLess(second - first, time.monotonic() - started + .2)

    def test_login_html_console_and_all_admin_metadata_exclude_seeded_private_content(self):
        memory = self.app.state.memory_manager.store
        conversation = memory.create_conversation(self.users["alice"].id, "cat-alice")
        message = memory.append_message(conversation, "user", "PRIVATE_EXPERIENCE_MESSAGE")
        memory.save_summary(conversation, message, "PRIVATE_EXPERIENCE_SUMMARY",
                            memories=["PRIVATE_EXPERIENCE_FACT"])
        documents = self.app.state.knowledge.store
        documents.add_document(self.users["alice"].id, "PRIVATE_EXPERIENCE_FILENAME.txt",
                               b"PRIVATE_EXPERIENCE_DOCUMENT")
        from knowledge_store import split_text
        documents.finish(documents.claim_next(), split_text("PRIVATE_EXPERIENCE_DOCUMENT"))
        paths = ("/admin", "/api/admin/dashboard", "/api/admin/session", "/api/admin/overview",
                 "/api/admin/users", "/api/admin/users/" + self.users["alice"].id,
                 "/api/admin/devices", "/api/admin/devices/cat-alice", "/api/admin/storage",
                 "/api/admin/backups", "/api/admin/backups/history", "/api/admin/audit")
        for path in paths:
            with self.subTest(path=path):
                response = self.request("GET", path, actor="owner")
                self.assertEqual(response.status_code, 200, response.text)
                for secret in ("PRIVATE_EXPERIENCE_", "alice-test-password", "password_hash",
                               "token_hash", "ciphertext", self.device_headers["Authorization"].split()[1]):
                    self.assertNotIn(secret, response.text)
        login = self.request("GET", "/admin/login")
        self.assertEqual(login.status_code, 200, login.text)
        self.assertNotIn("PRIVATE_EXPERIENCE_", login.text)
        self.assertEqual(self.app.state.devices.devices, {})


if __name__ == "__main__":
    unittest.main()
