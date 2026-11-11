"""账号契约独立验收；网页不提供提权、密码重置或凭证读取。"""

import unittest

from access_control import SESSION_COOKIE
from identity_store import csrf_for_session
from knowledge_store import split_text
from test_admin_extended_acceptance import ExtendedFixture


class IndependentAccountCrudTests(ExtendedFixture):
    def create(self, name="independent-user", username=None):
        response = self.request("POST", "/api/admin/users", actor="owner", json={
            "username": username or name, "password": "simulated-independent-password",
            "confirm_username": name})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        self.assertNotIn("simulated-independent-password", response.text)
        user = response.json()["user"]
        self.assertEqual(set(user), {"id", "username", "created_at", "disabled", "is_admin", "key_configured"})
        self.assertEqual(user["username"], name)
        self.assertFalse(user["disabled"])
        self.assertFalse(user["is_admin"])
        self.assertFalse(user["key_configured"])
        return user

    def user_write(self, user, action, **body):
        method = "PATCH" if action == "rename" else "DELETE" if action == "delete" else "POST"
        suffix = "" if action in ("rename", "delete") else "/" + action
        return self.request(method, "/api/admin/users/" + user["id"] + suffix, actor="owner",
                            json={"confirm_username": user["username"], **body})

    def test_create_is_normalized_ordinary_and_not_automatically_logged_in(self):
        user = self.create("round2.user", "  Ｒｏｕｎｄ２.User  ")
        detail = self.get("users/" + user["id"]).json()
        self.assertEqual(detail["active_sessions"], 0)
        self.assertFalse(detail["deletion"]["allowed"])
        self.assertIn("enabled", detail["deletion"]["blockers"])
        grant = self.identities.login(user["username"], "simulated-independent-password", client_key="independent")
        self.assertIsNotNone(grant)
        response = self.request("GET", "/api/admin/dashboard", headers={"Cookie": f"{SESSION_COOKIE}={grant.token}"})
        self.assertEqual(response.status_code, 403, response.text)
        response = self.request("POST", "/api/admin/users", actor="owner", json={
            "username": "ROUND2.USER", "password": "other-simulated-password", "confirm_username": "round2.user"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.get("users?q=" + user["id"]).json()["total"], 1)

    def test_rename_keeps_id_device_documents_and_memory_but_revokes_old_login(self):
        user = self.create()
        grant = self.identities.login(user["username"], "simulated-independent-password", client_key="old-login")
        credential = self.identities.register_device("deer-account-history", "模拟设备")
        pairing = self.identities.request_pairing(credential.device_id, credential.token)
        self.identities.claim_pairing(pairing.code, user["id"], client_key="independent")
        memory = self.app.state.memory_manager.store
        conversation = memory.create_conversation(user["id"], credential.device_id)
        message = memory.append_message(conversation, "user", "private-account-body")
        memory.save_summary(conversation, message, "private-account-summary", memories=["private-account-fact"])
        documents = self.app.state.knowledge.store
        documents.add_document(user["id"], "private-account-filename.txt", b"private-document-content")
        documents.finish(documents.claim_next(), split_text("private-document-content"))
        before = self.get("users/" + user["id"]).json()
        response = self.user_write(user, "rename", username="renamed-independent")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["sessions_revoked"])
        self.assertEqual(response.json()["user"]["id"], user["id"])
        self.assertIsNone(self.identities.session_user(grant.token))
        self.assertIsNone(self.identities.login(user["username"], "simulated-independent-password", client_key="old-name"))
        new_login = self.identities.login("renamed-independent", "simulated-independent-password", client_key="new-name")
        self.assertEqual(new_login.user.id, user["id"])
        after = self.get("users/" + user["id"]).json()
        self.assertEqual(after["devices"], before["devices"])
        self.assertEqual(after["documents"], before["documents"])
        self.assertEqual(after["memory"], before["memory"])
        self.assertEqual(memory.conversation_owner(conversation)["user_id"], user["id"])
        self.assertIsNotNone(self.identities.credential_device(credential.device_id, credential.token))
        for secret in (grant.token, credential.token, pairing.code, "simulated-independent-password",
                       "private-account-body", "private-account-summary", "private-account-fact",
                       "private-account-filename.txt", "private-document-content", "password_hash", "token_hash", "ciphertext"):
            self.assertNotIn(secret, str(after))
        self.assertEqual(self.app.state.devices.devices, {})

    def test_details_show_owner_and_guest_metadata_without_private_content(self):
        self.borrow()
        self.seed_history()
        owner = self.get("users/" + self.users["alice"].id).json()
        guest = self.get("users/" + self.users["bob"].id).json()
        self.assertEqual([row["id"] for row in owner["devices"]["owned"]], ["cat-alice"])
        self.assertEqual(owner["devices"]["borrowed"], [])
        self.assertEqual([row["id"] for row in guest["devices"]["borrowed"]], ["cat-alice"])
        self.assertEqual(guest["devices"]["owned"], [])
        self.assertEqual(owner["devices"]["history_count"], 1)
        self.assertEqual(guest["devices"]["history_count"], 1)
        self.assertEqual(owner["memory"]["messages"], 1)
        self.assertEqual(guest["memory"]["messages"], 1)
        allowed = {"id", "display_name", "device_type", "owner_user_id", "active_user_id",
                   "revoked", "loan_active", "loan_pending", "loan_expires_at"}
        for detail in (owner, guest):
            for row in detail["devices"]["owned"] + detail["devices"]["borrowed"]:
                self.assertEqual(set(row), allowed)
            for marker in ("private-round2-alice", "private-round2-bob", "alice-test-password",
                           "bob-test-password", self.device_headers["Authorization"].split()[1],
                           "password_hash", "token_hash", "ciphertext"):
                self.assertNotIn(marker, str(detail))

    def test_empty_account_requires_disable_before_delete_and_audit_survives(self):
        user = self.create()
        self.assertEqual(self.user_write(user, "delete").status_code, 409)
        response = self.user_write(user, "status", disabled=True)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(self.get("users/" + user["id"]).json()["deletion"]["allowed"])
        response = self.user_write(user, "delete")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["deleted_user_id"], user["id"])
        self.assertEqual(self.request("GET", "/api/admin/users/" + user["id"], actor="owner").status_code, 404)
        self.assertEqual(self.get("users?q=" + user["id"]).json()["total"], 0)
        audit = self.get("audit?q=" + user["id"]).json()["items"]
        self.assertTrue(any(row["action"] == "delete-user" and row["result"] == "success" for row in audit))

    def test_historical_accounts_are_not_deleted_and_self_admin_is_protected(self):
        self.borrow()
        self.seed_history()
        for name in ("alice", "bob"):
            user = {"id": self.users[name].id, "username": name}
            self.assertEqual(self.user_write(user, "status", disabled=True).status_code, 200)
            detail = self.get("users/" + user["id"]).json()
            self.assertFalse(detail["deletion"]["allowed"])
            self.assertIn("device_history", detail["deletion"]["blockers"])
            self.assertIn("memory", detail["deletion"]["blockers"])
            self.assertEqual(self.user_write(user, "delete").status_code, 409)
        for name in ("alice", "bob"):
            self.assertEqual(self.user_write({"id": self.users[name].id, "username": name}, "status", disabled=False).status_code, 200)
            self.assertEqual(self.request("GET", "/api/auth/session", actor=name).status_code, 401)
            grant = self.identities.login(name, name + "-test-password", client_key="reenabled")
            self.headers[name] = {"Cookie": f"{SESSION_COOKIE}={grant.token}",
                                  "Origin": "https://cat.example", "X-CSRF-Token": csrf_for_session(grant.token)}
        self.assert_private_history()
        own = {"id": self.users["owner"].id, "username": "owner"}
        self.assertEqual(self.user_write(own, "rename", username="renamed-admin").status_code, 409)
        self.assertEqual(self.user_write(own, "status", disabled=True).status_code, 409)
        self.assertEqual(self.user_write(own, "delete").status_code, 409)

    def test_invalid_inputs_and_new_crud_routes_enforce_all_auth_boundaries(self):
        user = self.create()
        create_body = {"username": "new-independent", "password": "simulated-private-password", "confirm_username": "new-independent"}
        operations = (("POST", "/api/admin/users", create_body),
                      ("PATCH", "/api/admin/users/" + user["id"], {"username": "renamed-independent", "confirm_username": user["username"]}),
                      ("DELETE", "/api/admin/users/" + user["id"], {"confirm_username": user["username"]}))
        for method, path, body in operations:
            for headers, status in (({}, 401), (self.headers["alice"], 403), (self.device_headers, 401),
                                    ({k: v for k, v in self.headers["owner"].items() if k != "X-CSRF-Token"}, 403),
                                    ({**self.headers["owner"], "Origin": "https://other.example"}, 403)):
                response = self.request(method, path, headers=headers, json=body)
                self.assertEqual(response.status_code, status, response.text)
            response = self.request(method, path, actor="owner", json={**body, "confirm_username": "wrong"})
            self.assertEqual(response.status_code, 409, response.text)
        for changes in ({"username": "../outside"}, {"username": "x" * 33}, {"password": "short"},
                        {"password": "x" * 129}, {"password": " " * 12}, {"is_admin": True}, {"disabled": True}):
            response = self.request("POST", "/api/admin/users", actor="owner", json={**create_body, **changes})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertNotIn("simulated-private-password", response.text)
        for headers, expected in (({}, 401), (self.headers["bob"], 403), (self.device_headers, 401)):
            self.assertEqual(self.request("GET", "/api/admin/users/" + user["id"], headers=headers).status_code, expected)
        self.assertEqual(self.get("users?q=new-independent").json()["total"], 0)
        self.assertEqual(self.get("users/" + user["id"]).json()["user"]["username"], user["username"])


if __name__ == "__main__":
    unittest.main()
