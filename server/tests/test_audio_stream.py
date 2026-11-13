"""消费实际响应生成器，验证不同设备的音频隔离和取消行为。"""

import asyncio
import unittest

from audio_stream import AudioStream


class AudioStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_https_connection_receives_entire_sentence_including_prefix(self):
        audio = AudioStream()
        # 在播放端连接前，模型已突发产生 8 秒语音，明显超过旧 1.6 秒窗口。
        pcm = b''.join(i.to_bytes(2, 'little') * 480 for i in range(400))
        await audio.broadcast_pcm16_realtime(pcm)
        audio.finish_response()
        response = audio.response().body_iterator
        self.assertTrue((await anext(response)).startswith(b'RIFF'))
        self.assertEqual(b''.join([chunk async for chunk in response]), pcm)
        await response.aclose()

    async def test_new_response_aborts_old_sink_without_replaying_previous_sentence(self):
        audio = AudioStream()
        old = audio.response().body_iterator
        await anext(old)
        previous = next(iter(audio.stream_clients))
        await audio.broadcast_pcm16_realtime(b'\x01\x00' * 480)
        audio.clear_stream_prebuffer()
        self.assertTrue(previous.abort_event.is_set())
        await audio.broadcast_pcm16_realtime(b'\x02\x00' * 240)
        audio.finish_response()
        new = audio.response().body_iterator
        await anext(new)
        self.assertEqual(b''.join([chunk async for chunk in new]), b'\x02\x00' * 240)
        await old.aclose()
        await new.aclose()

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
            self.assertEqual(await anext(left), a)
            self.assertEqual(await anext(right), b)
            first.set_playing_task_marker(asyncio.Event())
            second.set_playing_task_marker(asyncio.Event())
            await first.hard_reset_audio()
            self.assertFalse(first.is_playing_now())
            self.assertTrue(second.is_playing_now())
            self.assertEqual(len(second.stream_clients), 1)
            await second.broadcast_pcm16_realtime(b)
            self.assertEqual(await anext(right), b)
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

    async def test_model_fragmentation_preserves_pcm_samples_and_duration(self):
        # 同一秒的 24 kHz PCM，不同模型事件长度不能改变它的内容或时长。
        import math
        import struct

        audio = AudioStream()
        pcm = struct.pack("<24000h", *(round(18000 * math.sin(i * math.tau * 440 / 24000))
                                     for i in range(24000)))
        response = audio.response().body_iterator
        try:
            await anext(response)
            offset = 0
            sizes = (6144, 100, 1920, 258, 4096)
            while offset < len(pcm):
                size = sizes[(offset // 2) % len(sizes)]
                await audio.broadcast_pcm16_realtime(pcm[offset:offset + size])
                offset += size
            next(iter(audio.stream_clients)).q.put_nowait(None)
            emitted = b"".join([chunk async for chunk in response])
            self.assertEqual(len(emitted), len(pcm), "分块不能插入静音、延长语音")
            self.assertEqual(emitted, pcm, "采样顺序和数值必须保持不变")
        finally:
            await response.aclose()

    async def test_model_pause_does_not_insert_synthetic_silence(self):
        audio = AudioStream()
        response = audio.response().body_iterator
        pending = None
        try:
            await anext(response)
            audio.set_playing_task_marker(asyncio.Event())
            pending = asyncio.create_task(anext(response))
            await asyncio.sleep(.18)  # 超过流生成器的 120 ms 查询间隔。
            self.assertFalse(pending.done(), "等待模型数据时不能凭空插入 20 ms 静音")
            samples = b"\x12\x34" * 30
            await audio.broadcast_pcm16_realtime(samples)
            self.assertEqual(await asyncio.wait_for(pending, timeout=1), samples)
        finally:
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await response.aclose()
            await audio.hard_reset_audio()

    async def test_final_partial_audio_block_is_emitted_without_padding(self):
        audio = AudioStream()
        pcm = b"\x56\x78" * 97
        await audio.broadcast_pcm16_realtime(pcm)
        response = audio.response().body_iterator
        try:
            await anext(response)
            next(iter(audio.stream_clients)).q.put_nowait(None)
            self.assertEqual(b"".join([chunk async for chunk in response]), pcm)
        finally:
            await response.aclose()


if __name__ == "__main__":
    unittest.main()
