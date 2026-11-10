# -*- coding: utf-8 -*-
import asyncio
import base64
import inspect
import json
import os
import uuid
from typing import Awaitable, Callable, Optional

import websockets
from websockets.exceptions import ConnectionClosed
from runtime_logging import provider_error_tag, redact_secrets

REALTIME_MODEL = os.getenv("QWEN_REALTIME_MODEL", "qwen3.8-omni-flash")
REALTIME_VOICE = os.getenv("QWEN_REALTIME_VOICE", "Sunnybobi")
REALTIME_TURN_DETECTION = os.getenv("QWEN_REALTIME_TURN_DETECTION", "server_vad")
REALTIME_TURN_THRESHOLD = float(os.getenv("QWEN_REALTIME_TURN_THRESHOLD", "0.5"))
REALTIME_TURN_SILENCE_MS = int(os.getenv("QWEN_REALTIME_TURN_SILENCE_MS", "600"))
REALTIME_PREFIX_PADDING_MS = int(os.getenv("QWEN_REALTIME_PREFIX_PADDING_MS", "500"))
REALTIME_INPUT_TRANSCRIPTION_MODEL = os.getenv("QWEN_REALTIME_TRANSCRIPTION_MODEL", "gummy-realtime-v1")
REALTIME_WS_URL = os.getenv("QWEN_REALTIME_WS_URL", "wss://dashscope.aliyuncs.com/api-ws/v1/realtime")
REALTIME_READY_TIMEOUT_S = float(os.getenv("QWEN_REALTIME_READY_TIMEOUT", "10"))
API_KEY = os.getenv("DASHSCOPE_API_KEY", "")


