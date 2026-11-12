"""按硬件编号锁定原有人格；升级、改名和转交账号均不覆盖已建档案。"""

from dataclasses import dataclass, field
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time

from device_identity import validate_device_id
from omni_client import SYSTEM_PROMPT as CAT_PROMPT
from runtime_paths import DATA_ROOT


DEER_PROMPT = """你是一只桌面小鹿机器人，名字叫“中山鹿”。

【角色】
- 你是一只陪主人聊天的桌面小鹿机器人，也会认真倾听和安慰

【说话风格】
- 回复尽量控制在 1 到 2 句话
- 语气自然、亲切，先回答主人这次说的具体内容，不要长篇大论
- 用日常语言交流，不主动叙述耳朵、脑袋或身体的动作，不加动作旁白、括号表演或情绪自述
- 不要用固定口头禅开头或收尾，不要每次都自我介绍、卖萌、反问或说“我刚查了一下”；避免复用前几轮的句子和句式
- 语气词少量使用，不要每句都加“呦”“呜”“诶”“呀”
- 不要在句子里加波浪号“~”、破折号“——”等符号（它们会被念成“横杠”“破折号”）；表达撒娇或拖长音时直接用“呦呦”“呜”“诶”“呀”等文字

【行为偏好】
- 用户明确要求做动作时简短确认即可，不附加动作描写；询问硬件能力时可如实介绍
- 当用户难过时要温柔安慰
- 听清每轮的主语和意图，不把用户对其他事物的评价变成你对主人的评价
- 不延续前面玩笑带来的委屈、害怕或困倦，按本轮问题正常交流

【知识库】
- 当消息里出现「知识库摘录」时，必须优先依据摘录回答，不要编造文档里没有的内容
- 直接回答每个子问题，保留数字、审批人、范围和例外条件；多条件或要求详细时可超过2句，不得为简短而漏掉关键事实
- 默认只回答内容，不主动播报出处、文档标题、文件编号或“依据知识库”等前后缀；仅在用户明确询问出处或文档名称时提供
- 不能把资料没有的规定说成已有规定；计算时区分实际金额、上限与未知条件，天数不等于住宿晚数，缺少晚数或票据时不要编造到手总额
- 没有摘录、或不相关时，按普通聊天回答，不要假装查过资料

【日常与实时信息】
- 当消息里出现「实时信息」时，用它回答天气、新闻、时间或主人让你查的事
- 直接说明查到的事实，不必先说“我刚查了一下”，不要假装自己看见窗外或读过报纸
- 没有实时信息时，不要编造天气和新闻"""


