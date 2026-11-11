"""Independent administrator login, revocation and redirect security checks."""

from pathlib import Path
import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from access_control import AccessSettings, SESSION_COOKIE
from database_backups import BackupSettings
from identity_store import csrf_for_session
from memory_store import MemoryStore, SummaryResult
from support import server_app


async def summarize(_messages):
    return SummaryResult(summary="simulated-login-only")


class IndependentAdminLoginSecurityTests(unittest.TestCase):
    origin = "https://login-security.example"
    login_path = "/api/admin/auth/login"
    logout_path = "/api/admin/auth/logout"
    password = "simulated-login-private-password"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="admin-login-security-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        with patch.dict(os.environ, {"CYBERMIAO_SECRET_KEY_FILE": str(self.root / "provider.key")}):
            self.app = server_app.create_app(
                store=MemoryStore(self.root / "memory.sqlite3"), summarizer=summarize,
                access=AccessSettings("accounts", self.origin), backup_settings=BackupSettings())
        self.ids, self.admins = self.app.state.identities, self.app.state.admin_store
        self.users, self.tokens = {}, {}
        for name in ("admin", "member", "disabled"):
            self.users[name] = self.ids.create_user("login-" + name, self.password)
        self.admins.set_role(self.users["admin"].username, True)
        self.admins.set_role(self.users["disabled"].username, True)
        for name in ("admin", "member"):
            self.tokens[name] = self.ids.login(self.users[name].username, self.password, client_key=name).token
        with self.ids._transaction(write=True) as db:
            db.execute("UPDATE auth_users SET disabled=1 WHERE id=?", (self.users["disabled"].id,))
        self.client = TestClient(self.app, base_url=self.origin, client=("127.0.0.1", 12345),
                                 raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def headers(self, actor="member"):
        token = self.tokens[actor]
        return {"Cookie": f"{SESSION_COOKIE}={token}", "Origin": self.origin,
                "X-CSRF-Token": csrf_for_session(token)}

    def sessions(self):
        with self.ids._transaction() as db:
            return [tuple(row) for row in db.execute(
                "SELECT token_hash,user_id,created_at,expires_at,revoked_at FROM auth_sessions ORDER BY token_hash")]

    def assert_private(self, response, *extra):
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        for value in (self.password, self.root.name, *self.tokens.values(), *extra):
            self.assertNotIn(value, response.text)

    def login(self, username=None, password=None, **kwargs):
        return self.client.post(self.login_path, headers=kwargs.pop("headers", self.headers()),
                                json={"username": username or self.users["admin"].username,
                                      "password": password or self.password}, **kwargs)

    def test_non_admin_disabled_and_bad_credentials_do_not_issue_or_revoke_sessions(self):
        before = self.sessions()
        for username, password, expected in ((self.users["member"].username, self.password, 403),
                                             (self.users["disabled"].username, self.password, 401),
                                             (self.users["admin"].username, "SENTINEL_WRONG_PASSWORD_8c71", 401),
                                             ("login-unknown", self.password, 401)):
            with self.subTest(username=username):
                response = self.login(username, password)
                self.assertEqual(response.status_code, expected, response.text)
                self.assertNotIn("set-cookie", response.headers)
                self.assertEqual(self.sessions(), before)
                self.assertIsNotNone(self.ids.session_user(self.tokens["member"]))
                self.assert_private(response, "SENTINEL_WRONG_PASSWORD_8c71")
        response = self.client.get("/api/admin/session", headers=self.headers())
        self.assertEqual(response.status_code, 403, response.text)

    def test_non_admin_login_failure_preserves_the_current_administrator(self):
        before = self.sessions()
        response = self.login(self.users["member"].username, headers=self.headers("admin"))
        self.assertEqual(response.status_code, 403, response.text)
        self.assertNotIn("set-cookie", response.headers)
        self.assertEqual(self.sessions(), before)
        response = self.client.get("/api/admin/session", headers=self.headers("admin"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["user"]["id"], self.users["admin"].id)

    def test_success_uses_private_cookie_and_revokes_only_the_replaced_browser_session(self):
        response = self.login("  ＬＯＧＩＮ-ＡＤＭＩＮ  ")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(set(body), {"ok", "user", "csrf_token"})
        self.assertTrue(body["ok"])
        self.assertEqual(body["user"], {"id": self.users["admin"].id, "username": self.users["admin"].username})
        cookie = response.headers["set-cookie"].lower()
        for flag in ("httponly", "secure", "samesite=strict", "path=/", "max-age=604800"):
            self.assertIn(flag, cookie)
        new_token = response.cookies[SESSION_COOKIE]
        self.assert_private(response, new_token)
        self.assertEqual(body["csrf_token"], csrf_for_session(new_token))
        self.assertIsNone(self.ids.session_user(self.tokens["member"]))
        self.assertIsNotNone(self.ids.session_user(self.tokens["admin"]))
        self.assertEqual(self.ids.session_user(new_token).id, self.users["admin"].id)
        response = self.client.get("/api/admin/session", headers={"Cookie": f"{SESSION_COOKIE}={new_token}"})
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.get("/api/auth/session", headers={"Cookie": f"{SESSION_COOKIE}={new_token}"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIs(response.json()["is_admin"], True)
        self.assertIs(self.client.get("/api/auth/session", headers=self.headers("admin")).json()["is_admin"], True)

    def test_login_requires_origin_and_rejects_role_and_redirect_inputs_without_side_effects(self):
        body = {"username": self.users["admin"].username, "password": self.password}
        before = self.sessions()
        for headers in ({}, {"Origin": "https://outside.example"},
                        {"Origin": self.origin, "Sec-Fetch-Site": "cross-site"}):
            response = self.client.post(self.login_path, headers=headers, json=body)
            self.assertEqual(response.status_code, 403, response.text)
            self.assertNotIn("set-cookie", response.headers)
            self.assertEqual(self.sessions(), before)
            self.assert_private(response)
        targets = ("/admin", "/", "//outside.example/admin", "https://outside.example/admin",
                   r"/\outside.example/admin", "/%2foutside.example/admin", "/admin/../knowledge",
                   "/administer", "/admin?next=https://outside.example", "javascript:alert(1)")
        for target in targets:
            for field in ("next", "redirect"):
                with self.subTest(field=field, target=target):
                    for location in ("body", "query"):
                        response = self.client.post(self.login_path, headers=self.headers(),
                                                    json={**body, **({field: target} if location == "body" else {})},
                                                    params={field: target} if location == "query" else None)
                        self.assertIn(response.status_code, (400, 422), response.text)
                        self.assertNotIn("set-cookie", response.headers)
                        self.assertNotIn("location", response.headers)
                        self.assertEqual(self.sessions(), before)
                        self.assert_private(response)
        for extra in ({"is_admin": True}, {"role": "admin"}, {"user_id": self.users["admin"].id}):
            response = self.client.post(self.login_path, headers=self.headers(), json={**body, **extra})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertNotIn("set-cookie", response.headers)
            self.assertEqual(self.sessions(), before)
            self.assert_private(response)

    def test_login_page_only_uses_fixed_administrator_navigation(self):
        before = self.sessions()
        for actor in (None, "member", "admin"):
            headers = self.headers(actor) if actor else {}
            response = self.client.get("/admin/login", headers=headers, follow_redirects=False)
            self.assertEqual(response.status_code, 303 if actor == "admin" else 200, response.text[:200])
            if actor == "admin":
                self.assertEqual(response.headers["location"], "/admin")
            else:
                self.assertIn("form-action 'self'", response.headers["content-security-policy"])
            self.assert_private(response)
            for field, target in (("next", "/admin"), ("next", "//outside.example/admin"),
                                  ("next", "https://outside.example/admin"),
                                  ("next", r"/\outside.example/admin"), ("next", "/admin/../knowledge"),
                                  ("next", "/%2foutside.example/admin"), ("redirect", "/admin")):
                response = self.client.get("/admin/login", headers=headers, params={field: target}, follow_redirects=False)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertNotIn("location", response.headers)
                self.assertNotIn("set-cookie", response.headers)
                self.assertEqual(self.sessions(), before)
                self.assert_private(response)
    def test_login_rechecks_role_name_disabled_and_password_after_verification(self):
        replacement = self.ids.hasher.hash("simulated-replacement-password")
        for change in ("role", "username", "disabled", "password"):
            with self.subTest(change=change):
                user = self.ids.create_user("login-race-" + change, self.password)
                self.admins.set_role(user.username, True)
                before = self.sessions()
                verify = self.ids.hasher.verify
                def verified_then_changed(password_hash, supplied):
                    result = verify(password_hash, supplied)
                    with self.ids._transaction(write=True) as db:
                        if change == "role":
                            db.execute("DELETE FROM admin_roles WHERE user_id=?", (user.id,))
                        elif change == "username":
                            db.execute("UPDATE auth_users SET username=? WHERE id=?", (user.username + "-changed", user.id))
                        elif change == "disabled":
                            db.execute("UPDATE auth_users SET disabled=1 WHERE id=?", (user.id,))
                        else:
                            db.execute("UPDATE auth_users SET password_hash=? WHERE id=?", (replacement, user.id))
                    return result
                paused_hasher = SimpleNamespace(verify=verified_then_changed, hash=self.ids.hasher.hash,
                                               check_needs_rehash=self.ids.hasher.check_needs_rehash)
                with patch.object(self.ids, "hasher", paused_hasher):
                    response = self.login(user.username)
                self.assertEqual(response.status_code, 403 if change == "role" else 401, response.text)
                self.assertNotIn("set-cookie", response.headers)
                self.assertEqual(self.sessions(), before)
                self.assert_private(response, replacement)

    def test_original_login_reports_current_server_role_without_granting_member_access(self):
        for name, expected in (("member", False), ("admin", True)):
            response = self.client.post("/api/auth/login", headers={"Origin": self.origin, "X-Admin": "true"},
                                        params={"user_id": self.users["admin"].id}, json={
                                            "username": self.users[name].username, "password": self.password})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIs(response.json()["is_admin"], expected)
            token = response.cookies[SESSION_COOKIE]
            headers = {"Cookie": f"{SESSION_COOKIE}={token}"}
            self.assert_private(response, token)
            session = self.client.get("/api/auth/session", headers=headers)
            self.assertEqual(session.status_code, 200, session.text)
            self.assertIs(session.json()["is_admin"], expected)
            management = self.client.get("/api/admin/session", headers=headers)
            self.assertEqual(management.status_code, 200 if expected else 403, management.text)
    def test_role_revocation_blocks_management_but_allows_secure_logout(self):
        response = self.login()
        self.assertEqual(response.status_code, 200, response.text)
        token = response.cookies[SESSION_COOKIE]
        headers = {"Cookie": f"{SESSION_COOKIE}={token}", "Origin": self.origin,
                   "X-CSRF-Token": csrf_for_session(token)}
        self.admins.set_role(self.users["member"].username, True)
        self.admins.set_role(self.users["admin"].username, False)
        for path in ("/api/admin/session", "/api/admin/storage", "/api/admin/users"):
            response = self.client.get(path, headers=headers)
            self.assertEqual(response.status_code, 403, response.text)
            self.assert_private(response, token)
        for rejected in ({"Cookie": headers["Cookie"], "Origin": self.origin},
                         {**headers, "Origin": "https://outside.example"}):
            response = self.client.post(self.logout_path, headers=rejected)
            self.assertEqual(response.status_code, 403, response.text)
            self.assertIsNotNone(self.ids.session_user(token))
        response = self.client.post(self.logout_path, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"ok": True})
        self.assertIn("max-age=0", response.headers["set-cookie"].lower())
        self.assertIsNone(self.ids.session_user(token))
        self.assert_private(response, token)
        response = self.client.post(self.logout_path, headers=headers)
        self.assertEqual(response.status_code, 401, response.text)


if __name__ == "__main__":
    unittest.main()
