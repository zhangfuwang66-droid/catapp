"""首次分区迁移的模拟 Flash 测试：不枚举串口、不调用真实 esptool。"""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock
import zlib

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import ota_migrate as ota
from fleet_firmware import FleetError, digest, PARTITIONS, write_json

DEVICE='deer-C8381AAD4F7C'


def table(entries):
    import hashlib
    data=b''.join(struct.pack('<HBBII16sI',0x50AA,kind,subtype,offset,size,name.encode(),0)
                  for name,kind,subtype,offset,size in entries)
    return (data+b'\xeb\xeb'+b'\xff'*14+hashlib.md5(data).digest()).ljust(4096,b'\xff')


class SimulatedTool:
    def __init__(self,flash):
        self.flash=flash; self.mac='7c:4f:ad:1a:38:c8'; self.calls=[]; self.fail_verify=False
    def inspect(self,device,profile):
        assert device==DEVICE and profile=='deer'
    def invoke(self,operation,*args,**kwargs):
        self.calls.append(operation)
        if operation=='read-flash': Path(args[2]).write_bytes(self.flash[int(args[0],0):int(args[0],0)+int(args[1],0)])
        elif operation=='write-flash':
            for offset,path in zip(args[6::2],args[7::2]):
                data=Path(path).read_bytes(); offset=int(offset,0); self.flash[offset:offset+len(data)]=data
        elif operation=='verify-flash':
            if self.fail_verify and 'write-flash' in self.calls: raise FleetError('模拟写入后校验失败')
            for offset,path in zip(args[::2],args[1::2]):
                data=Path(path).read_bytes(); offset=int(offset,0)
                if self.flash[offset:offset+len(data)]!=data: raise FleetError('模拟校验不一致')
        else: raise AssertionError(operation)
    def reset(self): self.calls.append('reset')


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.kit=self.root/'kit'; self.kit.mkdir()
        self.flash=bytearray(b'\xff'*ota.FLASH_BYTES)
        self.flash[0x8000:0x9000]=table(PARTITIONS)
        self.flash[0x9000:0xE000]=b'N' * ota.NVS_SIZE
        self.flash[0x10000:0x10100]=b'O'*256
        self.old=bytes(self.flash)
        blobs={name:b'\xff'*size for name,(_,size) in ota.REGIONS.items()}
        blobs['partitions.bin']=table(ota.OTA_PARTITIONS)
        blobs['otadata.bin']=ota.initial_otadata()
        record={'schema':1,'layout':ota.LAYOUT,'device_id':DEVICE,'version':'0.3.0-ota.1','sequence':1,
                'previous_application_bytes':256,'previous_application_sha256':digest(b'O'*256),
                'previous_assets_sha256':digest(self.flash[ota.OLD_FS_START:ota.OLD_FS_START+ota.OLD_FS_SIZE]),
                'regions':{name:{'offset':ota.REGIONS[name][0],'bytes':len(data),'sha256':digest(data)} for name,data in blobs.items()}}
        for name,data in blobs.items(): (self.kit/name).write_bytes(data)
        write_json(self.kit/'migration.json',record)
        self.checksum=digest((self.kit/'migration.json').read_bytes())
        self.tool=SimulatedTool(self.flash)
        self.status={'device_id':DEVICE,'device_type':'deer','pending_reboot':False,'has_wifi':True,
                     'credential_configured':True,'server_url':'https://example.test','mode':'accounts'}
        self.diagnostics={'tts_playing':False,'psram_bytes':ota.FLASH_BYTES,'filesystem_ready':True,'animation_sets':4,'animation_frames':160}
        self.probe=Mock(side_effect=[(self.status,self.diagnostics),(self.status,{**self.diagnostics,'ota_capable':True,'firmware_version':'0.3.0-ota.1','firmware_sequence':1})])
    def tearDown(self): self.temp.cleanup()
    def migrate(self,**overrides):
        return ota.migrate(directory=self.kit,checksum=self.checksum,device_id=DEVICE,
            executable=Path('simulated-esptool.exe'),port='SIMULATED',backup_directory=self.root/'backup',
            tool_factory=lambda *_:self.tool,probe=self.probe,**overrides)
    def test_dry_run_never_accesses_hardware(self):
        result=self.migrate()
        self.assertTrue(result['plan_only']); self.assertFalse(result['hardware_accessed'])
        self.probe.assert_not_called(); self.assertEqual(self.tool.calls,[])
    def test_verified_migration_preserves_latest_nvs_and_checks_before_first_boot(self):
        result=self.migrate(approved=True)
        self.assertTrue(result['nvs_verified_before_first_boot'])
        self.assertEqual(self.flash[0x9000:0xE000],self.old[0x9000:0xE000])
        self.assertEqual(self.tool.calls,['read-flash','verify-flash','write-flash','verify-flash','reset'])
        self.assertFalse(result['first_usb_boot_automatic_rollback'])
        self.assertTrue(result['subsequent_ota_automatic_rollback'])
    def test_verify_failure_never_restarts_a_partially_migrated_device(self):
        self.tool.fail_verify=True
        with self.assertRaises(FleetError): self.migrate(approved=True)
        self.assertNotIn('reset',self.tool.calls)
        result=json.loads((self.root/'backup/migration-receipt.json').read_text())
        self.assertFalse(result['ok']); self.assertEqual(result['flash_written'],'possibly')
    def test_changed_application_stops_before_any_write(self):
        self.flash[0x10000]=0
        with self.assertRaises(FleetError): self.migrate(approved=True)
        self.assertNotIn('write-flash',self.tool.calls)
    def test_corrupted_kit_never_probes_device(self):
        (self.kit/'partitions.bin').write_bytes(b'wrong')
        with self.assertRaises(FleetError): self.migrate(approved=True)
        self.probe.assert_not_called()
    def test_full_backup_can_restore_old_layout_only_after_explicit_approval(self):
        self.migrate(approved=True)
        backup=self.root/'backup'; before=len(self.tool.calls)
        result=ota.restore(backup=backup,device_id=DEVICE)
        self.assertTrue(result['plan_only']); self.assertEqual(len(self.tool.calls),before)
        result=ota.restore(backup=backup,device_id=DEVICE,port='SIMULATED',executable=Path('fake'),approved=True,tool_factory=lambda *_:self.tool)
        self.assertTrue(result['full_restore_verified']); self.assertEqual(bytes(self.flash),self.old)
    def test_initial_primary_valid_entry_matches_idf_sequence_crc(self):
        data=ota.initial_otadata()
        sequence,label,state,crc=struct.unpack('<I20sII',data[:32])
        self.assertEqual((sequence,state,crc),(1,2,0x4743989A))
        self.assertEqual(crc,zlib.crc32(struct.pack('<I',sequence),0xFFFFFFFF)&0xFFFFFFFF)
        self.assertEqual(data[32:],b'\xff'*(8192-32))


if __name__=='__main__': unittest.main()
