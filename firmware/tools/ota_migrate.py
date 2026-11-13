"""小鹿首次 OTA 分区迁移。默认仅核验离线材料；没有明确执行开关时不访问硬件。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time
import zlib

from fleet_firmware import (Esptool, FleetError, FLASH_BYTES, PARTITIONS, TABLE_OFFSET, TABLE_BYTES,
    create_snapshot, digest, make_private_directory, partition_sector, probe_device, read_json,
    validate_device, validate_image, verify_sources, write_json)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'server'))
from firmware_manifest import read_package, LAYOUT

NVS_START, NVS_SIZE = 0x9000, 0x5000
OLD_FS_START, OLD_FS_SIZE = 0x290000, 0x560000
NEW_FS_START, NEW_FS_SIZE = 0x410000, 0x3F0000
OTA_PARTITIONS = (
    ('nvs', 1, 2, NVS_START, NVS_SIZE), ('otadata', 1, 0, 0xE000, 0x2000),
    ('app0', 0, 0x10, 0x10000, 0x200000), ('app1', 0, 0x11, 0x210000, 0x200000),
    ('spiffs', 1, 0x82, NEW_FS_START, NEW_FS_SIZE),
)
REGIONS = {'bootloader.bin':(0,0x8000),'partitions.bin':(0x8000,0x1000),
           'otadata.bin':(0xE000,0x2000),'app0.bin':(0x10000,0x200000),
           'app1.bin':(0x210000,0x200000),'assets.secret.bin':(NEW_FS_START,NEW_FS_SIZE)}


def ota_table(data):
    """沿用旧工具严格 MD5 校验，但只在此迁移器允许新分区。"""
    return partition_sector(data, expected_partitions=OTA_PARTITIONS)


def initial_otadata():
    # ESP-IDF esp_ota_select_entry_t：CRC32 只覆盖 ota_seq，seq=1 对应 app0。
    # 首次 USB 安装没有兼容旧槽，app0 从 VALID 启动；后续 OTA 才走 NEW/PENDING_VERIFY。
    entry = struct.pack('<I20sII',1,b'\xff'*20,2,zlib.crc32(struct.pack('<I',1),0xFFFFFFFF)&0xFFFFFFFF)
    return entry.ljust(0x2000,b'\xff')


def files_manifest(directory):
    result={}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise FleetError('资源目录包含链接，不能迁移')
        if path.is_file():
            data=path.read_bytes()
            result[path.relative_to(directory).as_posix()]={'bytes':len(data),'sha256':digest(data)}
    if not 1 <= len(result) <= 1024 or sum(item['bytes'] for item in result.values()) > NEW_FS_SIZE:
        raise FleetError('原资源数量或容量不能放入新的文件系统')
    return result


def fs_command(tool, operation, directory, image, size):
    args=[str(tool),operation,str(directory),'-b','4096','-p','256','-s',str(size),'-d','0',str(image)]
    result=subprocess.run(args,capture_output=True,timeout=120)
    if result.returncode:
        raise FleetError('LittleFS 资源搬迁失败，未访问硬件；保留私密工作目录检查')


def prepare(*, backup, old_image, release, public_key, build, littlefs, output, device_id):
    metadata=read_json(backup/'backup.secret.json')
    full=(backup/'full-flash.secret.bin').read_bytes()
    if (metadata.get('device_id')!=device_id or not device_id.startswith('deer-') or
            metadata.get('backup_verified') is not True or len(full)!=FLASH_BYTES or digest(full)!=metadata.get('full_backup_sha256')):
        raise FleetError('原始整机备份未通过来源或摘要校验')
    partition_sector(full[TABLE_OFFSET:TABLE_OFFSET+TABLE_BYTES])
    old=old_image.read_bytes(); validate_image(old)
    if full[0x10000:0x10000+len(old)]!=old or any(byte!=255 for byte in full[OLD_FS_START+OLD_FS_SIZE:]):
        raise FleetError('原应用白名单或原保留尾区不匹配，不能制作通用迁移包')
    manifest=read_package(release,public_key.read_bytes())
    if public_key.read_text(encoding='ascii').strip() not in (ROOT/'firmware/esp32_servo_control/OtaPublicKey.h').read_text(encoding='utf-8'):
        raise FleetError('发布公钥与设备内置公钥不一致')
    application=(release/'application.bin').read_bytes(); validate_image(application)
    source_hashes=verify_sources(ROOT,build)
    if (build/'esp32_servo_control.ino.bin').read_bytes()!=application:
        raise FleetError('发布程序与构建结果不同')
    options=read_json(build/'build.options.json')
    if 'CYBERMIAO_DEVICE_DEER=1' not in options.get('customBuildProperties','') or 'upload.maximum_size=2097152' not in options.get('customBuildProperties',''):
        raise FleetError('构建参数不是小鹿 OTA 版本')
    boot=(build/'esp32_servo_control.ino.bootloader.bin').read_bytes(); validate_image(boot)
    if len(boot)>0x8000:
        raise FleetError('Bootloader 超过允许区域')
    partitions=ota_table((build/'esp32_servo_control.ino.partitions.bin').read_bytes())
    make_private_directory(output)
    source=output/'unpacked-original'; source.mkdir()
    verified=output/'unpacked-verified'; verified.mkdir()
    original=output/'original-filesystem.secret.bin'; original.write_bytes(full[OLD_FS_START:OLD_FS_START+OLD_FS_SIZE])
    fs_command(littlefs,'-u',source,original,OLD_FS_SIZE)
    assets=files_manifest(source)
    fs_command(littlefs,'-c',source,output/'assets.secret.bin',NEW_FS_SIZE)
    fs_command(littlefs,'-u',verified,output/'assets.secret.bin',NEW_FS_SIZE)
    if files_manifest(verified)!=assets:
        raise FleetError('迁移后文件清单不一致，禁止烧录')
    blobs={'bootloader.bin':boot.ljust(0x8000,b'\xff'),'partitions.bin':partitions,
           'otadata.bin':initial_otadata(),'app0.bin':application.ljust(0x200000,b'\xff'),
           'app1.bin':b'\xff'*0x200000}
    for name,data in blobs.items(): (output/name).write_bytes(data)
    blobs['assets.secret.bin']=(output/'assets.secret.bin').read_bytes()
    shutil.copyfile(release/'manifest.json',output/'release-manifest.json')
    record={'schema':1,'layout':LAYOUT,'device_id':device_id,'version':manifest['version'],'sequence':manifest['sequence'],
        'application_sha256':manifest['sha256'],'application_bytes':manifest['size'],
        'previous_application_sha256':digest(old),'previous_application_bytes':len(old),
        'previous_assets_sha256':digest(full[OLD_FS_START:OLD_FS_START+OLD_FS_SIZE]),
        'original_asset_count':len(assets),'original_asset_bytes':sum(item['bytes'] for item in assets.values()),
        'source_files':source_hashes,'public_key_sha256':digest(public_key.read_bytes()),
        'regions':{name:{'offset':REGIONS[name][0],'bytes':len(data),'sha256':digest(data)} for name,data in blobs.items()},
        'flash_written':False}
    write_json(output/'asset-files.secret.json',assets)
    write_json(output/'migration.json',record)
    return {'ok':True,'plan_only':True,'device_id':device_id,'version':manifest['version'],
        'asset_files_verified':len(assets),'asset_bytes':record['original_asset_bytes'],
        'migration_sha256':digest((output/'migration.json').read_bytes()),'hardware_accessed':False,'flash_written':False}


def load_kit(directory, checksum):
    if digest((directory/'migration.json').read_bytes())!=checksum:
        raise FleetError('迁移清单与已审查摘要不符')
    record=read_json(directory/'migration.json')
    if record.get('schema')!=1 or record.get('layout')!=LAYOUT or set(record.get('regions',{}))!=set(REGIONS):
        raise FleetError('迁移包格式不受支持')
    blobs={}
    for name,(offset,size) in REGIONS.items():
        path=directory/name; region=record['regions'][name]
        if path.is_symlink() or path.stat().st_size!=size or region['offset']!=offset or region['bytes']!=size:
            raise FleetError('迁移文件大小或写入区域不符')
        data=path.read_bytes()
        if digest(data)!=region['sha256']:
            raise FleetError('迁移文件摘要不符')
        blobs[name]=data
    ota_table(blobs['partitions.bin'])
    if blobs['otadata.bin']!=initial_otadata() or any(byte!=255 for byte in blobs['app1.bin']):
        raise FleetError('首次启动槽记录或备用槽初始化不符')
    return record,blobs


def migrate(*, directory, checksum, device_id, port=None, executable=None, backup_directory=None,
            approved=False, tool_factory=Esptool, probe=probe_device, sleeper=time.sleep):
    record,blobs=load_kit(directory,checksum)
    if record['device_id']!=device_id or not device_id.startswith('deer-'):
        raise FleetError('本迁移包只适用指定的小鹿')
    plan={'ok':True,'device_id':device_id,'version':record['version'],'plan_only':not approved,
          'hardware_accessed':False,'flash_written':False,'nvs_preserved':True,
          'first_usb_boot_automatic_rollback':False,'subsequent_ota_automatic_rollback':True}
    if not approved: return plan
    if not port or not executable or not backup_directory or backup_directory.exists():
        raise FleetError('执行需要端口、esptool 和新的私密备份目录')
    status,diagnostics=probe(port); validate_device(status,diagnostics,'deer',device_id)
    tool=tool_factory(executable,port); tool.inspect(device_id,'deer')
    full,metadata=create_snapshot(tool,backup_directory,device_id=device_id,profile='deer')
    receipt={**plan,'plan_only':False,'hardware_accessed':True,'phase':'backup_verified',
             'backup_sha256':metadata['full_backup_sha256'],'backup_verified':True,'physical_acceptance_complete':False}
    write_json(backup_directory/'migration-receipt.json',receipt)
    try:
        partition_sector(full[TABLE_OFFSET:TABLE_OFFSET+TABLE_BYTES])
        if (digest(full[0x10000:0x10000+record['previous_application_bytes']])!=record['previous_application_sha256'] or
                digest(full[OLD_FS_START:OLD_FS_START+OLD_FS_SIZE])!=record['previous_assets_sha256'] or
                any(byte!=255 for byte in full[OLD_FS_START+OLD_FS_SIZE:])):
            raise FleetError('当前应用或资源与准备时不同，需重新制作迁移包，未写入')
        frozen=backup_directory/'frozen'; frozen.mkdir()
        nvs=frozen/'nvs.secret.bin'; nvs.write_bytes(full[NVS_START:NVS_START+NVS_SIZE])
        arguments=[]
        for name,(offset,_) in REGIONS.items():
            path=frozen/name; path.write_bytes(blobs[name]); arguments.extend((hex(offset),path))
        receipt.update(phase='write_started',flash_written='possibly')
        write_json(backup_directory/'migration-receipt.json',receipt)
        tool.invoke('write-flash','--flash-mode','keep','--flash-freq','keep','--flash-size','keep',*arguments,timeout=900)
        tool.invoke('verify-flash',*arguments,hex(NVS_START),nvs,timeout=900)
        receipt.update(phase='all_regions_and_nvs_verified',flash_written=True,nvs_verified_before_first_boot=True)
        write_json(backup_directory/'migration-receipt.json',receipt)
        tool.reset()
        after=after_diagnostics=None
        for _ in range(45):
            try: after,after_diagnostics=probe(port); break
            except (OSError,ValueError): sleeper(1)
        if after is None:
            raise FleetError('写入已核验，但设备尚未重新枚举；请观察实机，不自动重写')
        validate_device(after,after_diagnostics,'deer',device_id)
        if (after_diagnostics.get('ota_capable') is not True or after_diagnostics.get('firmware_version')!=record['version'] or
                after_diagnostics.get('firmware_sequence')!=record['sequence']):
            raise FleetError('启动后的版本或 OTA 能力不匹配')
        for name in ('has_wifi','credential_configured','server_url','mode'):
            if status.get(name)!=after.get(name): raise FleetError('升级后配置与升级前不同，请保留备份检查')
        for name in ('animation_sets','animation_frames'):
            if diagnostics[name]!=after_diagnostics[name]: raise FleetError('升级后表情资源计数变化，请保留备份检查')
        receipt.update(ok=True,phase='usb_verified_physical_acceptance_pending')
        write_json(backup_directory/'migration-receipt.json',receipt)
        return receipt
    except BaseException:
        receipt.update(ok=False,automatic_usb_restore=False)
        write_json(backup_directory/'migration-receipt.json',receipt)
        raise


def restore(*, backup, device_id, executable=None, port=None, approved=False, tool_factory=Esptool):
    metadata=read_json(backup/'backup.secret.json')
    full=backup/'full-flash.secret.bin'
    data=full.read_bytes()
    if (metadata.get('device_id')!=device_id or metadata.get('backup_verified') is not True or
            len(data)!=FLASH_BYTES or digest(data)!=metadata.get('full_backup_sha256')):
        raise FleetError('完整恢复备份来源或摘要不符')
    partition_sector(data[TABLE_OFFSET:TABLE_OFFSET+TABLE_BYTES])
    if not approved: return {'ok':True,'plan_only':True,'flash_written':False}
    if not executable or not port or not device_id.startswith('deer-'):
        raise FleetError('恢复需要小鹿编号、端口和 esptool')
    tool=tool_factory(executable,port); tool.inspect(device_id,'deer')
    tool.invoke('write-flash','--flash-mode','keep','--flash-freq','keep','--flash-size','keep','0x0',full,timeout=900)
    tool.invoke('verify-flash','0x0',full,timeout=900)
    tool.reset()
    return {'ok':True,'plan_only':False,'flash_written':True,'full_restore_verified':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    prep=sub.add_parser('prepare')
    for name in ('backup','old-image','release','public-key','build','littlefs','output'):
        prep.add_argument('--'+name,type=Path,required=True)
    prep.add_argument('--device-id',required=True)
    run=sub.add_parser('migrate')
    run.add_argument('--package',type=Path,required=True)
    run.add_argument('--package-sha256',required=True)
    run.add_argument('--device-id',required=True)
    run.add_argument('--port'); run.add_argument('--esptool',type=Path)
    run.add_argument('--backup-dir',type=Path)
    run.add_argument('--approved-partition-migration',action='store_true')
    recovery=sub.add_parser('restore')
    recovery.add_argument('--backup',type=Path,required=True)
    recovery.add_argument('--device-id',required=True)
    recovery.add_argument('--port'); recovery.add_argument('--esptool',type=Path)
    recovery.add_argument('--approved-full-restore',action='store_true')
    args=parser.parse_args()
    try:
        if args.action=='prepare': result=prepare(backup=args.backup,old_image=args.old_image,release=args.release,
            public_key=args.public_key,build=args.build,littlefs=args.littlefs,output=args.output,device_id=args.device_id)
        elif args.action=='migrate': result=migrate(directory=args.package,checksum=args.package_sha256,device_id=args.device_id,
            port=args.port,executable=args.esptool,backup_directory=args.backup_dir,approved=args.approved_partition_migration)
        else: result=restore(backup=args.backup,device_id=args.device_id,port=args.port,executable=args.esptool,approved=args.approved_full_restore)
        print(json.dumps(result,ensure_ascii=False))
    except (FleetError,OSError,ValueError):
        print(json.dumps({'ok':False,'error':'迁移核验或执行未完成。请保留私密回执与备份，不要盲目重写。'},ensure_ascii=False),file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
