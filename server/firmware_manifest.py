"""OTA 发布格式与软件签名；私钥只用于离线发布，不进入 Web 服务或设备。"""
import base64
import hashlib
import json
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

HARDWARE = 'cybermiao-s3-8m-opi-same-wiring'
LAYOUT = 'ab-2m-v1'
MAX_IMAGE = 0x200000
VERSION = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?\Z')
RELEASE_ID = re.compile(r'[a-z0-9][a-z0-9._-]{0,95}\Z')


def signing_bytes(manifest):
    if not isinstance(manifest, dict):
        raise ValueError('固件发布清单必须为对象')
    if manifest.get('schema') != 1 or manifest.get('profile') != 'deer':
        raise ValueError('本轮 OTA 仅支持小鹿发布格式 1')
    if manifest.get('hardware') != HARDWARE or manifest.get('layout') != LAYOUT:
        raise ValueError('固件硬件或分区格式不兼容')
    if not RELEASE_ID.fullmatch(manifest.get('id', '')) or not VERSION.fullmatch(manifest.get('version', '')):
        raise ValueError('固件版本或发布编号无效')
    if type(manifest.get('sequence')) is not int or not 1 <= manifest['sequence'] <= 2147483647:
        raise ValueError('固件发布序号无效')
    if type(manifest.get('size')) is not int or not 256 <= manifest['size'] <= MAX_IMAGE:
        raise ValueError('固件大小超出升级槽容量')
    if not re.fullmatch(r'[0-9a-f]{64}', manifest.get('sha256', '')):
        raise ValueError('固件摘要无效')
    notes = manifest.get('notes', '')
    if not isinstance(notes, str) or len(notes.encode('utf-8')) > 4000:
        raise ValueError('更新说明长度无效')
    notes_hash = hashlib.sha256(notes.encode('utf-8')).hexdigest()
    if manifest.get('notes_sha256') != notes_hash:
        raise ValueError('更新说明摘要不一致')
    # 固定顺序与换行，设备端可直接实现，不依赖 JSON 序列化细节。
    fields = ['cybermiao-ota-v1', manifest['id'], manifest['profile'], manifest['version'],
              str(manifest['sequence']), manifest['sha256'], str(manifest['size']), HARDWARE, LAYOUT, notes_hash]
    return ('\n'.join(fields) + '\n').encode('ascii')


def verify_manifest(manifest, public_key):
    signed = signing_bytes(manifest)
    try:
        signature = base64.b64decode(manifest['signature'], validate=True)
        key = serialization.load_pem_public_key(public_key)
        if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
            raise ValueError('OTA 公钥必须使用 P-256')
        key.verify(signature, signed, ec.ECDSA(hashes.SHA256()))
    except (KeyError, TypeError, InvalidSignature, ValueError):
        raise ValueError('固件发布签名验证失败') from None
    return manifest


def verify_image(path, manifest):
    if path.is_symlink() or not path.is_file() or path.stat().st_size != manifest['size']:
        raise ValueError('固件文件缺失或大小不一致')
    data = path.read_bytes()
    if data[0] != 0xE9 or int.from_bytes(data[12:14], 'little') != 9:
        raise ValueError('只允许 ESP32-S3 主程序镜像')
    if hashlib.sha256(data).hexdigest() != manifest['sha256']:
        raise ValueError('固件文件摘要不一致')
    marker = f"CYBERMIAO_OTA:{manifest['profile']}:{manifest['version']}:{manifest['sequence']}".encode()
    if marker not in data:
        raise ValueError('镜像内设备类型及版本与发布说明不一致')


def read_package(directory, public_key):
    manifest_path = directory / 'manifest.json'
    if directory.is_symlink() or manifest_path.is_symlink() or manifest_path.stat().st_size > 8192:
        raise ValueError('固件包路径或清单大小无效')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    verify_manifest(manifest, public_key)
    verify_image(directory / 'application.bin', manifest)
    return manifest
