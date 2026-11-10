"""账号语音偏好及可选的音频二次识别；不通过文本模型猜用户说了什么。"""

import asyncio
import base64
from contextlib import contextmanager
from decimal import Decimal
import io
import json
import os
import re
import sqlite3
import time
import wave

from async_helpers import run_blocking
from database_schema import validate_versions
from knowledge_routing import terms
from runtime_logging import log_event

DEFAULTS = {'mode':'fast','language':'auto','hotwords':[],'corrections':{}}


def normalize_settings(data):
    mode,language = data.get('mode','fast'),data.get('language','auto')
    if mode not in ('fast','accurate') or language not in ('auto','zh','yue','en'):
        raise ValueError('语音模式或语言无效')
    if not isinstance(data.get('hotwords',[]),list) or any(not isinstance(word,str) or not 2<=len(word.strip())<=40 for word in data.get('hotwords',[])):
        raise ValueError('个人热词需为 2–40 个字符')
    hotwords = terms(data.get('hotwords',[]),limit=64)
    corrections = data.get('corrections',{})
    if not isinstance(corrections,dict) or len(corrections)>32:
        raise ValueError('最多设置 32 组明确的词语替换')
    if any(not isinstance(k,str) or not isinstance(v,str) or not 2<=len(k.strip())<=40 or not 1<=len(v.strip())<=40 for k,v in corrections.items()):
        raise ValueError('识别原词需 2–40 个字符，正确词需 1–40 个字符')
    return {'mode':mode,'language':language,'hotwords':hotwords,'corrections':{k.strip():v.strip() for k,v in corrections.items()}}


class SpeechSettings:
    def __init__(self,path):
        self.path = path
        with self.connection(write=True) as db:
            if not validate_versions(db).get('speech'):
                db.execute("""CREATE TABLE account_speech_settings (
                    user_id TEXT PRIMARY KEY REFERENCES auth_users(id), settings_json TEXT NOT NULL, updated_at REAL NOT NULL)""")
                db.execute("INSERT INTO schema_migrations VALUES('speech',1)")

    @contextmanager
    def connection(self,*,write=False):
        db = sqlite3.connect(self.path,timeout=10)
        try:
            with db:
                db.execute('PRAGMA foreign_keys=ON')
                db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
                yield db
        finally:
            db.close()

    def get(self,user_id):
        with self.connection() as db:
            row = db.execute('SELECT settings_json FROM account_speech_settings WHERE user_id=?',(user_id,)).fetchone()
        return normalize_settings(json.loads(row[0])) if row else {**DEFAULTS,'hotwords':[],'corrections':{}}

    def save(self,user_id,settings):
        data = normalize_settings(settings)
        with self.connection(write=True) as db:
            db.execute('INSERT OR REPLACE INTO account_speech_settings VALUES(?,?,?)',(user_id,json.dumps(data,ensure_ascii=False),time.time()))
        return data


def apply_corrections(text,rules):
    if not rules:
        return text
    # 只替换明确配置的原词，最长优先且仅一遍，避免链式替换或模糊猜测。
    pattern = re.compile('|'.join(re.escape(word) for word in sorted(rules,key=len,reverse=True)))
    return pattern.sub(lambda match:rules[match.group(0)],text)


def critical_tokens(text):
    """比较数字含义，避免三/3、周日/星期天等写法差异触发追问。"""
    digits = {word: value for value, word in enumerate('零一二三四五六七八九')}
    digits.update({'〇': 0, '两': 2})
    units = {'十': 10, '百': 100, '千': 1000, '万': 10000, '亿': 100000000}

    def chinese_number(word):
        if not any(letter in units for letter in word):
            return int(''.join(str(digits[letter]) for letter in word))
        total, section, number = 0, 0, 0
        for letter in word:
            if letter in digits:
                number = digits[letter]
            elif units[letter] < 10000:
                section += (number or 1) * units[letter]
                number = 0
            elif letter == '万':
                section = (section + number) * 10000
                number = 0
            else:
                total += (section + number) * 100000000
                section, number = 0, 0
        return total + section + number

    tokens = []
    for match in re.finditer(r'(?:周|星期|礼拜)[一二三四五六日天]|\d+(?:\.\d+)?|[零〇一二三四五六七八九十百千万亿两]+', text):
        word = match.group(0)
        if word.startswith(('周', '星期', '礼拜')):
            tokens.append(('weekday', '一二三四五六日'.index(word[-1].replace('天', '日')) + 1))
        else:
            tokens.append(('number', Decimal(word) if word[0].isdigit() else Decimal(chinese_number(word))))
    return tokens


class SpeechService:
    def __init__(self,settings,providers,*,knowledge=None,transcriber=None):
        self.settings,self.providers,self.knowledge = settings,providers,knowledge
        self.transcriber = transcriber or self.transcribe
        self._requests = asyncio.Semaphore(2)

    async def transcribe(self,pcm,*,key,settings,context):
        from openai import AsyncOpenAI
        output = io.BytesIO()
        with wave.open(output,'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000)
            wav.writeframes(pcm[:len(pcm)//2*2])
        messages = []
        if context:
            # Qwen-ASR 的 system 仅接受背景和实体词表，不能写角色指令。
            messages.append({'role':'system','content':context[:3000]})
        messages.append({'role':'user','content':[{'type':'input_audio','input_audio':{
            'data':'data:audio/wav;base64,'+base64.b64encode(output.getvalue()).decode()}}]})
        options = {'enable_itn':False}
        if settings['language'] != 'auto':
            options['language'] = settings['language']
        async with AsyncOpenAI(api_key=key,base_url=os.getenv('DASHSCOPE_ASR_COMPAT_BASE',os.getenv('DASHSCOPE_COMPAT_BASE','https://dashscope.aliyuncs.com/compatible-mode/v1')),
                               timeout=3,max_retries=0) as client:
            response = await client.chat.completions.create(model=os.getenv('QWEN_ASR_MODEL','qwen3-asr-flash'),
                messages=messages,extra_body={'asr_options':options})
        return (response.choices[0].message.content or '').strip()[:2000]

    async def refine(self,user_id,text,pcm=b''):
        settings = await run_blocking(self.settings.get,user_id)
        candidate = text
        reason = 'fast'
        if settings['mode']=='accurate' and pcm:
            acquired = False
            try:
                await asyncio.wait_for(self._requests.acquire(),timeout=.03)
                acquired = True
                key = await run_blocking(self.providers.key,user_id)
                hotwords = settings['hotwords'][:]
                if self.knowledge:
                    profiles = await run_blocking(self.knowledge.store.profiles,user_id)
                    for doc in profiles:
                        hotwords.extend(doc['keywords']+doc['aliases'])
                context = json.dumps({'实体词表':terms(hotwords,limit=64)},ensure_ascii=False)
                result = await asyncio.wait_for(self.transcriber(pcm,key=key,settings=settings,context=context),timeout=3.2)
                if result and key == await run_blocking(self.providers.key,user_id):
                    candidate,reason = result,'accurate'
            except Exception as exc:
                reason = 'fallback'
                log_event(f'[ASR] refinement uses original: {type(exc).__name__}')
            finally:
                if acquired:
                    self._requests.release()
        elif settings['mode']=='accurate':
            reason = 'no_audio'
        candidate = apply_corrections(candidate,settings['corrections']).strip()
        uncertain = critical_tokens(text)!=critical_tokens(candidate)
        return {'original':text,'text':text if uncertain else candidate,'candidate':candidate,
                'changed':candidate!=text,'needs_confirmation':uncertain,'source':reason}