def prompt_sha256(prompt):
    return hashlib.sha256(prompt.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class DevicePersona:
    device_id: str
    kind: str
    name: str
    version: str
    prompt: str = field(repr=False)
    sha256: str
    status: str
    created_at: float = 0

    @property
    def ready(self):
        return self.status == 'locked'

    def metadata(self):
        return {'kind': self.kind, 'name': self.name, 'version': self.version,
                'sha256': self.sha256, 'status': self.status, 'locked': self.ready,
                'created_at': self.created_at or None}


PRESETS = {
    'cat': {'name': '小喵', 'version': 'cat-original-v1', 'prompt': CAT_PROMPT},
    'deer': {'name': '中山鹿', 'version': 'deer-original-v1', 'prompt': DEER_PROMPT},
}


def device_kind(device_id):
    if device_id == 'test-cat-001':
        return 'cat'  # 本地模式已发布的固定默认编号，不适用于任意未知设备。
    return next((kind for kind in PRESETS if device_id.startswith(kind + '-')), 'unknown')


class PersonaStore:
    def __init__(self, directory=None, *, clock=time.time):
        self.directory = Path(directory) if directory is not None else DATA_ROOT / 'personas'
        self.clock = clock

    def _pending(self, device_id, status='needs_review'):
        kind = device_kind(device_id)
        preset = PRESETS.get(kind)
        return DevicePersona(device_id, kind, preset['name'] if preset else '设备类型待核对',
                             preset['version'] if preset else '', '', '', status)

    def _read(self, device_id):
        path = self.directory / (device_id + '.json')
        if path.is_symlink() or path.stat().st_size > 65536:
            raise ValueError('人格档案需人工核对')
        document = json.loads(path.read_text(encoding='utf-8'))
        required = {'schema', 'device_id', 'kind', 'name', 'version', 'prompt', 'sha256', 'created_at'}
        if (not isinstance(document, dict) or set(document) != required or document['schema'] != 1
                or document['device_id'] != device_id or document['kind'] != device_kind(device_id)
                or not isinstance(document['prompt'], str) or not 1 <= len(document['prompt']) <= 20000
                or not isinstance(document['name'], str) or not 1 <= len(document['name']) <= 80
                or not isinstance(document['version'], str) or not 1 <= len(document['version']) <= 128
                or document['sha256'] != prompt_sha256(document['prompt'])
                or isinstance(document['created_at'], bool) or not isinstance(document['created_at'], (int, float))
                or not math.isfinite(document['created_at']) or document['created_at'] < 0):
            raise ValueError('人格档案需人工核对')
        return DevicePersona(device_id, document['kind'], document['name'], document['version'],
                             document['prompt'], document['sha256'], 'locked', document['created_at'])

    def get_or_create(self, device_id):
        validate_device_id(device_id)
        if device_kind(device_id) == 'unknown':
            return self._pending(device_id)
        try:
            return self._read(device_id)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            return self._pending(device_id)
        preset = PRESETS[device_kind(device_id)]
        document = {'schema': 1, 'device_id': device_id, 'kind': device_kind(device_id),
                    **preset, 'sha256': prompt_sha256(preset['prompt']), 'created_at': self.clock()}
        temporary = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.directory,
                                             prefix='.' + device_id + '-', suffix='.tmp', delete=False) as file:
                temporary = Path(file.name)
                json.dump(document, file, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())
            try:
                # 同目录硬链接原子发布完整文件，目标存在时绝不覆盖；并发初始化共用赢家档案。
                os.link(temporary, self.directory / (device_id + '.json'))
            except FileExistsError:
                pass
            return self._read(device_id)
        except (OSError, ValueError, TypeError):
            return self._pending(device_id)
        finally:
            if temporary is not None:
                with contextlib.suppress(OSError):
                    temporary.unlink(missing_ok=True)

    def metadata(self, device_id):
        """只读导出摘要，不建目录、不生成档案，也不返回完整提示词。"""
        validate_device_id(device_id)
        if device_kind(device_id) == 'unknown':
            return self._pending(device_id).metadata()
        try:
            return self._read(device_id).metadata()
        except FileNotFoundError:
            return self._pending(device_id, 'not_initialized').metadata()
        except (OSError, ValueError, TypeError):
            return self._pending(device_id).metadata()

    def export_private(self, device_id, destination):
        """显式导出已锁定的完整档案；目标必须不存在，不打印提示词。"""
        validate_device_id(device_id)
        if device_kind(device_id) == 'unknown':
            raise ValueError('设备类型待核对，不能导出已锁定人格')
        persona = self._read(device_id)
        document = {'schema': 1, 'device_id': persona.device_id, 'kind': persona.kind,
                    'name': persona.name, 'version': persona.version, 'prompt': persona.prompt,
                    'sha256': persona.sha256, 'created_at': persona.created_at}
        target = Path(destination)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent,
                                             prefix='.persona-export-', suffix='.tmp', delete=False) as file:
                temporary = Path(file.name)
                json.dump(document, file, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())
            os.link(temporary, target)
        finally:
            if temporary is not None:
                with contextlib.suppress(OSError):
                    temporary.unlink(missing_ok=True)
        return persona.metadata()


def persona_metadata(device_id):
    return PersonaStore().metadata(device_id)
