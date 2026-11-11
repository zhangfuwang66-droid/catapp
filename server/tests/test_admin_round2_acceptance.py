"""第二轮独立 HTTP 验收；只使用临时身份、离线登记和模拟历史。"""

import asyncio
from contextlib import closing
import sqlite3
import threading
import unittest
from unittest.mock import patch

import httpx

from connection_authorization import DeviceAuthorization
import test_admin_acceptance as fixture


class AdminRound2AcceptanceTests(unittest.TestCase):
    tearDown = fixture.AdminAcceptanceTests.tearDown
    request = fixture.AdminAcceptanceTests.request

    def setUp(self):
        fixture.AdminAcceptanceTests.setUp(self)
        self.app.state.admin_store.set_role("owner", True)
        for method in ("get", "resolve"):
            self.stack.enter_context(patch.object(
                self.app.state.devices, method,
                side_effect=AssertionError("离线验收不应创建设备运行时")))
        self.stack.enter_context(patch.object(
            self.app.state.providers, "key",
            side_effect=AssertionError("离线管理验收不应读取模型凭证")))

    def row(self, device_id="cat-alice"):
        response = self.request("GET", "/api/admin/devices?q=" + device_id, actor="owner")
        self.assertEqual(response.status_code, 200, response.text)
        return next(row for row in response.json()["items"] if row["id"] == device_id)

    def borrow(self):
        response = self.request("POST", "/api/devices/cat-alice/loan", actor="alice",
                                json={"confirm_device_id": "cat-alice", "hours": 1})
        self.assertEqual(response.status_code, 200, response.text)
        response = self.request("POST", "/api/devices/borrow", actor="bob",
                                json={"code": response.json()["code"]})
        self.assertEqual(response.status_code, 200, response.text)

    def seed_history(self):
        store = self.app.state.memory_manager.store
        for name in ("alice", "bob"):
            conversation = store.create_conversation(self.users[name].id, "cat-alice")
            store.append_message(conversation, "user", "private-round2-" + name)

    def assert_private_history(self):
        for name, other in (("alice", "bob"), ("bob", "alice")):
            response = self.request("GET", "/api/account/memories/cat-alice", actor=name)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("private-round2-" + name, response.text)
            self.assertNotIn("private-round2-" + other, response.text)

    def authorization(self, name):
        row = self.identities.accessible_device("cat-alice", self.users[name].id)
        return DeviceAuthorization("device", self.users[name].id, "cat-alice",
                                   self.device_headers["Authorization"].split()[1],
                                   row["access_epoch"])

    def test_two_administrators_cross_disable_through_http_keeps_one_enabled(self):
        self.app.state.admin_store.set_role("alice", True)
        # 两个 HTTP 请求都通过入口鉴权并读取目标，再竞争管理写锁。
        both_admitted = threading.Barrier(2)
        original_user = self.app.state.admin_store.user

        def read_target(user_id):
            result = original_user(user_id)
            both_admitted.wait(timeout=5)
            return result

        async def race():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                         base_url="https://cat.example") as client:
                return await asyncio.wait_for(asyncio.gather(*(
                    client.post(f"/api/admin/users/{self.users[target].id}/status",
                                headers=self.headers[actor],
                                json={"disabled": True, "confirm_username": target})
                    for actor, target in (("owner", "alice"), ("alice", "owner"))
                )), timeout=12)

        with patch.object(self.app.state.admin_store, "user", side_effect=read_target):
            responses = self.client.portal.call(race)
        self.assertEqual(sum(r.status_code == 200 for r in responses), 1,
                         [r.text for r in responses])
        self.assertTrue(all(r.status_code in (200, 401, 403) for r in responses),
                        [r.text for r in responses])
        winner, loser = ("owner", "alice") if responses[0].status_code == 200 else ("alice", "owner")
        roles = self.app.state.admin_store.roles()
        self.assertEqual([r["username"] for r in roles if not r["disabled"]], [winner])
        self.assertEqual(self.request("GET", "/api/admin/overview", actor=winner).status_code, 200)
        self.assertEqual(self.request("GET", "/api/admin/overview", actor=loser).status_code, 401)
        rejected = self.request("POST", f"/api/admin/users/{self.users['bob'].id}/status",
                                actor=loser, json={"disabled": True, "confirm_username": "bob"})
        self.assertEqual(rejected.status_code, 401, rejected.text)
        self.assertFalse(self.app.state.admin_store.user(self.users["bob"].id)["disabled"])
        audit = self.request("GET", "/api/admin/audit", actor=winner).json()["items"]
        writes = [row for row in audit if row["action"] == "disable-user"]
        self.assertEqual(len(writes), 1)
        self.assertEqual((writes[0]["actor_username"], writes[0]["target_id"], writes[0]["result"]),
                         (winner, self.users[loser].id, "success"))

    def test_admin_voice_targets_guest_and_return_restores_owner_preference(self):
        self.identities.save_voice_preference(self.users["alice"].id, "cat-alice", True)
        self.borrow()
        self.seed_history()
        guest = self.authorization("bob")
        self.assertTrue(self.client.portal.call(guest.is_valid, self.identities))
        for paused in (True, False):
            response = self.request("POST", "/api/admin/devices/cat-alice/voice", actor="owner",
                                    json={"paused": paused, "confirm_device_id": "cat-alice"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.identities.voice_preference(self.users["bob"].id, "cat-alice"), paused)
            self.assertTrue(self.identities.voice_preference(self.users["alice"].id, "cat-alice"))
            row = self.row()
            self.assertEqual((row["owner_user_id"], row["active_user_id"], row["voice_paused"]),
                             (self.users["alice"].id, self.users["bob"].id, paused))
        self.assert_private_history()
        response = self.request("POST", "/api/devices/cat-alice/loan/end", actor="bob",
                                json={"confirm_device_id": "cat-alice"})
        self.assertEqual(response.status_code, 200, response.text)
        row = self.row()
        self.assertEqual(row["active_user_id"], self.users["alice"].id)
        self.assertFalse(row["loan_active"])
        self.assertTrue(row["voice_paused"])
        self.assertIsNone(self.identities.accessible_device("cat-alice", self.users["bob"].id))
        self.assertFalse(self.client.portal.call(guest.is_valid, self.identities))
        self.assert_private_history()
        self.assertEqual(self.app.state.devices.devices, {})

    def test_admin_unbind_ends_active_loan_and_keeps_both_private_histories(self):
        old_owner = self.authorization("alice")
        self.borrow()
        self.seed_history()
        old_guest = self.authorization("bob")
        response = self.request("POST", "/api/admin/devices/cat-alice/unbind", actor="owner",
                                json={"confirm_device_id": "cat-alice"})
        self.assertEqual(response.status_code, 200, response.text)
        binding = self.request("GET", "/api/devices/cat-alice/binding", headers=self.device_headers)
        self.assertEqual(binding.status_code, 200, binding.text)
        self.assertFalse(binding.json()["bound"])
        for authorization in (old_owner, old_guest):
            self.assertFalse(self.client.portal.call(authorization.is_valid, self.identities))
        row = self.row()
        self.assertIsNone(row["owner_user_id"])
        self.assertIsNone(row["active_user_id"])
        self.assertFalse(row["loan_active"])
        self.assertFalse(row["loan_pending"])
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM device_loans WHERE ended_at IS NULL").fetchone()[0], 0)
            memberships = db.execute("SELECT user_id,ended_at FROM device_memberships WHERE device_id='cat-alice'").fetchall()
        self.assertEqual({row[0] for row in memberships}, {self.users[n].id for n in ("alice", "bob")})
        self.assertTrue(all(row[1] is not None for row in memberships))
        self.assert_private_history()
        self.assertEqual(self.app.state.devices.devices, {})

    def test_search_is_literal_and_filtered_pagination_is_stable(self):
        names = ["cat-round2-%02d" % number for number in range(7)]
        for number, device_id in enumerate(names):
            self.identities.register_device(device_id, "分页样本 ' % _ <b>🙂" if number == 0 else "分页样本")
        pages = []
        for page in (1, 2, 3):
            response = self.request("GET",
                f"/api/admin/devices?q=CAT-ROUND2-&status=unbound&page={page}&page_size=3", actor="owner")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["total"], 7)
            pages.extend(row["id"] for row in response.json()["items"])
        self.assertEqual(len(pages), len(set(pages)))
        self.assertEqual(set(pages), set(names))
        for literal in ("%27%20OR%201%3D1--", "not-a-device"):
            response = self.request("GET", "/api/admin/devices?q=" + literal, actor="owner")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["total"], 0)
        for literal in ("%25", "_%20%3Cb%3E%F0%9F%99%82"):
            response = self.request("GET", "/api/admin/devices?q=" + literal, actor="owner")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual([r["id"] for r in response.json()["items"]], [names[0]])
        response = self.request("GET", "/api/admin/devices?q=round2-&page=999", actor="owner")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 7)
        self.assertEqual(response.json()["items"], [])

    def test_invalid_queries_and_writes_do_not_change_binding_or_history(self):
        self.seed_history()
        before = self.authorization("alice")
        for resource in ("users", "devices", "audit"):
            for query in ("page=0", "page=-1", "page=abc", "page_size=0", "page_size=101", "q=" + "x" * 81):
                with self.subTest(resource=resource, query=query):
                    response = self.request("GET", f"/api/admin/{resource}?{query}", actor="owner")
                    self.assertEqual(response.status_code, 422, response.text)
        path = "/api/admin/devices/cat-alice/unbind"
        body = {"confirm_device_id": "cat-alice"}
        for headers, status in (({}, 401), (self.headers["alice"], 403), (self.device_headers, 401),
                                ({k: v for k, v in self.headers["owner"].items() if k != "X-CSRF-Token"}, 403),
                                ({**self.headers["owner"], "Origin": "https://other.example"}, 403)):
            response = self.request("POST", path, headers=headers, json=body)
            self.assertEqual(response.status_code, status, response.text)
        for bad_body, status in (({}, 422), ({"confirm_device_id": "wrong"}, 409)):
            response = self.request("POST", path, actor="owner", json=bad_body)
            self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(self.row()["owner_user_id"], self.users["alice"].id)
        self.assertTrue(self.client.portal.call(before.is_valid, self.identities))
        response = self.request("GET", "/api/account/memories/cat-alice", actor="alice")
        self.assertIn("private-round2-alice", response.text)
        self.assertNotIn("private-round2-bob", response.text)

    def test_unbound_registration_reads_are_private_and_do_not_create_runtime_or_preferences(self):
        device_id = "deer-round2"
        credential = self.identities.register_device(device_id, "未绑定离线验收登记")
        paths = ("/api/admin/overview", "/api/admin/devices?q=" + device_id,
                 "/api/admin/devices?status=unbound", "/api/admin/devices?status=offline")
        with closing(sqlite3.connect(self.database)) as db:
            preferences_before = db.execute("SELECT * FROM device_preferences ORDER BY user_id,device_id").fetchall()
            epoch_before = db.execute("SELECT access_epoch FROM devices WHERE id=?", (device_id,)).fetchone()[0]
        for path in paths:
            for headers, status in (({}, 401), (self.headers["alice"], 403),
                                    ({"Authorization": "Bearer " + credential.token}, 401)):
                response = self.request("GET", path, headers=headers)
                self.assertEqual(response.status_code, status, response.text)
            response = self.request("GET", path, actor="owner")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers.get("cache-control"), "no-store")
            self.assertNotIn(credential.token, response.text)
        row = self.row(device_id)
        for field in ("owner_user_id", "owner_username", "active_user_id", "active_username", "voice_paused"):
            self.assertIsNone(row[field], field)
        self.assertFalse(row["online"])
        self.assertFalse(row["loan_active"])
        response = self.request("POST", f"/api/admin/devices/{device_id}/voice", actor="owner",
                                json={"paused": False, "confirm_device_id": device_id})
        self.assertEqual(response.status_code, 409, response.text)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT * FROM device_preferences ORDER BY user_id,device_id").fetchall(), preferences_before)
            self.assertEqual(db.execute("SELECT access_epoch FROM devices WHERE id=?", (device_id,)).fetchone()[0], epoch_before)
        self.assertEqual(self.app.state.devices.devices, {})


if __name__ == "__main__":
    unittest.main()
