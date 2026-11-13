"""从已提交源码构建小鹿双分区版本；只构建，不枚举串口或烧录。"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from fleet_firmware import FQBN, CORE_VERSION, FleetError, digest, run_git, verify_sources, write_json

ROOT = Path(__file__).resolve().parents[2]


def write_changed(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file() or path.read_bytes() != data:
        path.write_bytes(data)


def build(args):
    from sys import path
    path.insert(0, str(ROOT / 'server'))
    from firmware_manifest import VERSION
    if not VERSION.fullmatch(args.version) or not 1 <= args.sequence <= 2147483647:
        raise FleetError('固件版本或序号无效')
    if CORE_VERSION not in subprocess.run([str(args.cli), 'core', 'list'], capture_output=True, check=True, timeout=30).stdout.decode():
        raise FleetError('未安装已审核 ESP32 核心')
    commit = run_git(ROOT, 'rev-parse', 'HEAD').decode().strip()
    source = args.work / 'build-source' / 'esp32_servo_control'
    names = run_git(ROOT, 'ls-tree', '-r', '--name-only', 'HEAD', 'firmware/esp32_servo_control').decode().splitlines()
    expected = set()
    for name in names:
        if Path(name).suffix not in {'.ino', '.h', '.cpp', '.csv'}:
            continue
        target = source / Path(name).relative_to('firmware/esp32_servo_control')
        expected.add(target.resolve()); write_changed(target, run_git(ROOT, 'show', 'HEAD:' + name))
    # 版本来自独立构建参数，主源码和猫的旧分区不被覆盖。
    generated = source / 'FirmwareBuildVersion.h'; expected.add(generated.resolve())
    write_changed(generated, (f'#pragma once\n#define CYBERMIAO_FIRMWARE_VERSION "{args.version}"\n#define CYBERMIAO_FIRMWARE_SEQUENCE {args.sequence}\n').encode())
    for path in source.rglob('*'):
        if path.is_file() and path.resolve() not in expected:
            raise FleetError('构建源码目录包含多余文件，请使用新的工作目录')
    write_changed(source / 'partitions.csv', run_git(ROOT, 'show', 'HEAD:firmware/partitions-ota.csv'))
    output = args.work / 'build-deer'; output.mkdir(parents=True, exist_ok=True)
    with (args.work / 'build-deer.log').open('wb') as log:
        result = subprocess.run([str(args.cli), 'compile', '--fqbn', FQBN,
            '--build-property', 'upload.maximum_size=2097152',
            '--build-property', 'compiler.cpp.extra_flags=-DCYBERMIAO_DEVICE_DEER=1',
            '--build-path', str(output.resolve()), '--warnings', 'default', '--jobs', str(args.jobs), str(source.resolve())],
            stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise FleetError('编译失败，请检查本地 compile.log；未访问硬件')
    hashes = verify_sources(ROOT, output)
    files = {name: digest((output / ('esp32_servo_control.ino.' + suffix)).read_bytes()) for name, suffix in
             (('application','bin'),('partitions','partitions.bin'),('bootloader','bootloader.bin'))}
    write_json(args.work / f'build-receipt-{args.sequence}.json', {'commit':commit,'version':args.version,'sequence':args.sequence,
        'profile':'deer','layout':'ab-2m-v1','source_files':hashes,'build_version_sha256':digest(generated.read_bytes()),
        'artifacts':files,'flash_written':False})
    print(json.dumps({'ok':True,'commit':commit,'version':args.version,'application_sha256':files['application'],'flash_written':False}))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli',type=Path,required=True)
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--version',required=True)
    parser.add_argument('--sequence',type=int,required=True)
    parser.add_argument('--jobs',type=int,default=4,choices=range(1,9))
    build(parser.parse_args())


if __name__=='__main__':
    main()
