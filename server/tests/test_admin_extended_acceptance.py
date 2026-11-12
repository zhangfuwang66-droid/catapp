"""按公开契约独立验收设备 CRUD、首页和备份；全部使用临时数据库。"""

import asyncio
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from database_backups import BackupSettings, DatabaseBackups
import test_admin_round2_acceptance as fixture


class ExtendedFixture(unittest.TestCase):
    setUp = fixture.AdminRound2AcceptanceTests.setUp
    tearDown = fixture.AdminRound2AcceptanceTests.tearDown
    request = fixture.AdminRound2AcceptanceTests.request
    borrow = fixture.AdminRound2AcceptanceTests.borrow
    seed_history = fixture.AdminRound2AcceptanceTests.seed_history
    assert_private_history = fixture.AdminRound2AcceptanceTests.assert_private_history

    def register(self, device_id="deer-independent"):
        response = self.request("POST", "/api/admin/devices", actor="owner", json={
            "id": device_id, "display_name": "模拟登记", "confirm_device_id": device_id})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertTrue(response.json()["credential_once"])
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        return response.json()["credential"]

    def write_device(self, device_id, action, **body):
        method = "DELETE" if action == "delete" else "PATCH" if action == "rename" else "POST"
        path = "/api/admin/devices/" + device_id + ("" if action in ("rename", "delete") else "/" + action)
        return self.request(method, path, actor="owner", json={"confirm_device_id": device_id, **body})

    def get(self, path):
        response = self.request("GET", "/api/admin/" + path, actor="owner")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        return response

    def epoch(self, device_id):
        with closing(sqlite3.connect(self.database)) as db:
            return db.execute("SELECT access_epoch FROM devices WHERE id=?", (device_id,)).fetchone()[0]


