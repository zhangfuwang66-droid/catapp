"""每台设备独立的 PCM 播放任务、预缓冲与 HTTP 播放客户端。"""

import asyncio
from collections import deque
from dataclasses import dataclass
import struct
from typing import Deque, Optional, Set

from fastapi.responses import StreamingResponse

STREAM_SR = 24000
STREAM_CH = 1
STREAM_SW = 2
BYTES_PER_20MS_16K = STREAM_SR * STREAM_SW * 20 // 1000
SILENCE_20MS = bytes(BYTES_PER_20MS_16K)
STREAM_QUEUE_MAX = 3000
STREAM_PREBUFFER_CHUNKS = 80
STREAM_IDLE_GRACE_SEC = 3.0


@dataclass(frozen=True)
class StreamClient:
    q: asyncio.Queue
    abort_event: asyncio.Event


def _wav_header_unknown_size(sr=STREAM_SR, ch=STREAM_CH, sw=STREAM_SW) -> bytes:
    data_size = 0x7FFFFFF0
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + data_size, b"WAVE", b"fmt ",
        16, 1, ch, sr, sr * ch * sw, ch * sw, sw * 8, b"data", data_size,
    )


class AudioStream:
    def __init__(self):
        self.current_ai_task: Optional[asyncio.Task] = None
        self.stream_clients: Set[StreamClient] = set()
        self.recent_pcm_chunks: Deque[bytes] = deque(maxlen=STREAM_PREBUFFER_CHUNKS)

    async def cancel_current_ai(self):
        task = self.current_ai_task
        self.current_ai_task = None
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

    def is_playing_now(self) -> bool:
        task = self.current_ai_task
        return task is not None and not task.done()

    def set_playing_task_marker(self, release_event: Optional[asyncio.Event]):
        previous = self.current_ai_task
        if previous is not None and not previous.done():
            previous.cancel()
        self.current_ai_task = None
        if release_event is not None:
            self.current_ai_task = asyncio.create_task(release_event.wait())

    async def hard_reset_audio(self, reason: str = ""):
        for client in tuple(self.stream_clients):
            client.abort_event.set()
        self.stream_clients.clear()
        self.recent_pcm_chunks.clear()
        await self.cancel_current_ai()
        if reason:
            print(f"[HARD-RESET] {reason}", flush=True)

    def clear_stream_prebuffer(self):
        self.recent_pcm_chunks.clear()

    async def broadcast_pcm16_realtime(self, pcm16: bytes):
        # 不阻塞模型回调；队列满时仅丢弃该播放客户端最旧的音频块。
        for offset in range(0, len(pcm16), BYTES_PER_20MS_16K):
            piece = pcm16[offset:offset + BYTES_PER_20MS_16K]
            self.recent_pcm_chunks.append(piece)
            for client in tuple(self.stream_clients):
                if client.abort_event.is_set():
                    self.stream_clients.discard(client)
                    continue
                if client.q.full():
                    client.q.get_nowait()
                client.q.put_nowait(piece)
        await asyncio.sleep(0)

    def response(self) -> StreamingResponse:
        async def generate():
            # 在消费响应时登记，避免未消费的响应留下永久播放客户端。
            for old in tuple(self.stream_clients):
                old.abort_event.set()
            self.stream_clients.clear()
            client = StreamClient(asyncio.Queue(maxsize=STREAM_QUEUE_MAX), asyncio.Event())
            self.stream_clients.add(client)
            for chunk in self.recent_pcm_chunks:
                client.q.put_nowait(chunk)
            loop = asyncio.get_running_loop()
            last_emit_at = loop.time()
            try:
                yield _wav_header_unknown_size()
                while not client.abort_event.is_set():
                    try:
                        chunk = await asyncio.wait_for(client.q.get(), timeout=0.12)
                    except asyncio.TimeoutError:
                        if self.is_playing_now():
                            yield SILENCE_20MS
                            last_emit_at = loop.time()
                        elif loop.time() - last_emit_at > STREAM_IDLE_GRACE_SEC:
                            break
                        continue
                    if client.abort_event.is_set() or chunk is None:
                        break
                    if chunk:
                        yield chunk.ljust(BYTES_PER_20MS_16K, b"\x00")
                        last_emit_at = loop.time()
            finally:
                self.stream_clients.discard(client)

        return StreamingResponse(generate(), media_type="audio/wav", headers={"Cache-Control": "no-store"})


def register_stream_route(app, audio: AudioStream):
    @app.get("/stream.wav")
    async def stream_wav():
        return audio.response()
