"""管理员自身的写入队列、授权撤销和审计故障，使用事件及临时数据库。"""

import asyncio
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from support import server_app
from access_control import AccessSettings, SESSION_COOKIE
from async_helpers import run_blocking
from identity_store import csrf_for_session
from identity_store import IdentityStore
from admin_store import AdminStore
from memory_store import MemoryStore, SummaryResult


class ObservedLock(asyncio.Lock):
    def __init__(self):
        super().__init__()
        self.waiting = 0
        self.changed = asyncio.Event()

    async def acquire(self):
        if not self.locked():
            return await super().acquire()
        self.waiting += 1
        self.changed.set()
        try:
            return await super().acquire()
        finally:
            self.waiting -= 1

    async def wait_for_waiters(self, count=1):
        async def wait():
            while self.waiting < count:
                self.changed.clear()
                await self.changed.wait()
        await asyncio.wait_for(wait(), timeout=3)


async def summarize(_messages):
    return SummaryResult(summary="Fictional summary")


class AdminWriterRevocationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 2
        self.temp = tempfile.TemporaryDirectory(prefix="admin-writer-")
        self.app = server_app.create_app(
            store=MemoryStore(Path(self.temp.name) / "memory.sqlite3"),
            access=AccessSettings("accounts"), summarizer=summarize,
        )
        self.ids = self.app.state.identities
        self.now = time.time()
        self.ids.clock = lambda: self.now
        self.admins = self.app.state.admin_store
        self.first = self.ids.create_user("fictional_first", "fictional-password-123")
        self.second = self.ids.create_user("fictional_second", "fictional-password-123")
        self.target = self.ids.create_user("fictional_target", "fictional-password-123")
        for user in (self.first, self.second):
            self.admins.set_role(user.username, True)
        self.headers = self.login(self.first)
        self.second_headers = self.login(self.second)
        self.credential = self.ids.register_device("cat-fictional-writer", "Fictional writer")
        pairing = self.ids.request_pairing(self.credential.device_id, self.credential.token)
        self.ids.claim_pairing(pairing.code, self.target.id, client_key="fictional")
        self.gate = ObservedLock()
        self.app.state.admin_mutation_lock = self.gate
        self.client = AsyncClient(transport=ASGITransport(self.app), base_url="http://testserver")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.app.state.devices.close()
        self.temp.cleanup()

    def login(self, user):
        grant = self.ids.login(user.username, "fictional-password-123", client_key="fictional")
        return {"Cookie": f"{SESSION_COOKIE}={grant.token}", "Origin": "http://testserver",
                "X-CSRF-Token": csrf_for_session(grant.token)}

    def write(self, kind="voice"):
        if kind == "status":
            return self.client.post(f"/api/admin/users/{self.target.id}/status", headers=self.headers,
                                    json={"disabled": True, "confirm_username": self.target.username})
        if kind == "revoke":
            return self.client.post(f"/api/admin/users/{self.target.id}/revoke-sessions", headers=self.headers,
                                    json={"confirm_username": self.target.username})
        payload = {"confirm_device_id": self.credential.device_id}
        operation = "voice" if kind == "live-voice" else kind
        if operation == "voice":
            payload["paused"] = True
        return self.client.post(f"/api/admin/devices/{self.credential.device_id}/{operation}",
                                headers=self.headers, json=payload)

    def audit_rows(self):
        return self.admins.audit("", 1, 100)["items"]

    async def assert_unchanged(self):
        self.assertFalse(self.admins.user(self.target.id)["disabled"])
        self.assertFalse(self.ids.voice_preference(self.target.id, self.credential.device_id))
        self.assertEqual(self.ids.credential_device(self.credential.device_id, self.credential.token)["owner_user_id"],
                         self.target.id)

    async def test_admin_session_revoked_by_preceding_request_cannot_write(self):
        for kind in ("status", "revoke", "voice", "unbind"):
            with self.subTest(kind=kind):
                self.headers = self.login(self.first)
                self.admins.change_user(self.second.id, self.target.id, False)
                await self.gate.acquire()
                revoke = asyncio.create_task(self.client.post(
                    f"/api/admin/users/{self.first.id}/revoke-sessions", headers=self.second_headers,
                    json={"confirm_username": self.first.username}))
                pending = None
                try:
                    await self.gate.wait_for_waiters()
                    pending = asyncio.create_task(self.write(kind))
                    await self.gate.wait_for_waiters(2)
                finally:
                    self.gate.release()
                try:
                    response, old_write = await asyncio.wait_for(asyncio.gather(revoke, pending), timeout=3)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(old_write.status_code, 401, old_write.text)
                    self.assertTrue(self.admins.is_admin(self.first.id))
                    await self.assert_unchanged()
                finally:
                    for task in (revoke, pending):
                        if task is not None:
                            task.cancel()
                    await asyncio.gather(*(task for task in (revoke, pending) if task), return_exceptions=True)

    async def invalidate_writer(self, mode):
        if mode == "cli":
            other = AdminStore(IdentityStore(self.ids.path))
            await run_blocking(other.set_role,self.first.username,False)
        elif mode == "expired":
            self.now += 8 * 86400
        else:
            await run_blocking(self.admins.change_user,self.second.id,self.first.id,True if mode == "disabled" else None)

    def reset_writer(self):
        self.now += 301
        self.admins.change_user(self.second.id,self.first.id,False)
        self.admins.set_role(self.first.username,True)
        self.headers = self.login(self.first)

    async def test_revocation_is_checked_after_account_and_device_queues(self):
        for lock_kind, kind in (("account", "status"), ("account", "voice"), ("device", "unbind")):
            for mode in ("revoked", "disabled", "cli", "expired"):
                with self.subTest(lock=lock_kind, kind=kind, invalidation=mode):
                    self.reset_writer()
                    lock = ObservedLock()
                    if lock_kind == "account":
                        self.app.state.model_locks[self.target.id] = lock
                    else:
                        self.app.state.devices._locks[self.credential.device_id] = lock
                    await lock.acquire()
                    pending = asyncio.create_task(self.write(kind))
                    try:
                        await lock.wait_for_waiters()
                        await self.invalidate_writer(mode)
                    finally:
                        lock.release()
                    try:
                        response = await asyncio.wait_for(pending, timeout=3)
                        self.assertEqual(response.status_code,403 if mode == "cli" else 401,response.text)
                        await self.assert_unchanged()
                    finally:
                        pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)

    async def test_cancelled_account_and_device_queues_do_not_start_mutations(self):
        for lock_kind, kind in (("account", "status"), ("device", "voice")):
            with self.subTest(lock=lock_kind):
                lock = ObservedLock()
                if lock_kind == "account":
                    self.app.state.model_locks[self.target.id] = lock
                else:
                    self.app.state.devices._locks[self.credential.device_id] = lock
                await lock.acquire()
                baseline = asyncio.all_tasks()
                pending = asyncio.create_task(self.write(kind))
                try:
                    await lock.wait_for_waiters()
                    pending.cancel()
                    result = await asyncio.wait_for(asyncio.gather(pending, return_exceptions=True), timeout=3)
                    self.assertIsInstance(result[0], asyncio.CancelledError)
                    self.assertEqual([task for task in asyncio.all_tasks() - baseline if not task.done()], [])
                finally:
                    lock.release()
                await self.assert_unchanged()

    async def test_authorization_is_rechecked_in_the_mutation_transaction(self):
        for kind, store, method in (("status", self.admins, "change_user"),
                                    ("voice", self.ids, "save_voice_preference"),
                                    ("unbind", self.ids, "unbind_device"),
                                    ("live-voice", self.ids, "save_voice_preference")):
            for mode in ("revoked", "cli"):
                with self.subTest(kind=kind, invalidation=mode):
                    self.reset_writer()
                    if kind == "live-voice":
                        await self.app.state.devices.resolve(self.credential.device_id,self.target.id)
                    entered, release = asyncio.Event(), threading.Event()
                    loop, original = asyncio.get_running_loop(), getattr(store,method)

                    def delayed(*args, **kwargs):
                        loop.call_soon_threadsafe(entered.set)
                        if not release.wait(3):
                            raise TimeoutError("fictional test did not release the database mutation")
                        return original(*args, **kwargs)

                    with patch.object(store, method, side_effect=delayed):
                        pending = asyncio.create_task(self.write(kind))
                        try:
                            await asyncio.wait_for(entered.wait(), timeout=3)
                            if mode == "cli":
                                await run_blocking(self.admins.set_role,self.first.username,False)
                            else:
                                token = self.headers["Cookie"].split("=",1)[1]
                                await run_blocking(self.ids.revoke_session,token)
                        finally:
                            release.set()
                        response = await asyncio.wait_for(pending, timeout=3)
                    self.assertEqual(response.status_code,403 if mode == "cli" else 401,response.text)
                    await self.assert_unchanged()
                    row = next(row for row in self.audit_rows() if row["actor_id"] == self.first.id)
                    self.assertEqual(row["result"], "failed")

    async def test_started_change_and_audit_finish_before_cancelled_request_exits(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.app.state.connections.revoke_device

        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            await original(*args, **kwargs)

        with patch.object(self.app.state.connections,"revoke_device",side_effect=delayed):
            pending = asyncio.create_task(self.write("unbind"))
            revoke = None
            try:
                await asyncio.wait_for(entered.wait(), timeout=3)
                pending.cancel()
                revoke = asyncio.create_task(self.client.post(
                    f"/api/admin/users/{self.first.id}/revoke-sessions",headers=self.second_headers,
                    json={"confirm_username":self.first.username}))
                await self.gate.wait_for_waiters()
                self.assertFalse(pending.done(), "已提交操作在收尾前脱离所属请求")
            finally:
                release.set()
                result = await asyncio.wait_for(asyncio.gather(pending, return_exceptions=True), timeout=3)
                if revoke is not None:
                    response = await asyncio.wait_for(revoke, timeout=3)
            self.assertIsInstance(result[0], asyncio.CancelledError)
            self.assertEqual(response.status_code, 200,response.text)
            row = next(row for row in self.audit_rows() if row["action"] == "unbind-device")
            self.assertEqual(row["result"],"success")
            self.assertFalse(self.gate.locked())
            self.assertIsNone(self.ids.credential_device(self.credential.device_id,self.credential.token)["owner_user_id"])

    async def test_cancelled_admin_global_queue_leaves_no_detached_write(self):
        await self.gate.acquire()
        baseline = asyncio.all_tasks()
        pending = asyncio.create_task(self.write())
        queued = set()
        try:
            await self.gate.wait_for_waiters()
            queued = asyncio.all_tasks() - baseline
            pending.cancel()
            result = await asyncio.wait_for(asyncio.gather(pending, return_exceptions=True), timeout=3)
            detached = [task for task in queued if not task.done()]
        finally:
            self.gate.release()
        await asyncio.wait_for(asyncio.gather(*queued, return_exceptions=True), timeout=3)
        self.assertIsInstance(result[0], asyncio.CancelledError)
        self.assertEqual(detached, [], "取消全局队列后遗留了后台写任务")
        await self.assert_unchanged()

    async def test_success_audit_failure_preserves_committed_state_in_response(self):
        with patch.object(self.admins, "finish_action", side_effect=sqlite3.OperationalError("fictional audit failure")):
            try:
                response = await self.write()
            except sqlite3.Error:
                self.fail("审计落盘失败绕过了已提交状态的 503 响应")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("已生效", response.json()["detail"])
        self.assertNotIn("fictional audit failure", response.text)
        self.assertTrue(self.ids.voice_preference(self.target.id, self.credential.device_id))
        self.assertEqual(self.audit_rows()[0]["result"], "started")

    async def test_start_audit_failure_prevents_business_change(self):
        with patch.object(self.admins,"begin_action",side_effect=sqlite3.OperationalError("fictional start failure")):
            response = await self.write()
        self.assertEqual(response.status_code,503,response.text)
        self.assertIn("未执行",response.json()["detail"])
        self.assertNotIn("fictional start failure",response.text)
        await self.assert_unchanged()

    async def test_failed_mutation_and_failed_audit_return_unconfirmed_result(self):
        with patch.object(self.ids,"save_voice_preference",side_effect=sqlite3.OperationalError("fictional save failure")), \
             patch.object(self.admins,"finish_action",side_effect=sqlite3.OperationalError("fictional result failure")):
            response = await self.write()
        self.assertEqual(response.status_code,503,response.text)
        self.assertIn("审计写入未确认",response.json()["detail"])
        self.assertNotIn("已生效",response.text)
        self.assertNotIn("fictional",response.text)
        await self.assert_unchanged()
        self.assertEqual(self.audit_rows()[0]["result"],"started")

    async def test_cancellation_waits_for_the_started_success_audit(self):
        entered, release = asyncio.Event(), threading.Event()
        loop, original = asyncio.get_running_loop(), self.admins.finish_action

        def delayed(*args):
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(3):
                raise TimeoutError("fictional result audit was not released")
            return original(*args)

        with patch.object(self.admins,"finish_action",side_effect=delayed):
            pending = asyncio.create_task(self.write())
            try:
                await asyncio.wait_for(entered.wait(),timeout=3)
                pending.cancel()
                self.assertFalse(pending.done())
                self.assertTrue(self.ids.voice_preference(self.target.id,self.credential.device_id))
            finally:
                release.set()
                result = await asyncio.wait_for(asyncio.gather(pending,return_exceptions=True),timeout=3)
        self.assertIsInstance(result[0],asyncio.CancelledError)
        self.assertEqual(self.audit_rows()[0]["result"],"success")
        self.assertFalse(self.gate.locked())

    async def test_committed_cleanup_completes_after_cli_role_revocation(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()

        with patch.object(self.app.state.connections,"revoke_device",side_effect=delayed):
            pending = asyncio.create_task(self.write("unbind"))
            try:
                await asyncio.wait_for(entered.wait(),timeout=3)
                await self.invalidate_writer("cli")
            finally:
                release.set()
            response = await asyncio.wait_for(pending,timeout=3)
        self.assertEqual(response.status_code,200,response.text)
        row = next(row for row in self.audit_rows() if row["action"] == "unbind-device")
        self.assertEqual(row["result"],"success")
        self.assertFalse(self.admins.is_admin(self.first.id))
