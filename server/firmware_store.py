"""持久化远程升级意图与设备回执，离线设备上线后领取；不保存模型或网络凭证。"""
from contextlib import contextmanager
import json
import sqlite3
import time
import uuid

from firmware_manifest import read_package
from database_schema import FIRMWARE_COLUMNS, FIRMWARE_SCHEMA_VERSION, validate_columns, validate_versions

BUSY = {'queued', 'downloading', 'verifying', 'rebooting', 'pending_validation'}
STATES = BUSY | {'idle', 'updated', 'failed', 'rolled_back'}


class FirmwareStore:
    def __init__(self, path, release_root, public_key_path, *, clock=time.time):
        self.path, self.release_root, self.public_key_path, self.clock = path, release_root, public_key_path, clock
        self._release_cache = {}
        with self.connect(write=True) as db:
            version = validate_versions(db).get('firmware', 0)
            if not version:
                names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if names.intersection(FIRMWARE_COLUMNS):
                    raise ValueError('升级数据库存在未登记表，请先检查迁移')
                schema = '''
                CREATE TABLE IF NOT EXISTS firmware_devices (
                    device_id TEXT PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
                    report_json TEXT NOT NULL, reported_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS firmware_preferences (
                    device_id TEXT PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
                    owner_user_id TEXT NOT NULL, auto_update INTEGER NOT NULL DEFAULT 0,
                    excluded_release TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS firmware_commands (
                    id TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                    owner_user_id TEXT NOT NULL, access_epoch INTEGER NOT NULL,
                    action TEXT NOT NULL, release_id TEXT NOT NULL, state TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0, error_code TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL, updated_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_firmware_device_commands ON firmware_commands(device_id,created_at);
                '''
                for statement in schema.strip().split(';'):
                    if statement.strip():
                        db.execute(statement)
                db.execute("INSERT INTO schema_migrations(component,version) VALUES('firmware',?)", (FIRMWARE_SCHEMA_VERSION,))
            validate_columns(db, FIRMWARE_COLUMNS)

    @contextmanager
    def connect(self, *, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def releases(self):
        if not self.public_key_path.is_file() or not self.release_root.is_dir():
            return []
        public_key = self.public_key_path.read_bytes()
        result = []
        for directory in sorted(self.release_root.iterdir()):
            if not directory.is_dir() or directory.name.startswith('.'):
                continue
            try:
                files = [directory / 'manifest.json', directory / 'application.bin', self.public_key_path]
                fingerprint = tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size) for path in files)
                manifest = self._release_cache.get(fingerprint)
                if manifest is None:
                    manifest = read_package(directory, public_key)
                    if len(self._release_cache) >= 64:
                        self._release_cache.clear()
                    self._release_cache[fingerprint] = manifest
                if directory.name != manifest['id']:
                    continue
                result.append(manifest)
            except (ValueError, OSError, KeyError, TypeError):
                continue  # 未签名、未完整上传、篡改或不兼容的包不对设备发布。
        return sorted(result, key=lambda item: item['sequence'], reverse=True)

    @staticmethod
    def _latest(db, device_id):
        row = db.execute('SELECT * FROM firmware_commands WHERE device_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1', (device_id,)).fetchone()
        return dict(row) if row else None

    def _view(self, db, device):
        row = db.execute('SELECT * FROM firmware_devices WHERE device_id=?', (device['id'],)).fetchone()
        report = json.loads(row['report_json']) if row else None
        preference = db.execute('SELECT * FROM firmware_preferences WHERE device_id=? AND owner_user_id=?', (device['id'], device['owner_user_id'])).fetchone()
        command = self._latest(db, device['id'])
        if command and (command['owner_user_id'] != device['owner_user_id'] or command['access_epoch'] != device['access_epoch']):
            command = None
        excluded = preference['excluded_release'] if preference else ''
        available = next((release for release in self.releases() if release['sequence'] > (report or {}).get('sequence', 0)), None)
        online = bool(row and self.clock() - row['reported_at'] < 45)
        return {'supported': bool(device['id'].startswith('deer-') and report and report.get('ota_capable')),
                'online': online, 'reported_at': row['reported_at'] if row else None,
                'current': report, 'available': available, 'command': command,
                'auto_update': bool(preference and preference['auto_update']),
                'automatic_blocked': bool(available and available['id'] == excluded),
                'borrowed': device.get('active_user_id') != device['owner_user_id'],
                'idle_seconds': 300}

    def view(self, device):
        with self.connect() as db:
            return self._view(db, device)

    def set_policy(self, device, auto_update):
        with self.connect(write=True) as db:
            db.execute('''INSERT INTO firmware_preferences(device_id,owner_user_id,auto_update) VALUES(?,?,?)
                ON CONFLICT(device_id) DO UPDATE SET owner_user_id=excluded.owner_user_id,auto_update=excluded.auto_update,
                excluded_release=CASE WHEN firmware_preferences.owner_user_id=excluded.owner_user_id THEN firmware_preferences.excluded_release ELSE '' END''',
                (device['id'], device['owner_user_id'], bool(auto_update)))
        return self.view(device)

    def request(self, device, action, *, release_id='', automatic=False):
        if not device['id'].startswith('deer-'):
            raise ValueError('本轮只向小鹿开放远程升级')
        with self.connect(write=True) as db:
            state = self._view(db, device)
            if not state['supported']:
                raise ValueError('请先通过 USB 安装支持双分区的 OTA 首版')
            if state['command'] and state['command']['state'] in BUSY:
                return state['command']  # 并发点击只产生一个持久命令。
            if action == 'install':
                release = state['available']
                if not release or (release_id and release['id'] != release_id):
                    raise ValueError('没有匹配的新版固件，请先刷新版本信息')
                release_id = release['id']
            elif action == 'rollback':
                if not state['current'].get('rollback_available'):
                    raise ValueError('设备没有已验证的可回退固件')
                release_id = state['current'].get('previous_version', '')
                # 主动回退表示不希望自动装回当前发布；手动仍可再次升级。
                excluded = next((item['id'] for item in self.releases() if item['sequence'] == state['current']['sequence']), '')
                db.execute('''INSERT INTO firmware_preferences(device_id,owner_user_id,excluded_release) VALUES(?,?,?)
                    ON CONFLICT(device_id) DO UPDATE SET owner_user_id=excluded.owner_user_id,excluded_release=excluded.excluded_release''',
                    (device['id'], device['owner_user_id'], excluded))
            else:
                raise ValueError('不支持的升级操作')
            command_id, now = uuid.uuid4().hex, self.clock()
            db.execute('INSERT INTO firmware_commands VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (command_id, device['id'], device['owner_user_id'], device['access_epoch'], action, release_id, 'queued', 0, '', now, now))
            return self._latest(db, device['id'])

    def check(self, device, report):
        with self.connect(write=True) as db:
            db.execute('INSERT INTO firmware_devices VALUES(?,?,?) ON CONFLICT(device_id) DO UPDATE SET report_json=excluded.report_json,reported_at=excluded.reported_at',
                       (device['id'], json.dumps(report, ensure_ascii=False), self.clock()))
            command = self._latest(db, device['id'])
            if command and (command['owner_user_id'] != device['owner_user_id'] or command['access_epoch'] != device['access_epoch']):
                db.execute("UPDATE firmware_commands SET state='failed',error_code='ownership_changed',updated_at=? WHERE id=? AND state IN ('queued','downloading','verifying','rebooting','pending_validation')", (self.clock(), command['id']))
                command = None
            if command and report.get('command_id') == command['id'] and report['state'] in STATES - {'idle', 'queued'} and command['state'] in BUSY:
                if command['action'] == 'rollback' and report['state'] == 'updated':
                    raise ValueError('回退任务不能用升级完成回执结束')
                target = next((item for item in self.releases() if item['id'] == command['release_id']), None)
                if report['state'] == 'updated' and command['action'] == 'install' and (not target or report['version'] != target['version'] or report['sequence'] != target['sequence']):
                    raise ValueError('升级完成回执与目标版本不一致')
                if report['state'] == 'rolled_back' and command['action'] == 'rollback' and report['version'] != command['release_id']:
                    raise ValueError('回退完成回执与目标版本不一致')
                if report['state'] == 'rolled_back' and command['action'] == 'install' and target and report['sequence'] >= target['sequence']:
                    raise ValueError('自动回退回执仍在目标版本')
                progress = max(command['progress'], min(100, report['progress']))
                db.execute('UPDATE firmware_commands SET state=?,progress=?,error_code=?,updated_at=? WHERE id=?',
                           (report['state'], progress, report.get('error_code', ''), self.clock(), command['id']))
                if report['state'] in ('rolled_back', 'failed') and command['action'] == 'install':
                    db.execute('''INSERT INTO firmware_preferences(device_id,owner_user_id,excluded_release) VALUES(?,?,?)
                        ON CONFLICT(device_id) DO UPDATE SET owner_user_id=excluded.owner_user_id,excluded_release=excluded.excluded_release''',
                        (device['id'], device['owner_user_id'], command['release_id']))
            state = self._view(db, device)
        # 自动模式仅在有主人、能力就绪且设备本地连续空闲五分钟时安排。
        if device['owner_user_id'] and device['active_user_id'] == device['owner_user_id'] and state['auto_update'] and not state['automatic_blocked'] and report.get('idle_seconds', 0) >= 300 and not report['busy'] and state['available'] and not (state['command'] and state['command']['state'] in BUSY):
            self.request(device, 'install', automatic=True)
            state = self.view(device)
        command = state['command']
        if not device['active_user_id'] or device['active_user_id'] != device['owner_user_id'] or not state['supported'] or not command or command['state'] not in BUSY:
            return {'command': None}
        if command['action'] == 'rollback':
            return {'command': {'id': command['id'], 'action': 'rollback'}}
        release = next((item for item in self.releases() if item['id'] == command['release_id']), None)
        return {'command': {'id': command['id'], 'action': 'install', 'manifest': release}} if release else {'command': None}

    def image(self, device, command_id):
        with self.connect() as db:
            command = self._latest(db, device['id'])
        if not command or command['id'] != command_id or command['action'] != 'install' or command['state'] not in BUSY or command['access_epoch'] != device['access_epoch'] or command['owner_user_id'] != device['owner_user_id'] or device['active_user_id'] != device['owner_user_id']:
            raise ValueError('升级任务已失效')
        release = next((item for item in self.releases() if item['id'] == command['release_id']), None)
        if release is None:
            raise ValueError('固件已撤回或验证失败')
        return self.release_root / release['id'] / 'application.bin'