class IndependentDeviceCrudTests(ExtendedFixture):
    def test_create_read_update_and_delete_all_prefixes_preserve_credential_boundaries(self):
        for device_id, kind in (("cat-independent", "cat"), ("deer-independent", "deer"),
                                ("other-independent", "unknown")):
            with self.subTest(device_id=device_id):
                credential = self.register(device_id)
                token = credential["token"]
                before = self.epoch(device_id)
                response = self.write_device(device_id, "rename", display_name="  另一类型名称 <b>🙂 ' % _  ")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.epoch(device_id), before)
                detail = self.get("devices/" + device_id).json()
                self.assertEqual(detail["device"]["device_type"], kind)
                self.assertEqual(detail["device"]["display_name"], "另一类型名称 <b>🙂 ' % _")
                self.assertIsNone(detail["device"]["owner_user_id"])
                self.assertEqual(detail["credentials"]["active_count"], 1)
                self.assertFalse(detail["can_delete"])
                self.assertEqual(self.write_device(device_id, "delete").status_code, 409)
                self.assertIsNotNone(self.identities.credential_device(device_id, token))
                for endpoint in ("devices", "devices/" + device_id, "audit", "dashboard"):
                    response = self.get(endpoint)
                    for secret in (token, "token_hash", "ciphertext", "password_hash"):
                        self.assertNotIn(secret, response.text)
                self.assertEqual(self.write_device(device_id, "revoke").status_code, 200)
                self.assertTrue(self.get("devices/" + device_id).json()["can_delete"])
                self.assertEqual(self.write_device(device_id, "delete").status_code, 200)
                self.assertEqual(self.request("GET", "/api/admin/devices/" + device_id, actor="owner").status_code, 404)
                self.assertIsNone(self.identities.credential_device(device_id, token))
                audit = self.get("audit?q=" + device_id).json()["items"]
                self.assertTrue(any(row["action"] == "delete-device" and row["result"] == "success" for row in audit))
        self.assertEqual(self.app.state.devices.devices, {})

    def test_concurrent_admin_registration_issues_one_credential(self):
        self.app.state.admin_store.set_role("alice", True)
        device_id = "deer-double-register"

        async def submit():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="https://cat.example") as client:
                return await asyncio.wait_for(asyncio.gather(*(
                    client.post("/api/admin/devices", headers=self.headers[name], json={
                        "id": device_id, "display_name": name, "confirm_device_id": device_id})
                    for name in ("owner", "alice"))), timeout=8)

        responses = self.client.portal.call(submit)
        self.assertEqual(sorted(row.status_code for row in responses), [201, 409], [r.text for r in responses])
        detail = self.get("devices/" + device_id).json()
        self.assertEqual(detail["credentials"]["active_count"], 1)
        self.assertEqual(detail["credentials"]["revoked_count"], 0)
        self.assertEqual(self.get("devices?q=" + device_id).json()["total"], 1)

    def test_all_new_writes_require_admin_origin_csrf_and_confirmation(self):
        credential = self.register()
        device_id = credential["device_id"]
        operations = (("POST", "/api/admin/devices", {"id": "cat-denied", "display_name": "拒绝", "confirm_device_id": "cat-denied"}),
                      ("PATCH", f"/api/admin/devices/{device_id}", {"display_name": "拒绝", "confirm_device_id": device_id}),
                      ("POST", f"/api/admin/devices/{device_id}/rotate-credential", {"confirm_device_id": device_id}),
                      ("POST", f"/api/admin/devices/{device_id}/revoke", {"confirm_device_id": device_id}),
                      ("DELETE", f"/api/admin/devices/{device_id}", {"confirm_device_id": device_id}))
        epoch = self.epoch(device_id)
        for method, path, body in operations:
            for headers, expected in (({}, 401), (self.headers["bob"], 403),
                                      ({"Authorization": "Bearer " + credential["token"]}, 401),
                                      ({k: v for k, v in self.headers["owner"].items() if k != "X-CSRF-Token"}, 403),
                                      ({**self.headers["owner"], "Origin": "https://other.example"}, 403)):
                with self.subTest(method=method, path=path, expected=expected):
                    response = self.request(method, path, headers=headers, json=body)
                    self.assertEqual(response.status_code, expected, response.text)
            response = self.request(method, path, actor="owner", json={**body, "confirm_device_id": "wrong"})
            self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.epoch(device_id), epoch)
        self.assertIsNotNone(self.identities.credential_device(device_id, credential["token"]))
        self.assertEqual(self.get("devices?q=cat-denied").json()["total"], 0)

    def test_invalid_registration_and_unknown_details_are_rejected_without_echoing_input(self):
        body = {"id": "cat-invalid", "display_name": "登记", "confirm_device_id": "cat-invalid"}
        for changes in ({"id": "../outside"}, {"id": "x" * 65}, {"display_name": "   "},
                        {"display_name": "a\nb"}, {"display_name": "x" * 81}, {"token": "private-rejected-input"}):
            response = self.request("POST", "/api/admin/devices", actor="owner", json={**body, **changes})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertNotIn("private-rejected-input", response.text)
        self.assertEqual(self.get("devices?q=cat-invalid").json()["total"], 0)
        self.assertEqual(self.request("GET", "/api/admin/devices/missing-independent", actor="owner").status_code, 404)
        for action in ("rename", "revoke", "rotate-credential", "delete"):
            response = self.write_device("missing-independent", action, **({"display_name": "新名"} if action == "rename" else {}))
            self.assertEqual(response.status_code, 404, response.text)

    def test_borrowed_revoke_preserves_both_histories_and_cannot_delete(self):
        self.borrow()
        self.seed_history()
        response = self.write_device("cat-alice", "rotate-credential")
        self.assertEqual(response.status_code, 409, response.text)
        response = self.write_device("cat-alice", "revoke")
        self.assertEqual(response.status_code, 200, response.text)
        epoch = self.epoch("cat-alice")
        detail = self.get("devices/cat-alice").json()
        self.assertTrue(detail["device"]["revoked"])
        self.assertFalse(detail["device"]["loan_active"])
        self.assertIsNone(detail["device"]["active_user_id"])
        self.assertEqual(detail["history"]["memberships"], 2)
        self.assertEqual(detail["history"]["loans"], 1)
        self.assertEqual(detail["history"]["conversations"], 2)
        self.assertFalse(detail["can_delete"])
        self.assertTrue(detail["delete_blockers"])
        for name in ("alice", "bob"):
            self.assertNotIn("private-round2-" + name, str(detail))
        self.assertIsNone(self.identities.credential_device("cat-alice", self.device_headers["Authorization"].split()[1]))
        self.assertEqual(self.write_device("cat-alice", "revoke").status_code, 200)
        self.assertEqual(self.epoch("cat-alice"), epoch)
        self.assertEqual(self.write_device("cat-alice", "delete").status_code, 409)
        self.assert_private_history()
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM device_loans WHERE ended_at IS NULL").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM device_memberships WHERE ended_at IS NULL").fetchone()[0], 0)

    def test_rotation_revokes_old_token_pending_pairing_and_reenables_revoked_record(self):
        credential = self.register()
        device_id = credential["device_id"]
        invitation = self.identities.request_pairing(device_id, credential["token"])
        self.assertEqual(self.write_device(device_id, "revoke").status_code, 200)
        response = self.write_device(device_id, "rotate-credential")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["credential_once"])
        new = response.json()["credential"]
        self.assertNotEqual(new["token"], credential["token"])
        self.assertIsNone(self.identities.credential_device(device_id, credential["token"]))
        self.assertIsNotNone(self.identities.credential_device(device_id, new["token"]))
        with self.assertRaises(ValueError):
            self.identities.claim_pairing(invitation.code, self.users["bob"].id, client_key="independent")
        detail = self.get("devices/" + device_id).json()
        self.assertFalse(detail["device"]["revoked"])
        self.assertEqual(detail["pairings"]["active_count"], 0)
        self.assertEqual(detail["credentials"]["active_count"], 1)
        self.assertNotIn(new["token"], str(detail))

    def test_user_revoked_bound_device_recovers_with_new_usb_credential_and_private_history(self):
        self.borrow()
        self.seed_history()
        self.identities.save_voice_preference(self.users['alice'].id, 'cat-alice', False)
        self.identities.save_voice_preference(self.users['bob'].id, 'cat-alice', False)
        before = self.epoch('cat-alice')
        response = self.request('POST', '/api/devices/cat-alice/revoke', actor='alice')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['requires_usb_provisioning'])
        detail = self.get('devices/cat-alice').json()
        self.assertEqual(detail['device']['owner_user_id'], self.users['alice'].id)
        self.assertEqual(detail['lifecycle']['state'], 'revoked')
        self.assertTrue(detail['lifecycle']['can_rotate_credential'])
        self.assertFalse(detail['lifecycle']['can_unbind'])
        self.assertTrue(detail['lifecycle']['recovery_requires_usb'])
        self.assertFalse(detail['lifecycle']['pairing_available'])
        self.assertEqual(self.request('POST', '/api/devices/cat-alice/pairing',
                                      headers=self.device_headers).status_code, 401)
        response = self.write_device('cat-alice', 'rotate-credential')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['requires_usb_provisioning'])
        self.assertTrue(response.json()['binding_cleared'])
        new = response.json()['credential']['token']
        self.assertNotEqual(new, self.device_headers['Authorization'].split()[1])
        self.assertGreater(self.epoch('cat-alice'), before)
        self.assertIsNone(self.identities.credential_device(
            'cat-alice', self.device_headers['Authorization'].split()[1]))
        detail = self.get('devices/cat-alice').json()
        self.assertEqual(detail['lifecycle']['state'], 'unbound')
        self.assertTrue(detail['lifecycle']['pairing_available'])
        self.assertIsNone(detail['device']['owner_user_id'])
        self.assertFalse(detail['device']['loan_active'])
        self.assertFalse(detail['can_delete'])
        for name in ('alice', 'bob'):
            self.assertTrue(self.identities.voice_preference(self.users[name].id, 'cat-alice'))
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM device_memberships WHERE ended_at IS NULL').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM device_loans WHERE ended_at IS NULL').fetchone()[0], 0)
        new_headers = {'Authorization': 'Bearer ' + new}
        invitation = self.request('POST', '/api/devices/cat-alice/pairing', headers=new_headers)
        self.assertEqual(invitation.status_code, 200, invitation.text)
        bound = self.request('POST', '/api/devices/pair', actor='bob', json={'code': invitation.json()['code']})
        self.assertEqual(bound.status_code, 200, bound.text)
        self.assertIsNone(self.identities.accessible_device('cat-alice', self.users['alice'].id))
        self.assertEqual(self.identities.accessible_device('cat-alice', self.users['bob'].id)['owner_user_id'], self.users['bob'].id)
        self.assertTrue(self.identities.voice_preference(self.users['bob'].id, 'cat-alice'))
        self.assert_private_history()
        self.assertEqual(self.request('POST', '/api/devices/cat-alice/pairing',
                                      headers=self.device_headers).status_code, 401)

    def test_lifecycle_distinguishes_transfer_from_credential_revocation(self):
        lifecycle = self.get('devices/cat-alice').json()['lifecycle']
        self.assertEqual(lifecycle['state'], 'bound')
        self.assertTrue(lifecycle['can_unbind'])
        self.assertFalse(lifecycle['can_rotate_credential'])
        self.assertFalse(lifecycle['pairing_available'])
        self.assertFalse(lifecycle['recovery_requires_usb'])
        self.borrow()
        self.assertEqual(self.get('devices/cat-alice').json()['lifecycle']['state'], 'borrowed')
        response = self.request('POST', '/api/devices/cat-alice/unbind', actor='alice',
                                json={'confirm_device_id': 'cat-alice'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['requires_usb_provisioning'])
        lifecycle = self.get('devices/cat-alice').json()['lifecycle']
        self.assertEqual(lifecycle['state'], 'unbound')
        self.assertTrue(lifecycle['pairing_available'])
        self.assertFalse(lifecycle['can_unbind'])
        self.assertTrue(lifecycle['can_rotate_credential'])
        self.assertFalse(lifecycle['recovery_requires_usb'])
        self.assertEqual(self.request('POST', '/api/devices/cat-alice/pairing',
                                      headers=self.device_headers).status_code, 200)

    def test_delete_empty_device_then_register_same_chip_id_never_revives_old_credential(self):
        credential = self.register('cat-same-chip')
        self.assertEqual(self.write_device('cat-same-chip', 'revoke').status_code, 200)
        deleted = self.write_device('cat-same-chip', 'delete')
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertTrue(deleted.json()['requires_usb_provisioning'])
        replacement = self.register('cat-same-chip')
        self.assertNotEqual(credential['token'], replacement['token'])
        self.assertEqual(self.request('POST', '/api/devices/cat-same-chip/pairing', headers={
            'Authorization': 'Bearer ' + credential['token']}).status_code, 401)
        response = self.request('POST', '/api/devices/cat-same-chip/pairing', headers={
            'Authorization': 'Bearer ' + replacement['token']})
        self.assertEqual(response.status_code, 200, response.text)

    def test_lost_rotation_response_does_not_expose_token_and_allows_manual_recovery(self):
        credential = self.register()
        device_id = credential["device_id"]
        with patch.object(self.app.state.connections, "revoke_device", new=AsyncMock(side_effect=OSError("private-cleanup-path"))):
            response = self.write_device(device_id, "rotate-credential")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertNotIn("private-cleanup-path", response.text)
        self.assertNotIn("credential", response.json())
        self.assertIsNone(self.identities.credential_device(device_id, credential["token"]))
        epoch = self.epoch(device_id)
        detail = self.get("devices/" + device_id).json()
        self.assertEqual(detail["credentials"]["active_count"], 1)
        self.get("dashboard")
        self.get("audit?q=" + device_id)
        self.assertEqual(self.epoch(device_id), epoch)
        response = self.write_device(device_id, "rotate-credential")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNotNone(self.identities.credential_device(device_id, response.json()["credential"]["token"]))
        audit = self.get("audit?q=" + device_id).json()["items"]
        self.assertTrue(any(row["action"] == "rotate-device-credential" and row["result"] == "started" for row in audit))


class IndependentDashboardTests(ExtendedFixture):
    def test_utc_calendar_empty_days_and_recent_audit_are_real_counts(self):
        today = datetime.now(timezone.utc).replace(hour=0, minute=5, second=0, microsecond=0)
        now = today.timestamp()
        first = today.replace(minute=0) - timedelta(days=6)
        self.identities.clock = lambda: now
        with self.identities._transaction(write=True) as db:
            for table in ("auth_users", "devices", "admin_audit"):
                db.execute("UPDATE " + table + " SET created_at=?", (now - 20 * 86400,))
            db.execute("UPDATE auth_users SET created_at=? WHERE id=?", (first.timestamp(), self.users["alice"].id))
            db.execute("UPDATE auth_users SET created_at=? WHERE id=?", (now, self.users["owner"].id))
            db.execute("UPDATE auth_users SET created_at=? WHERE id=?", (now + 3600, self.users["bob"].id))
            db.execute("UPDATE devices SET created_at=?", (today.replace(hour=0, minute=0).timestamp() - 1,))
            for index in range(10):
                db.execute("""INSERT INTO admin_audit(actor_id,actor_username,target_type,target_id,action,result,created_at)
                              VALUES(?,?,'user',?,'acceptance-check','success',?)""",
                           (self.users["owner"].id, "owner", self.users["bob"].id, now - index))
        data = self.get("dashboard").json()
        self.assertEqual(data["generated_at"], now)
        self.assertEqual([day["date"] for day in data["activity"]],
                         [(first + timedelta(days=index)).strftime("%Y-%m-%d") for index in range(7)])
        self.assertEqual([day["users"] for day in data["activity"]], [1, 0, 0, 0, 0, 0, 1])
        self.assertEqual([day["devices"] for day in data["activity"]], [0, 0, 0, 0, 0, 1, 0])
        self.assertEqual([day["actions"] for day in data["activity"]], [0, 0, 0, 0, 0, 0, 10])
        self.assertEqual(len(data["recent_audit"]), 8)
        self.assertEqual([row["created_at"] for row in data["recent_audit"]], [now - index for index in range(8)])
        self.assertIsNone(data["summary"]["model_billing"])
        self.assertEqual(data["summary"]["devices"]["online"], 0)

    def test_metadata_is_private_and_disk_failure_is_unknown(self):
        conversation = self.app.state.memory_manager.store.create_conversation(self.users["alice"].id, "cat-alice")
        self.app.state.memory_manager.store.append_message(conversation, "user", "private-dashboard-body")
        with patch("admin_dashboard.shutil.disk_usage", side_effect=OSError("private-disk-path")):
            response = self.get("dashboard")
        data = response.json()
        self.assertEqual(data["system"]["disk_status"], "unknown")
        self.assertIsNone(data["system"]["disk_total_bytes"])
        self.assertIsNone(data["system"]["disk_free_bytes"])
        self.assertTrue(any(row["code"] == "disk_unknown" for row in data["attention"]))
        for secret in ("private-dashboard-body", "private-disk-path", str(self.database),
                       "password_hash", "token_hash", "ciphertext", self.device_headers["Authorization"].split()[1]):
            self.assertNotIn(secret, response.text)
        for path in ("dashboard", "devices/cat-alice"):
            for headers, status in (({}, 401), (self.headers["bob"], 403), (self.device_headers, 401)):
                self.assertEqual(self.request("GET", "/api/admin/" + path, headers=headers).status_code, status)
        self.assertEqual(self.app.state.devices.devices, {})


class IndependentBackupTests(ExtendedFixture):
    def test_unconfigured_backup_is_explicit_and_writes_reject_extra_directory(self):
        response = self.request("POST", "/api/admin/backups/run", actor="owner", json={"confirm": "检查并备份"})
        self.assertEqual(response.status_code, 409, response.text)
        history = self.get("backups/history").json()
        self.assertEqual(history["items"], [])
        self.assertFalse(history["includes_provider_key"])
        for body in ({}, {"confirm": "wrong"}, {"confirm": "检查并备份", "directory": "outside"}):
            response = self.request("POST", "/api/admin/backups/run", actor="owner", json=body)
            self.assertEqual(response.status_code, 422, response.text)
        for headers, status in (({}, 401), (self.headers["alice"], 403), (self.device_headers, 401),
                                ({k: v for k, v in self.headers["owner"].items() if k != "X-CSRF-Token"}, 403)):
            response = self.request("POST", "/api/admin/backups/run", headers=headers, json={"confirm": "检查并备份"})
            self.assertEqual(response.status_code, status, response.text)

    def test_manual_check_creates_once_and_history_only_returns_verified_metadata(self):
        directory = Path(self.temp.name) / "only-temporary-backups"
        directory.mkdir()
        (directory / "unverified.sqlite3").write_bytes(b"not-a-valid-backup")
        service = DatabaseBackups(self.database, BackupSettings(directory=directory, min_free_bytes=0))
        previous = self.app.state.database_backups
        self.app.state.database_backups = service
        try:
            results = [self.request("POST", "/api/admin/backups/run", actor="owner",
                                    json={"confirm": "检查并备份"}) for _ in range(2)]
            self.assertEqual([r.status_code for r in results], [200, 200], [r.text for r in results])
            self.assertEqual([r.json()["created"] for r in results], [True, False])
            self.assertIn("近期备份", results[1].json()["message"])
            history_response = self.get("backups/history")
            history = history_response.json()
            self.assertEqual(history["total"], 1)
            self.assertEqual(len(history["items"]), 1)
            self.assertEqual(set(history["items"][0]), {"created_at", "bytes", "verified"})
            self.assertGreater(history["items"][0]["bytes"], 0)
            self.assertTrue(history["items"][0]["verified"])
            self.assertFalse(history["includes_provider_key"])
            self.assertFalse(history["status"]["running"])
            self.assertFalse(history["status"]["scheduler_running"])
            for marker in (str(directory), "unverified.sqlite3", "receipt", "token_hash"):
                self.assertNotIn(marker, history_response.text)
        finally:
            self.client.portal.call(service.close)
            self.app.state.database_backups = previous


if __name__ == "__main__":
    unittest.main()
