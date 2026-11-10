"""语音二次识别的账号 Key、词表、超时回退与关键数字保护。"""

import asyncio
import base64
from contextlib import closing
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,MagicMock,patch
import wave

from support import server_app
from identity_store import IdentityStore
from knowledge_store import KnowledgeStore
from memory_store import MemoryStore
from provider_settings import ProviderSettings,ModelCredentials
from speech_settings import SpeechSettings,SpeechService,apply_corrections,critical_tokens
from test_realtime_knowledge import ModelSocket,until
from omni_realtime_client import OmniRealtimeSession


class SpeechTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'memory.sqlite3'
        MemoryStore(self.path)
        ids=IdentityStore(self.path)
        self.a=ids.create_user('alice','alice-test-password').id
        self.b=ids.create_user('bob','bob-test-password').id
        self.vault=ProviderSettings(self.path)
        self.vault.save(self.a,'sk-simulated-private-alice')
        self.vault.save(self.b,'sk-simulated-private-bob')
        self.settings=SpeechSettings(self.path)
        self.transcribe=AsyncMock(return_value='请问OA请假流程')
        self.service=SpeechService(self.settings,ModelCredentials(self.vault),transcriber=self.transcribe)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_default_fast_skips_provider_and_applies_only_explicit_single_pass_corrections(self):
        result=await self.service.refine(self.a,'请问欧诶流程',b'pcm')
        self.assertFalse(result['changed'])
        self.transcribe.assert_not_awaited()
        self.settings.save(self.a,{'corrections':{'欧诶':'OA','OA':'不应链式替换'}})
        result=await self.service.refine(self.a,'请问欧诶流程',b'pcm')
        self.assertEqual(result['text'],'请问OA流程')
        self.assertEqual((await self.service.refine(self.b,'请问欧诶流程'))['text'],'请问欧诶流程')

    async def test_accurate_passes_only_owner_key_and_hotwords_and_checks_numbers(self):
        self.settings.save(self.a,{'mode':'accurate','hotwords':['OA','请假']})
        result=await self.service.refine(self.a,'请问欧诶请假流程',b'pcm')
        self.assertEqual(result['text'],'请问OA请假流程')
        options=self.transcribe.call_args.kwargs
        self.assertEqual(options['key'],'sk-simulated-private-alice')
        self.assertIn('OA',options['context'])
        self.assertNotIn('bob',options['context'])
        self.transcribe.return_value='我想周三请假'
        result=await self.service.refine(self.a,'我想周四请假',b'pcm')
        self.assertTrue(result['needs_confirmation'])
        self.assertEqual(result['text'],'我想周四请假')
        self.assertEqual(result['candidate'],'我想周三请假')

    async def test_provider_failure_rotation_and_cancellation_do_not_commit_stale_correction(self):
        self.settings.save(self.a,{'mode':'accurate'})
        self.transcribe.side_effect=ConnectionError('simulated')
        self.assertEqual((await self.service.refine(self.a,'原文',b'pcm'))['text'],'原文')
        async def rotate(*args,**kwargs):
            self.vault.save(self.a,'sk-simulated-rotated-alice')
            return '旧Key结果'
        self.transcribe.side_effect=rotate
        self.assertEqual((await self.service.refine(self.a,'原文',b'pcm'))['text'],'原文')
        self.transcribe.side_effect=asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.service.refine(self.a,'原文',b'pcm')

    def test_number_spelling_is_equivalent_but_changed_dates_and_amounts_require_confirmation(self):
        self.assertEqual(critical_tokens('星期天三个人，费用一百二十三元'),critical_tokens('周日3个人，费用123元'))
        self.assertEqual(critical_tokens('一万两千人'),critical_tokens('12000人'))
        self.assertNotEqual(critical_tokens('周三三人'),critical_tokens('周四3人'))
        self.assertNotEqual(critical_tokens('费用30元'),critical_tokens('费用三百元'))

    async def test_official_asr_payload_contains_wav_and_context_not_role_prompt(self):
        self.settings.save(self.a,{'mode':'accurate','language':'zh','hotwords':['OA']})
        service=SpeechService(self.settings,ModelCredentials(self.vault))
        client=MagicMock()
        client.__aenter__=AsyncMock(return_value=client)
        client.__aexit__=AsyncMock()
        client.chat.completions.create=AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='你好'))]))
        with patch('openai.AsyncOpenAI',return_value=client) as factory:
            await service.refine(self.a,'你好',b'\0'*3200)
        self.assertEqual(factory.call_args.kwargs['api_key'],'sk-simulated-private-alice')
        payload=client.chat.completions.create.call_args.kwargs
        self.assertEqual(payload['extra_body']['asr_options'],{'language':'zh','enable_itn':False})
        self.assertIn('实体词表',payload['messages'][0]['content'])
        data=payload['messages'][1]['content'][0]['input_audio']['data']
        with wave.open(io.BytesIO(base64.b64decode(data.split(',')[1]))) as wav:
            self.assertEqual(wav.getframerate(),16000)
            self.assertEqual(wav.getnchannels(),1)


class SpeechAudioTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_uses_vad_boundaries_and_clears_audio_on_close(self):
        socket=ModelSocket()
        callback=AsyncMock()
        session=OmniRealtimeSession(loop=asyncio.get_running_loop(),api_key='simulated-only',system_prompt='test',
            on_input_transcript=callback,on_output_text_delta=callback,on_output_audio=callback,
            on_response_started=callback,on_response_done=callback,on_speech_started=callback,on_error=callback,
            capture_transcription_audio=True,manual_response=True)
        async def connect(url,*,additional_headers=None,**kwargs):return socket
        with patch('omni_realtime_client.websockets.connect',connect):
            await session.append_audio(b'a'*32000)
            socket.emit('input_audio_buffer.speech_started',item_id='one',audio_start_ms=500)
            await until(lambda:session._capture_start==16000)
            await session.append_audio(b'b'*32000)
            socket.emit('input_audio_buffer.speech_stopped',audio_end_ms=1500)
            await until(lambda:session._capture_end==48000)
            self.assertEqual(session.transcription_audio(),b'a'*16000+b'b'*16000)
            await session.append_audio(b'c'*(26*32000))
            self.assertEqual(len(session._capture_buffer),25*32000)
            self.assertEqual(session.transcription_audio(),b'')
            await session.close()
            self.assertEqual(session._capture_buffer,b'')


if __name__=='__main__':unittest.main()
