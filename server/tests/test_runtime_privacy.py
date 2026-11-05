"""上游事件、识别和运行日志不输出聊天正文或凭证。"""

import asyncio
import json
import logging
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from asr_core import ASRCallback
from omni_realtime_client import OmniRealtimeSession
from runtime_logging import configure_logging, log_event


class PrivacyLogTests(unittest.TestCase):
    def test_rotating_log_redacts_credentials_and_exception_body(self):
        root = logging.getLogger()
        previous, level = root.handlers[:], root.level
        with tempfile.TemporaryDirectory() as directory:
            try:
                with patch.dict("os.environ", {"DASHSCOPE_API_KEY": "simulated-private-key"}):
                    configure_logging(Path(directory), max_bytes=512, backups=2)
                    for _ in range(30):
                        log_event("event key=simulated-private-key Authorization=Bearer dev-private-token")
                    try:
                        raise ValueError("不应出现在日志中的聊天正文")
                    except ValueError:
                        logging.getLogger("cybermiao").exception("request_failed")
            finally:
                for handler in root.handlers[:]:
                    if handler not in previous:
                        root.removeHandler(handler)
                        handler.close()
                root.setLevel(level)
            files = list(Path(directory).glob("CyberMiao.log*"))
            self.assertLessEqual(len(files), 3)
            output = "".join(path.read_text(encoding="utf-8") for path in files)
            self.assertNotIn("simulated-private-key", output)
            self.assertNotIn("dev-private-token", output)
            self.assertNotIn("不应出现在日志中的聊天正文", output)
            self.assertIn("ValueError", output)
            if os.name != "nt":
                self.assertTrue(all(path.stat().st_mode & 0o077 == 0 for path in files))

    def test_asr_debug_and_ignored_events_keep_only_metadata(self):
        private_text = "只应保存在聊天数据库中的识别内容"
        callback = ASRCallback(lambda _: None, lambda coroutine: coroutine.close(), AsyncMock(), AsyncMock(),
                               lambda: True, AsyncMock(), AsyncMock(), asyncio.Lock())
        with patch("asr_core.ASR_DEBUG_RAW", True), self.assertLogs("cybermiao", level="INFO") as logs:
            callback.on_event({"text": private_text})
        self.assertNotIn(private_text, "\n".join(logs.output))


class RealtimePrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def test_transcript_reaches_owner_callback_but_not_debug_or_provider_errors(self):
        private_text = "模拟的私人对话"
        events = [
            {"type": "conversation.item.input_audio_transcription.completed", "transcript": private_text},
            {"type": "error", "error": {"type": "invalid_request_error", "code": "invalid_parameter",
                                           "message": private_text, "request": {"api_key": "private-credential"}}},
        ]

        class Socket:
            async def __aiter__(self):
                for item in events:
                    yield json.dumps(item)

        debug, errors, transcripts = [], [], []

        async def debug_callback(value):
            debug.append(value)

        async def error_callback(value):
            errors.append(value)

        async def transcript_callback(value):
            transcripts.append(value)

        callback = AsyncMock()
        session = OmniRealtimeSession(loop=asyncio.get_running_loop(), system_prompt="模拟",
                                      on_input_transcript=transcript_callback, on_output_text_delta=callback,
                                      on_output_audio=callback, on_response_started=callback, on_response_done=callback,
                                      on_speech_started=callback, on_error=error_callback, on_debug=debug_callback)
        session._ws = Socket()
        await session._handle_messages()
        self.assertEqual(transcripts, [private_text])
        metadata = "\n".join(debug + errors)
        self.assertNotIn(private_text, metadata)
        self.assertNotIn("private-credential", metadata)
        self.assertIn("invalid_parameter", metadata)


if __name__ == "__main__":
    unittest.main()
