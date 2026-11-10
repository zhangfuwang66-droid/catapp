"""Final regressions for queued writes, Windows static aliases and committed audit."""

import asyncio
from unittest.mock import AsyncMock, patch
import unittest

import httpx

from provider_routes import lock_for
import test_admin_acceptance as fixture
from tools.memory_database import backup_database, restore_database


class AdminFinalAcceptanceTests(unittest.TestCase):
    tearDown = fixture.AdminAcceptanceTests.tearDown
    request = fixture.AdminAcceptanceTests.request
    admin_cli = fixture.AdminAcceptanceTests.admin_cli

    def setUp(self):
        fixture.AdminAcceptanceTests.setUp(self)
        self.admin_cli("grant", "owner")

    async def wait_until(self, predicate, message):
        async def poll():
            while not predicate():
                await asyncio.sleep(.005)
        try:
            await asyncio.wait_for(poll(), timeout=3)
        except asyncio.TimeoutError:
            self.fail(message)

    def admin_change(self, action):
        if action == "disable":
            return (f"/api/admin/users/{self.users['alice'].id}/status",
                    {"disabled": True, "confirm_username": "alice"})
        return (f"/api/admin/users/{self.users['alice'].id}/revoke-sessions",
                {"confirm_username": "alice"})

    async def queued_speech(self, action):
        user_id = self.users["alice"].id
        before = self.app.state.speech.settings.get(user_id)
        lock = lock_for(self.app, user_id)
        await lock.acquire()
        admin_task = member_task = None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                     base_url="https://cat.example") as client:
            try:
                path, body = self.admin_change(action)
                admin_task = asyncio.create_task(client.post(path, headers=self.headers["owner"], json=body))
                await self.wait_until(lambda: bool(lock._waiters), "admin change did not queue")
                member_task = asyncio.create_task(client.put(
                    "/api/account/speech", headers=self.headers["alice"],
                    json={"mode": "accurate", "language": "en", "hotwords": ["queued change"], "corrections": {}},
                ))
                await self.wait_until(lambda: len(lock._waiters or ()) >= 2, "speech write did not queue behind admin")
                lock.release()
                admin_response, member_response = await asyncio.wait_for(
                    asyncio.gather(admin_task, member_task), timeout=8)
                # A second acquisition waits for any shield child already queued on this lock.
                async with lock:
                    pass
                self.assertEqual(admin_response.status_code, 200, admin_response.text)
                self.assertEqual(member_response.status_code, 401, member_response.text)
                self.assertEqual(self.app.state.speech.settings.get(user_id), before,
                                 "revoked queued speech request changed persisted preferences")
            finally:
                if lock.locked():
                    lock.release()
                for task in (admin_task, member_task):
                    if task and not task.done():
                        task.cancel()
                await asyncio.gather(*(t for t in (admin_task, member_task) if t), return_exceptions=True)

    def test_speech_queued_after_disable_does_not_write(self):
        self.client.portal.call(self.queued_speech, "disable")

    def test_speech_queued_after_session_revoke_does_not_write(self):
        self.client.portal.call(self.queued_speech, "revoke")

    async def queued_memory_clear(self, action):
        user_id = self.users["alice"].id
        store = self.app.state.memory_manager.store
        conversation = store.create_conversation(user_id, "cat-alice")
        marker = "private history must survive revoked queued deletion"
        store.append_message(conversation, "user", marker)
        token = self.headers["alice"]["Cookie"].split("=", 1)[1]
        registry = self.app.state.devices
        lock = registry.lock_for("cat-alice")
        await lock.acquire()
        admin_task = member_task = None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                     base_url="https://cat.example") as client:
            try:
                member_task = asyncio.create_task(client.request(
                    "DELETE", "/api/account/memories/cat-alice", headers=self.headers["alice"],
                    json={"confirm_device_id": "cat-alice"},
                ))
                await self.wait_until(lambda: lock.waiting_count > 0, "memory deletion did not queue")
                path, body = self.admin_change(action)
                admin_task = asyncio.create_task(client.post(path, headers=self.headers["owner"], json=body))
                await self.wait_until(lambda: self.identities.session_user(token) is None,
                                      "admin did not revoke the queued request's session")
                lock.release()
                admin_response, member_response = await asyncio.wait_for(
                    asyncio.gather(admin_task, member_task), timeout=8)
                async with lock:
                    pass
                self.assertEqual(admin_response.status_code, 200, admin_response.text)
                self.assertEqual(member_response.status_code, 401, member_response.text)
                self.assertIn(marker, [m["content"] for m in store.context_for(user_id, "cat-alice")["messages"]],
                              "revoked queued deletion removed private history")
            finally:
                if lock.locked():
                    lock.release()
                for task in (admin_task, member_task):
                    if task and not task.done():
                        task.cancel()
                await asyncio.gather(*(t for t in (admin_task, member_task) if t), return_exceptions=True)

    def test_memory_clear_queued_before_disable_preserves_history(self):
        self.client.portal.call(self.queued_memory_clear, "disable")

    def test_memory_clear_queued_before_session_revoke_preserves_history(self):
        self.client.portal.call(self.queued_memory_clear, "revoke")

    def test_windows_static_aliases_require_admin(self):
        paths = ("/web/ADMIN/index.html", "/web/AdMiN/admin.js", "/web/admin%5cindex.html",
                 "/web/admin%5Cadmin.css", "/web/ADMIN%5cindex.html")
        for path in paths:
            for actor in (None, "alice"):
                with self.subTest(path=path, actor=actor):
                    response = self.request("GET", path, actor=actor)
                    self.assertIn(response.status_code, (401, 403, 404), response.text[:200])
        self.assertEqual(self.request("GET", "/web/admin/admin.js", actor="owner").status_code, 200)

    def test_unbind_cleanup_failure_keeps_committed_audit_unfinished(self):
        with patch.object(self.app.state.connections, "revoke_device",
                          new=AsyncMock(side_effect=RuntimeError("private cleanup exception marker"))):
            response = self.request("POST", "/api/admin/devices/cat-alice/unbind", actor="owner",
                                    json={"confirm_device_id": "cat-alice"})
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIsNone(self.identities.credential_device(
            "cat-alice", self.device_headers["Authorization"].split()[1])["owner_user_id"])
        self.assertIn("已生效", response.json()["detail"])
        self.assertNotIn("private cleanup exception marker", response.text)
        audit = self.request("GET", "/api/admin/audit", actor="owner").json()["items"]
        unbind = next(row for row in audit if row["action"] == "unbind-device")
        self.assertEqual(unbind["result"], "started")

    def test_cutover_restore_preserves_changes_made_after_trial_backup(self):
        user_id = self.users["alice"].id
        store = self.app.state.memory_manager.store
        conversation = store.create_conversation(user_id, "cat-alice")
        store.append_message(conversation, "user", "before isolation trial")
        provider = self.app.state.provider_settings
        provider.save(user_id, "sk-simulated-before-trial")
        directory = self.database.parent
        trial = backup_database(self.database, directory / "trial-only.sqlite3")

        store.append_message(conversation, "user", "new data during isolation trial")
        provider.save(user_id, "sk-simulated-at-cutover")
        cutover_path = directory / "cutover.sqlite3"
        cutover = backup_database(self.database, cutover_path)
        self.assertGreater(cutover["row_counts"]["messages"], trial["row_counts"]["messages"])
        store.append_message(conversation, "user", "after cutover requires separate recovery")

        restored = directory / "restored-cutover.sqlite3"
        restore_database(cutover_path, restored, cutover["sha256"], apply=True, offline=True)
        recovered = fixture.MemoryStore(restored)
        self.assertEqual([row["content"] for row in recovered.context_for(user_id, "cat-alice")["messages"]],
                         ["before isolation trial", "new data during isolation trial"])
        self.assertEqual(fixture.ProviderSettings(restored, key_file=provider.key_file).get(user_id),
                         "sk-simulated-at-cutover")


if __name__ == "__main__":
    unittest.main()
