"""消费实际响应生成器，验证不同设备的音频隔离和取消行为。"""

import asyncio
import unittest

from audio_stream import AudioStream, BYTES_PER_20MS_16K


class AudioStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_streams_prebuffer_and_reset_are_isolated(self):
        first, second = AudioStream(), AudioStream()
        a, b = b"\x12\x34" * 20, b"\x56\x78" * 20
        await first.broadcast_pcm16_realtime(a)
        await second.broadcast_pcm16_realtime(b)
        left = first.response().body_iterator
        right = second.response().body_iterator
        try:
            self.assertTrue((await anext(left)).startswith(b"RIFF"))
            self.assertTrue((await anext(right)).startswith(b"RIFF"))
            self.assertEqual(await anext(left), a.ljust(BYTES_PER_20MS_16K, b"\0"))
            self.assertEqual(await anext(right), b.ljust(BYTES_PER_20MS_16K, b"\0"))
            first.set_playing_task_marker(asyncio.Event())
            second.set_playing_task_marker(asyncio.Event())
            await first.hard_reset_audio()
            self.assertFalse(first.is_playing_now())
            self.assertTrue(second.is_playing_now())
            self.assertEqual(len(second.stream_clients), 1)
            await second.broadcast_pcm16_realtime(b)
            self.assertEqual(await anext(right), b.ljust(BYTES_PER_20MS_16K, b"\0"))
        finally:
            await left.aclose()
            await right.aclose()
            await first.hard_reset_audio()
            await second.hard_reset_audio()

    async def test_replacing_one_device_sink_does_not_abort_another(self):
        first, second = AudioStream(), AudioStream()
        old = first.response().body_iterator
        other = second.response().body_iterator
        replacement = first.response().body_iterator
        try:
            await anext(old)
            await anext(other)
            old_client = next(iter(first.stream_clients))
            other_client = next(iter(second.stream_clients))
            await anext(replacement)
            self.assertTrue(old_client.abort_event.is_set())
            self.assertFalse(other_client.abort_event.is_set())
            await old.aclose()
            self.assertEqual(len(first.stream_clients), 1)
        finally:
            await old.aclose()
            await other.aclose()
            await replacement.aclose()

    async def test_unused_response_does_not_register_client(self):
        audio = AudioStream()
        response = audio.response()
        self.assertEqual(audio.stream_clients, set())
        await response.body_iterator.aclose()


if __name__ == "__main__":
    unittest.main()
