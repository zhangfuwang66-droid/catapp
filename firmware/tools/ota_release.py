"""离线生成 OTA 签名密钥及发布包；不访问服务器、不打开串口。"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'server'))
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from firmware_manifest import signing_bytes, verify_image, HARDWARE, LAYOUT


def keygen(args):
    from fleet_firmware import make_private_directory
    make_private_directory(args.private.parent)
    key = ec.generate_private_key(ec.SECP256R1())
    with args.private.open('xb') as stream:
        stream.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    with args.public.open('xb') as stream:
        stream.write(public)
    header = '#pragma once\n// 离线发布者公钥；私钥不得进入设备或服务器。\n'
    header += 'static constexpr const char CYBERMIAO_OTA_PUBLIC_KEY[] = R"OTAKEY(' + public.decode('ascii') + ')OTAKEY";\n'
    with args.header.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(header)
    print(json.dumps({'public_key_sha256': hashlib.sha256(public).hexdigest(), 'private_key_printed': False}))


def package(args):
    data = args.image.read_bytes()
    manifest = {'schema': 1, 'id': f'deer-{args.version}', 'profile': 'deer', 'version': args.version,
                'sequence': args.sequence, 'hardware': HARDWARE, 'layout': LAYOUT,
                'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                'notes': args.notes.read_text(encoding='utf-8').strip()}
    manifest['notes_sha256'] = hashlib.sha256(manifest['notes'].encode('utf-8')).hexdigest()
    signed = signing_bytes(manifest)
    verify_image(args.image, manifest)
    key = serialization.load_pem_private_key(args.private.read_bytes(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError('发布密钥必须为 P-256')
    manifest['signature'] = base64.b64encode(key.sign(signed, ec.ECDSA(hashes.SHA256()))).decode('ascii')
    args.output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(args.image, args.output / 'application.bin')
    (args.output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'release_id': manifest['id'], 'bytes': len(data), 'sha256': manifest['sha256']}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    keys = commands.add_parser('keygen')
    keys.add_argument('--private', type=Path, required=True)
    keys.add_argument('--public', type=Path, required=True)
    keys.add_argument('--header', type=Path, required=True)
    keys.set_defaults(run=keygen)
    release = commands.add_parser('package')
    for name in ('image', 'private', 'notes', 'output'):
        release.add_argument('--' + name, type=Path, required=True)
    release.add_argument('--version', required=True)
    release.add_argument('--sequence', type=int, required=True)
    release.set_defaults(run=package)
    args = parser.parse_args()
    args.run(args)


if __name__ == '__main__':
    main()
