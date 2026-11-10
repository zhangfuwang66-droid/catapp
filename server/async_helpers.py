"""取消异步调用时，等待已经开始的短时阻塞操作完成，避免遗留文件句柄。"""

import asyncio
import contextvars
import functools


async def finish_before_cancel(awaitable):
    """取消所属请求前等待已开始的操作收尾；调用方须先完成可取消的排队和授权。"""
    task = asyncio.ensure_future(awaitable)
    cancelled = None
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError as exc:
            if task.done():
                if not task.cancelled():
                    task.exception()
                raise
            cancelled = exc
        except BaseException:
            if cancelled is not None:
                raise cancelled
            raise
    if cancelled is not None:
        raise cancelled
    return result


async def run_blocking(function, *args, **kwargs):
    context = contextvars.copy_context()
    call = functools.partial(function, *args, **kwargs)
    return await finish_before_cancel(asyncio.get_running_loop().run_in_executor(None, context.run, call))