class OmniRealtimeSession:
    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        system_prompt: str,
        on_input_transcript: Callable[[str], Awaitable[Optional[str | bool]]],
        on_output_text_delta: Callable[[str], Awaitable[None]],
        on_output_audio: Callable[[bytes], Awaitable[None]],
        on_response_started: Callable[[], Awaitable[None]],
        on_response_done: Callable[[], Awaitable[None]],
        on_speech_started: Callable[[], Awaitable[None]],
        on_error: Callable[[str], Awaitable[None]],
        on_debug: Optional[Callable[[str], Awaitable[None]]] = None,
        get_latest_image_bytes: Optional[Callable[[], bytes]] = None,
        manual_response: bool = False,
        api_key: Optional[str] = None,
    ):
        self.loop = loop
        self.system_prompt = system_prompt
        self.on_input_transcript = on_input_transcript
        self.on_output_text_delta = on_output_text_delta
        self.on_output_audio = on_output_audio
        self.on_response_started = on_response_started
        self.on_response_done = on_response_done
        self.on_speech_started = on_speech_started
        self.on_error = on_error
        self.on_debug = on_debug
        self.get_latest_image_bytes = get_latest_image_bytes
        self.manual_response = manual_response
        self.api_key = API_KEY if api_key is None else api_key

        self._ws = None
        self._message_task: Optional[asyncio.Task] = None
        self._connect_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._connected = False
        self._responding = False
        self._image_sent_for_turn = False
        self._ready_event = asyncio.Event()
        self._last_error_message = ""
        self._last_close_message = ""
        self._session_updated = False
        self._fatal_error = False
        self._audio_appended_for_turn = False
        self._audio_append_count_for_turn = 0
        self._audio_append_count_after_speech_started = 0
        self._speech_started_for_turn = False
        self._pending_image_task: Optional[asyncio.Task] = None
        self._instruction_lock = asyncio.Lock()
        self._instruction_waiter = None
        self._turn_generation = 0
        self._prepare_task = None
        self._turn_tasks = set()
        self._speech_item_id = None
        self._transcribed_items = {}

    @property
    def is_fatal(self) -> bool:
        return self._fatal_error

    def _ws_is_closed(self, ws) -> bool:
        if ws is None:
            return True
        closed = getattr(ws, "closed", None)
        if isinstance(closed, bool):
            return closed
        state = getattr(ws, "state", None)
        if state is None:
            return False
        state_name = getattr(state, "name", str(state)).upper()
        return state_name in {"CLOSED", "CLOSING"}

    async def ensure_connected(self):
        if not self.api_key:
            raise RuntimeError("未设置 DASHSCOPE_API_KEY")

        async with self._connect_lock:
            if self._fatal_error:
                detail = self._last_error_message or self._last_close_message or "fatal realtime error"
                raise RuntimeError(detail)
            if self._ws is not None and self._connected and not self._ws_is_closed(self._ws):
                return

            self._ready_event.clear()
            self._last_error_message = ""
            self._last_close_message = ""
            self._session_updated = False

            url = f"{REALTIME_WS_URL}?model={REALTIME_MODEL}"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            await self._debug(f"connecting url={REALTIME_WS_URL} model={REALTIME_MODEL}")
            connect_kwargs = {
                "open_timeout": 10,
                "max_size": None,
                "ping_interval": 20,
                "ping_timeout": 20,
            }
            sig = inspect.signature(websockets.connect)
            if "additional_headers" in sig.parameters:
                connect_kwargs["additional_headers"] = headers
            elif "extra_headers" in sig.parameters:
                connect_kwargs["extra_headers"] = headers
            else:
                connect_kwargs["extra_headers"] = headers
            try:
                self._ws = await websockets.connect(url, **connect_kwargs)
            except TypeError as exc:
                # 兼容某些环境下 websockets / loop 实现对 headers 参数名的差异
                msg = str(exc)
                if "additional_headers" in msg and "additional_headers" in connect_kwargs:
                    connect_kwargs["extra_headers"] = connect_kwargs.pop("additional_headers")
                    self._ws = await websockets.connect(url, **connect_kwargs)
                elif "extra_headers" in msg and "extra_headers" in connect_kwargs:
                    connect_kwargs["additional_headers"] = connect_kwargs.pop("extra_headers")
                    self._ws = await websockets.connect(url, **connect_kwargs)
                else:
                    raise
            self._message_task = asyncio.create_task(self._handle_messages())
            try:
                await self._update_session()
                await asyncio.wait_for(self._ready_event.wait(), timeout=REALTIME_READY_TIMEOUT_S)
            except BaseException:
                await self.close()
                raise
            if not self._connected or not self._session_updated:
                detail = self._last_error_message or self._last_close_message or "服务端未返回 session.updated"
                await self.close()
                raise RuntimeError(f"Realtime 会话未就绪：{detail}")
            await self._debug("session ready")

    async def _send_event(self, event: dict, *, turn_generation=None):
        ws = self._ws
        if ws is None or self._ws_is_closed(ws):
            self._connected = False
            raise RuntimeError(self._build_closed_error("WebSocket 未连接"))
        event["event_id"] = "event_" + uuid.uuid4().hex
        try:
            async with self._send_lock:
                if turn_generation is not None and turn_generation != self._turn_generation:
                    return False
                if self._ws is not ws or self._ws_is_closed(ws):
                    raise RuntimeError(self._build_closed_error("WebSocket 已关闭"))
                await ws.send(json.dumps(event, ensure_ascii=False))
                return True
        except ConnectionClosed as exc:
            self._connected = False
            self._last_close_message = f"code={exc.code}"
            raise RuntimeError(self._build_closed_error("WebSocket 已关闭"))

    async def _update_session(self):
        session_config = {
            "modalities": ["text", "audio"],
            "voice": REALTIME_VOICE,
            "instructions": self.system_prompt,
            "input_audio_format": "pcm",
            "output_audio_format": "pcm",
            "input_audio_transcription": {
                "model": REALTIME_INPUT_TRANSCRIPTION_MODEL,
            },
            "turn_detection": {
                "type": REALTIME_TURN_DETECTION,
                "threshold": REALTIME_TURN_THRESHOLD,
                "prefix_padding_ms": REALTIME_PREFIX_PADDING_MS,
                "silence_duration_ms": REALTIME_TURN_SILENCE_MS,
            },
        }
        if self.manual_response:
            # 保留服务端 VAD 和打断，由本地检索完成后显式发 response.create。
            session_config["turn_detection"]["create_response"] = False
        await self._debug(
            f"session.update turn_detection={REALTIME_TURN_DETECTION} threshold={REALTIME_TURN_THRESHOLD} silence_ms={REALTIME_TURN_SILENCE_MS}"
        )
        await self._send_event({"type": "session.update", "session": session_config})

    async def update_instructions(self, new_instructions: str, new_voice: Optional[str] = None, *, wait_for_ack=False):
        """Push a live session.update with a new system prompt (and optionally a new voice).

        Safe to call before the websocket is ready: we update the cached prompt
        so the next connection uses it.
        """
        async with self._instruction_lock:
            self.system_prompt = new_instructions
            ws = self._ws
            if ws is None or self._ws_is_closed(ws) or not self._connected:
                if wait_for_ack:
                    raise RuntimeError("Realtime 会话未就绪，无法确认本轮资料")
                await self._debug("update_instructions cached (session not ready)")
                return
            session_payload = {"instructions": new_instructions}
            if new_voice:
                session_payload["voice"] = new_voice
            waiter = asyncio.get_running_loop().create_future() if wait_for_ack else None
            if waiter is not None:
                self._instruction_waiter = (new_instructions, waiter)
            try:
                await self._send_event({"type": "session.update", "session": session_payload})
                if waiter is not None:
                    # 必须由读循环收到相同 instructions 的 ACK，不能只依赖本地发送顺序。
                    await asyncio.wait_for(waiter, timeout=5)
                await self._debug("session.update pushed (instructions refreshed)")
            except Exception as exc:
                await self._debug(f"update_instructions failed: {type(exc).__name__}")
                if wait_for_ack:
                    raise
            finally:
                if waiter is not None and self._instruction_waiter == (new_instructions, waiter):
                    self._instruction_waiter = None
                if waiter is not None and not waiter.done():
                    waiter.cancel()

    def _fail_instruction_update(self, message):
        pending = self._instruction_waiter
        if pending is not None and not pending[1].done():
            pending[1].set_exception(RuntimeError(message))

    def _invalidate_turn(self):
        self._turn_generation += 1
        task, self._prepare_task = self._prepare_task, None
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()

    async def _prepare_response(self, transcript, generation):
        try:
            started_at = self.loop.time()
            respond = await self.on_input_transcript(transcript)
            if respond is False or generation != self._turn_generation or not self._connected or self._fatal_error:
                return
            # 本轮资料直接随 response.create 发送，省去 session.update/ACK 往返。
            # 不写回会话默认提示词，避免下一轮继承上一轮检索资料。
            instructions = respond if isinstance(respond, str) else self.system_prompt
            await self._debug(f"turn preparation_ms={round((self.loop.time() - started_at) * 1000)}")
            await self._send_event({"type": "response.create", "response": {
                "modalities": ["text", "audio"], "instructions": instructions}},
                                   turn_generation=generation)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if generation == self._turn_generation:
                await self._debug(f"turn preparation failed: {type(exc).__name__}")
                await self.on_error("turn_preparation_failed:" + type(exc).__name__)
        finally:
            if self._prepare_task is asyncio.current_task():
                self._prepare_task = None

    async def append_audio(self, pcm_chunk: bytes):
        if not pcm_chunk:
            return
        await self.ensure_connected()
        payload = base64.b64encode(pcm_chunk).decode("ascii")
        await self._send_event({"type": "input_audio_buffer.append", "audio": payload})
        self._audio_appended_for_turn = True
        self._audio_append_count_for_turn += 1
        if self._speech_started_for_turn:
            self._audio_append_count_after_speech_started += 1
        if self._speech_started_for_turn and not self._image_sent_for_turn:
            self._schedule_image_append()

    async def send_text_turn(self, text: str):
        text = (text or "").strip()
        if not text:
            return
        await self.ensure_connected()
        self._image_sent_for_turn = False
        self._audio_appended_for_turn = False
        self._audio_append_count_for_turn = 0
        self._audio_append_count_after_speech_started = 0
        self._speech_started_for_turn = False
        await self._send_event(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            }
        )
        await self._send_event(
            {
                "type": "response.create",
                "response": {
                    "modalities": ["text", "audio"],
                    "instructions": self.system_prompt,
                },
            }
        )

    async def cancel_response(self):
        self._invalidate_turn()
        if not self._responding:
            return
        await self._send_event({"type": "response.cancel"})

    async def close(self):
        self._invalidate_turn()
        self._fail_instruction_update("Realtime 会话已关闭")
        ws = self._ws
        self._ws = None
        self._connected = False
        self._responding = False
        self._image_sent_for_turn = False
        self._audio_appended_for_turn = False
        self._audio_append_count_for_turn = 0
        self._audio_append_count_after_speech_started = 0
        self._speech_started_for_turn = False
        self._session_updated = False
        pending_task = self._pending_image_task
        self._pending_image_task = None
        task = self._message_task
        self._message_task = None
        current_task = asyncio.current_task()
        turn_tasks = [item for item in self._turn_tasks if item is not current_task]
        for item in turn_tasks:
            item.cancel()
        if turn_tasks:
            await asyncio.gather(*turn_tasks, return_exceptions=True)
        if pending_task is not None and pending_task is not current_task and not pending_task.done():
            pending_task.cancel()
            try:
                await pending_task
            except (asyncio.CancelledError, Exception):
                pass
        if ws is not None and not self._ws_is_closed(ws):
            try:
                await ws.close()
            except Exception:
                pass
        if task is not None and task is not current_task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await self._debug("session closed")

    async def _handle_messages(self):
        if self._ws is None:
            return
        try:
            async for raw in self._ws:
                event = json.loads(raw)
                event_type = event.get("type", "")
                if event_type == "error":
                    self._fail_instruction_update("Realtime 拒绝了会话更新")
                    err_payload = event.get("error", event)
                    err_msg = json.dumps(err_payload, ensure_ascii=False)
                    error_tag = provider_error_tag(err_payload)
                    self._last_error_message = error_tag
                    err_type = ""
                    err_code = ""
                    if isinstance(err_payload, dict):
                        err_type = str(err_payload.get("type", "")).lower()
                        err_code = str(err_payload.get("code", "")).lower()
                    lowered = err_msg.lower()
                    # session 还没 updated 的错，或明确的致命错误，判定为 fatal
                    fatal_signatures = (
                        "model not found",
                        "unauthorized",
                        "invalid api key",
                        "forbidden",
                        "quota",
                        "insufficient",
                    )
                    is_fatal = (not self._session_updated) or any(
                        sig in lowered for sig in fatal_signatures
                    )
                    if is_fatal:
                        self._connected = False
                        self._fatal_error = True
                        self._ready_event.set()
                        await self._debug(f"server fatal error: {error_tag}")
                    else:
                        self._responding = False
                        self._image_sent_for_turn = False
                        self._audio_appended_for_turn = False
                        self._audio_append_count_for_turn = 0
                        self._audio_append_count_after_speech_started = 0
                        self._speech_started_for_turn = False
                        self._cancel_pending_image_task()
                        await self._debug(
                            f"server recoverable error: {error_tag}"
                        )
                    await self.on_error(error_tag)
                    continue
                if event_type == "session.created":
                    self._connected = True
                    await self._debug("event session.created")
                    continue
                if event_type == "session.updated":
                    config = event.get("session") or {}
                    if self.manual_response and (config.get("turn_detection") or {}).get("create_response") is not False:
                        self._last_error_message = "模型未确认关闭自动回答，已停止资料库语音会话"
                        self._fatal_error = True
                        self._connected = False
                        self._ready_event.set()
                        self._fail_instruction_update(self._last_error_message)
                        await self.on_error("manual_response_not_supported")
                        continue
                    self._connected = True
                    self._session_updated = True
                    self._ready_event.set()
                    pending = self._instruction_waiter
                    if pending is not None and config.get("instructions") == pending[0] and not pending[1].done():
                        pending[1].set_result(None)
                    await self._debug("event session.updated")
                    continue
                if event_type == "response.created":
                    self._responding = True
                    await self._debug("event response.created")
                    await self.on_response_started()
                    continue
                if event_type == "response.done":
                    self._responding = False
                    self._image_sent_for_turn = False
                    self._audio_appended_for_turn = False
                    self._audio_append_count_for_turn = 0
                    self._audio_append_count_after_speech_started = 0
                    self._speech_started_for_turn = False
                    self._cancel_pending_image_task()
                    await self._debug("event response.done")
                    await self.on_response_done()
                    continue
                if event_type == "input_audio_buffer.speech_started":
                    self._invalidate_turn()
                    self._speech_item_id = event.get("item_id")
                    self._image_sent_for_turn = False
                    self._speech_started_for_turn = True
                    self._audio_append_count_after_speech_started = 0
                    await self._debug("event speech_started")
                    await self.on_speech_started()
                    self._schedule_image_append()
                    continue
                if event_type == "input_audio_buffer.speech_stopped":
                    await self._debug("event speech_stopped")
                    continue
                if event_type == "conversation.item.input_audio_transcription.completed":
                    transcript = (event.get("transcript") or "").strip()
                    if transcript:
                        await self._debug(f"input transcript chars={len(transcript)}")
                        if self.manual_response:
                            item_id = event.get("item_id")
                            if item_id and (item_id in self._transcribed_items or
                                    (self._speech_item_id and item_id != self._speech_item_id)):
                                continue
                            if item_id:
                                self._transcribed_items[item_id] = True
                                if len(self._transcribed_items) > 64:
                                    self._transcribed_items.pop(next(iter(self._transcribed_items)))
                            self._invalidate_turn()
                            task = asyncio.create_task(self._prepare_response(transcript, self._turn_generation),
                                                       name="realtime-prepare-response")
                            self._prepare_task = task
                            self._turn_tasks.add(task)
                            task.add_done_callback(self._turn_tasks.discard)
                        else:
                            await self.on_input_transcript(transcript)
                    continue
                if event_type in ("response.audio_transcript.delta", "response.text.delta"):
                    delta = event.get("delta") or ""
                    if delta:
                        await self.on_output_text_delta(delta)
                    continue
                if event_type == "response.audio.delta":
                    delta = event.get("delta") or ""
                    if delta:
                        try:
                            audio_bytes = base64.b64decode(delta)
                        except Exception:
                            continue
                        if audio_bytes:
                            await self._debug(f"audio delta bytes={len(audio_bytes)}")
                            await self.on_output_audio(audio_bytes)
        except ConnectionClosed as exc:
            self._connected = False
            self._responding = False
            self._last_close_message = f"code={exc.code}"
            if "Model not found" in (exc.reason or ""):
                self._fatal_error = True
            self._ready_event.set()
            await self._debug(f"connection closed code={exc.code}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._connected = False
            self._last_error_message = type(exc).__name__
            self._ready_event.set()
            await self._debug(f"message loop exception: {type(exc).__name__}")
            await self.on_error(type(exc).__name__)
        finally:
            self._connected = False
            self._invalidate_turn()
            self._fail_instruction_update("Realtime 连接已断开")
            self._ready_event.set()

    def _cancel_pending_image_task(self):
        task = self._pending_image_task
        self._pending_image_task = None
        if task is not None and not task.done():
            task.cancel()

    def _schedule_image_append(self):
        task = self._pending_image_task
        if task is not None and not task.done():
            return
        self._pending_image_task = asyncio.create_task(self._append_latest_image_when_ready())

    async def _append_latest_image_when_ready(self):
        try:
            # 视觉模式改成按需抓单帧后，需要给 ESP32 留出一点额外时间上传这帧。
            for _ in range(50):
                if self._image_sent_for_turn:
                    return
                if not self._speech_started_for_turn:
                    return
                # 关键点：必须等到 speech_started 之后又真正 append 了音频，
                # 不能拿 speech_started 之前累计的音频数来判断，否则服务端仍可能认定
                # 这张图“早于本轮音频”。
                if self._audio_append_count_after_speech_started >= 1:
                    await asyncio.sleep(0.08)
                    await self._append_latest_image()
                    if self._image_sent_for_turn:
                        return
                await asyncio.sleep(0.03)
        except asyncio.CancelledError:
            raise
        finally:
            if self._pending_image_task is asyncio.current_task():
                self._pending_image_task = None

    async def _append_latest_image(self):
        if self._ws is None or self.get_latest_image_bytes is None or self._image_sent_for_turn:
            return
        # 服务端要求本轮必须先有 audio 才能 append image，否则会回
        # invalid_request_error "Error append image before append audio"
        if not self._audio_appended_for_turn:
            await self._debug("skip image append: no audio in current turn yet")
            return
        jpeg_bytes = self.get_latest_image_bytes() or b""
        if not jpeg_bytes:
            return
        payload = base64.b64encode(jpeg_bytes).decode("ascii")
        try:
            await self._send_event({"type": "input_image_buffer.append", "image": payload})
            self._image_sent_for_turn = True
            await self._debug(
                "image append sent after "
                f"turn_audio_count={self._audio_append_count_for_turn} "
                f"post_speech_audio_count={self._audio_append_count_after_speech_started}"
            )
        except Exception as exc:
            await self._debug(f"image append failed, will retry next turn: {type(exc).__name__}")

    def _build_closed_error(self, default_message: str) -> str:
        detail = self._last_error_message or self._last_close_message or default_message
        return f"Realtime 连接不可用：{detail}"

    async def _debug(self, message: str):
        if self.on_debug is not None:
            await self.on_debug(redact_secrets(message))
