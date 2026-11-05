"""长连接退出时完成巡检清理，并保留真正的外部取消。"""

import asyncio
import anyio
import unittest
from unittest.mock import AsyncMock, patch

from connection_authorization import ConnectionAuthorizer, DeviceAuthorization


class ConnectionCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_asgi_cancellation_waits_for_watcher_cleanup(self):
        authorizer = ConnectionAuthorizer(None)
        authorization = DeviceAuthorization("session", "owner", "cat", "simulated-token")
        started, cleaning, release, finished = (asyncio.Event() for _ in range(4))
        scopes = []

        async def watcher(lease):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
                finished.set()

        async def handler():
            await started.wait()

        async def serve():
            with anyio.CancelScope() as scope:
                scopes.append(scope)
                await authorizer.run(authorization, handler)
                await anyio.sleep_forever()

        with patch.object(DeviceAuthorization, "is_valid", AsyncMock(return_value=True)), \
                patch.object(authorizer, "_watch", watcher):
            task = asyncio.create_task(serve())
            try:
                await asyncio.wait_for(cleaning.wait(), 1)
                scopes[0].cancel()
                # 清理尚未放行时，取消域不得再次打断巡检任务。
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), .02)
                release.set()
                await asyncio.wait_for(task, 1)
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(finished.is_set())
        self.assertFalse(authorizer.leases)

    async def test_external_task_cancellation_still_propagates(self):
        authorizer = ConnectionAuthorizer(None)
        authorization = DeviceAuthorization("session", "owner", "cat", "simulated-token")
        started, finished = asyncio.Event(), asyncio.Event()

        async def handler():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()

        async def cancellation_args(coroutine):
            started.clear()
            finished.clear()
            task = asyncio.create_task(coroutine)
            await asyncio.wait_for(started.wait(), 1)
            task.cancel("caller shutdown")
            with self.assertRaises(asyncio.CancelledError) as result:
                await task
            self.assertTrue(finished.is_set())
            return result.exception.args

        async def await_child():
            return await asyncio.create_task(handler())

        # Python 3.10 在 await 子任务时可能丢失取消消息；以同一解释器的
        # 原生行为作对照，仍要求清理完成、取消继续传播且不替换可用的消息。
        expected = await cancellation_args(await_child())
        with patch.object(DeviceAuthorization, "is_valid", AsyncMock(return_value=True)):
            actual = await cancellation_args(authorizer.run(authorization, handler))
        self.assertEqual(actual, expected)
        self.assertFalse(authorizer.leases)


if __name__ == "__main__":
    unittest.main()
