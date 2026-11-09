"""只对明确的音频能力错误降为文本；同步模型请求不能阻塞事件循环。"""
import asyncio
import base64
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx
from openai import BadRequestError, AuthenticationError
from support import server_app
import omni_client


class OutputStream:
    def __init__(self, *, audio=False):
        self.closed = False
        self.chunk = SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
            content="来自资料的回答", audio={"data": base64.b64encode(b"pcm").decode()} if audio else None))])

    def __iter__(self):
        yield self.chunk

    def close(self):
        self.closed = True


def provider_error(cls, body, status=400):
    response = httpx.Response(status, request=httpx.Request("POST", "https://example.invalid"))
    return cls("synthetic provider error", response=response, body=body)


class OmniOutputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.capability = patch.object(omni_client, "_text_only_output", threading.Event())
        self.capability.start()
        self.addCleanup(self.capability.stop)

    async def collect(self, text="集合口令", prompt="按当前账号资料回答"):
        return [piece async for piece in omni_client.stream_chat([{"type":"text","text":text}], system_prompt=prompt)]

    async def test_audio_rejection_retries_text_preserves_prompt_and_caches_only_capability(self):
        error = provider_error(BadRequestError, {"code":"invalid_parameter_error",
            "message":"The current model does not support the modalities parameter containing audio."})
        first, second = OutputStream(), OutputStream()
        create = Mock(side_effect=[error, first, second])
        with patch.object(omni_client.oai_client.chat.completions, "create", create):
            pieces = await self.collect()
            self.assertTrue(pieces[0].notice)
            self.assertEqual(pieces[1].text_delta, "来自资料的回答")
            await self.collect("第二个账号的问题", "第二个账号的资料")
        requests = [call.kwargs for call in create.call_args_list]
        self.assertEqual(requests[0]["modalities"], ["text", "audio"])
        self.assertEqual(requests[1]["modalities"], ["text"])
        self.assertNotIn("audio", requests[1])
        self.assertEqual(requests[0]["messages"], requests[1]["messages"])
        self.assertEqual(requests[2]["messages"][0]["content"], "第二个账号的资料")
        self.assertNotIn("集合口令", str(requests[2]))
        self.assertTrue(first.closed and second.closed)

    async def test_authentication_and_unrelated_parameter_errors_never_fall_back(self):
        errors = [provider_error(AuthenticationError, {"code":"invalid_api_key"}, 401),
                  provider_error(BadRequestError, {"code":"invalid_parameter_error", "message":"invalid voice"})]
        for error in errors:
            create = Mock(side_effect=error)
            with self.subTest(kind=type(error).__name__), \
                    patch.object(omni_client.oai_client.chat.completions, "create", create):
                with self.assertRaises(type(error)):
                    await self.collect()
                self.assertEqual(create.call_count, 1)
                self.assertFalse(omni_client._text_only_output.is_set())

    async def test_audio_capable_model_keeps_audio_and_closes_stream(self):
        stream = OutputStream(audio=True)
        with patch.object(omni_client.oai_client.chat.completions, "create", Mock(return_value=stream)):
            pieces = await self.collect()
        self.assertEqual(len(pieces), 1)
        self.assertTrue(pieces[0].audio_b64)
        self.assertFalse(pieces[0].notice)
        self.assertTrue(stream.closed)

    async def test_request_creation_runs_off_event_loop(self):
        entered, release = threading.Event(), threading.Event()
        def create(**kwargs):
            entered.set()
            if not release.wait(timeout=2):
                raise TimeoutError("test event loop was blocked")
            return OutputStream()
        with patch.object(omni_client.oai_client.chat.completions, "create", create):
            task = asyncio.create_task(self.collect())
            try:
                async def wait_until_entered():
                    while not entered.is_set():
                        await asyncio.sleep(.005)
                await asyncio.wait_for(wait_until_entered(), timeout=1)
                release.set()
                await asyncio.wait_for(task, timeout=1)
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
