"""账号停用/退出管理时取消已授权的旧 HTTP 请求，补足长连接之外的任务边界。"""

import asyncio
from http.cookies import SimpleCookie, CookieError

from starlette.responses import JSONResponse

from access_control import SESSION_COOKIE
from async_helpers import run_blocking


class AccountOperations:
    def __init__(self):
        self.tasks = {}
        self.revoked_tasks = set()

    async def cancel_user(self,user_id):
        tasks = set(self.tasks.get(user_id,set())) - {asyncio.current_task()}
        self.revoked_tasks.update(tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)


class AccountOperationsMiddleware:
    def __init__(self,app,*,identities,operations):
        self.app,self.identities,self.operations = app,identities,operations

    async def __call__(self,scope,receive,send):
        if scope['type'] != 'http' or not scope['path'].startswith('/api/') or scope['path'].startswith('/api/admin/'):
            return await self.app(scope,receive,send)
        cookies = SimpleCookie()
        try:
            for key,value in scope.get('headers',[]):
                if key.lower() == b'cookie':
                    cookies.load(value.decode('latin-1'))
        except CookieError:
            pass
        cookie = cookies.get(SESSION_COOKIE)
        user = await run_blocking(self.identities.session_user,cookie.value) if cookie else None
        if user is None:
            return await self.app(scope,receive,send)
        task = asyncio.current_task()
        self.operations.tasks.setdefault(user.id,set()).add(task)
        started = False
        async def tracked_send(message):
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
            await send(message)
        try:
            await self.app(scope,receive,tracked_send)
        except asyncio.CancelledError:
            if task not in self.operations.revoked_tasks:
                raise
            if not started:
                await JSONResponse({'detail':'账号会话已被管理员撤销，请重新登录'},status_code=401)(scope,receive,send)
        finally:
            self.operations.tasks.get(user.id,set()).discard(task)
            if not self.operations.tasks.get(user.id):
                self.operations.tasks.pop(user.id,None)
            self.operations.revoked_tasks.discard(task)
