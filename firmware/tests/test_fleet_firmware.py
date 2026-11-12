"""批量工具的离线/模拟测试；不枚举真实端口，不调用 esptool，不烧录。"""

from contextlib import redirect_stderr
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import fleet_firmware as fleet


DEVICE = "cat-665544332211"
MAC = "11:22:33:44:55:66"


def image(payload):
    header = bytearray(24)
    header[0:2] = b"\xe9\x01"
    struct.pack_into("<H", header, 12, 9)
    header[23] = 1
    result = bytes(header) + struct.pack("<II", 0x3FC88000, len(payload)) + payload
    checksum = 0xEF
    for byte in payload:
        checksum ^= byte
    result += b"\0" * (15 - len(result) % 16) + bytes([checksum])
    return result + hashlib.sha256(result).digest()


def table():
    result = b""
    for name, kind, subtype, offset, size in fleet.PARTITIONS:
        result += struct.pack("<HBBII16sI", 0x50AA, kind, subtype, offset, size, name.encode(), 0)
    result += b"\xeb\xeb" + b"\xff" * 14 + hashlib.md5(result).digest()
    return result.ljust(3072, b"\xff")


def state():
    return ({"device_id": DEVICE, "device_type": "cat", "pending_reboot": False, "has_wifi": True,
             "credential_configured": True, "server_url": "https://cat.example:443", "mode": "accounts"},
            {"tts_playing": False, "psram_bytes": fleet.FLASH_BYTES, "filesystem_ready": True,
             "animation_sets": 4, "animation_frames": 160})


class Fixture:
    def __init__(self, directory):
        self.root = Path(directory)
        self.sketch = self.root / "firmware/esp32_servo_control"
        self.build = self.root / "build"
        self.sketch.mkdir(parents=True)
        (self.build / "sketch").mkdir(parents=True)
        self.sources = {"esp32_servo_control.ino": "void setup() {}\nvoid loop() {}\n",
                        "DeviceProfile.h": "#pragma once\n",
                        "partitions.csv": "nvs,data,nvs,0x9000,0x5000,\notadata,data,ota,0xe000,0x2000,\napp0,app,ota_0,0x10000,0x280000,\nspiffs,data,spiffs,0x290000,0x560000,\n"}
        for name, source in self.sources.items():
            (self.sketch / name).write_text(source, encoding="utf-8")
            if name.endswith(".csv"):
                continue
            copied_name = name + ".cpp.merged" if name.endswith(".ino") else name
            prefix = '#include <Arduino.h>\n#line 1 "fixture.ino"\n' if name.endswith(".ino") else '#line 1 "fixture.h"\n'
            (self.build / "sketch" / copied_name).write_text(prefix + source, encoding="utf-8")
        self.options = {"fqbn": fleet.FQBN, "hardwareFolders": "/packages/esp32/hardware/esp32/3.3.12",
                        "customBuildProperties": "upload.maximum_size=2621440"}
        (self.build / "build.options.json").write_text(json.dumps(self.options), encoding="utf-8")
        self.candidate, self.previous, self.partition = image(b"new application"), image(b"reviewed baseline"), table()
        (self.build / "esp32_servo_control.ino.bin").write_bytes(self.candidate)
        (self.build / "esp32_servo_control.ino.partitions.bin").write_bytes(self.partition)
        self.log = self.root / "compile.log"
        self.log.write_text("Sketch uses 12345 bytes (1%) of program storage space.\n", encoding="utf-8")
        self.baseline = self.root / "previous.bin"
        self.baseline.write_bytes(self.previous)
        self.package = self.root / "package"
        self.backup = self.root / "private-backup"
        self.persona_file = self.root / "personality.secret.txt"
        self.persona_file.write_text("原有的独立性格，模拟测试内容", encoding="utf-8")
        self.persona = self.root / "personality.secret.json"
        self.persona_data = {"device_id": DEVICE, "device_type": "cat",
                            "personality_reference": "backend://device-personality/" + DEVICE + "/v1",
                            "personality_sha256": fleet.digest(self.persona_file.read_bytes()),
                            "verified_original": True, "personality_file": self.persona_file.name}
        self.save_persona()

    def save_persona(self):
        self.persona.write_text(json.dumps(self.persona_data), encoding="utf-8")

    def git(self, root, *args):
        if args == ("rev-parse", "HEAD"):
            return b"a" * 40 + b"\n"
        if args[0] == "show":
            return self.sources[Path(args[1]).name].encode()
        raise AssertionError(args)

    def prepare(self, profile="cat"):
        with patch.object(fleet, "run_git", side_effect=self.git):
            self.prepared = fleet.prepare_package(root=self.root, build=self.build, profile=profile,
                                                  output=self.package, approved_images=[self.baseline], compile_log=self.log)
        return self.prepared

    def full_flash(self, *, application=None):
        result = bytearray(b"\xff" * fleet.FLASH_BYTES)
        result[fleet.TABLE_OFFSET:fleet.TABLE_OFFSET + fleet.TABLE_BYTES] = fleet.partition_sector(self.partition)
        app = self.previous if application is None else application
        result[fleet.APP_OFFSET:fleet.APP_OFFSET + len(app)] = app
        for offset, marker in ((0x9000, b"PRIVATE-NVS-DATA"), (fleet.ASSET_OFFSET, b"EXISTING-EXPRESSIONS")):
            result[offset:offset + len(marker)] = marker
        return bytes(result)

    def upgrade(self, tool, **overrides):
        options = dict(directory=self.package, manifest_sha256=self.prepared["manifest_sha256"],
                       device_id=DEVICE, personality=self.persona, port="SIMULATED", executable=Path("simulated-esptool"),
                       backup_directory=self.backup, apply=True, same_wiring_confirmed=True,
                       tool_factory=lambda *args: tool, probe=Mock(side_effect=lambda port: state()), sleeper=lambda _: None)
        options.update(overrides)
        # 权限逻辑单独核对；模拟硬件流程不调用系统 ACL 命令。
        def private(path):
            path.mkdir(parents=True, exist_ok=False)
        with patch.object(fleet, "make_private_directory", side_effect=private):
            return fleet.upgrade_device(**options)


class FakeTool:
    def __init__(self, full, fail=None):
        self.full, self.fail = full, fail
        self.mac = MAC
        self.calls = []

    def inspect(self, device_id, profile):
        self.calls.append(("inspect", device_id, profile))
        return self.mac

    def invoke(self, operation, *args, **kwargs):
        self.calls.append((operation, *map(str, args)))
        if operation == self.fail:
            raise fleet.FleetError("模拟硬件失败")
        if operation == "read-flash":
            Path(args[-1]).write_bytes(self.full)
        return ""

    def reset(self):
        self.calls.append(("reset",))


class FleetFirmwareTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = Fixture(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_prepare_freezes_source_metadata_without_hardware_or_absolute_build_paths(self):
        result = self.fixture.prepare()
        manifest, blobs = fleet.load_package(self.fixture.package, result["manifest_sha256"])
        self.assertEqual(blobs["application"], self.fixture.candidate)
        self.assertEqual(manifest["source_commit"], "a" * 40)
        self.assertFalse(result["hardware_accessed"])
        self.assertFalse(result["personality_verified"])
        self.assertNotIn(str(self.fixture.root), json.dumps(manifest))
        with self.assertRaises(fleet.FleetError):
            self.fixture.prepare()

    def test_prepare_refuses_uncommitted_or_stale_source(self):
        source = self.fixture.sketch / "DeviceProfile.h"
        source.write_text("#pragma once\n// uncommitted\n", encoding="utf-8")
        with self.assertRaisesRegex(fleet.FleetError, "尚未提交"):
            self.fixture.prepare()
        source.write_text(self.fixture.sources[source.name], encoding="utf-8")
        (self.fixture.build / "sketch/DeviceProfile.h").write_text("// stale", encoding="utf-8")
        with self.assertRaisesRegex(fleet.FleetError, "构建副本"):
            self.fixture.prepare()

    def test_prepare_refuses_failed_compile_log_or_absent_approved_baseline(self):
        self.fixture.log.write_text("Sketch uses 12345 bytes\nError during build", encoding="utf-8")
        with self.assertRaisesRegex(fleet.FleetError, "编译日志"):
            self.fixture.prepare()
        self.assertFalse(self.fixture.package.exists())
        self.fixture.log.write_text("Sketch uses 12345 bytes", encoding="utf-8")
        with patch.object(fleet, "run_git", side_effect=self.fixture.git), self.assertRaisesRegex(fleet.FleetError, "旧应用镜像"):
            fleet.prepare_package(root=self.fixture.root, build=self.fixture.build, profile="cat",
                                  output=self.fixture.package, approved_images=[], compile_log=self.fixture.log)
        self.assertFalse(self.fixture.package.exists())

    def test_build_profile_core_and_override_options_are_strict(self):
        options = self.fixture.options
        fleet.validate_build_options(options, "cat")
        with self.assertRaises(fleet.FleetError):
            fleet.validate_build_options(options, "deer")
        deer = {**options, "customBuildProperties": options["customBuildProperties"] + ",compiler.cpp.extra_flags=-DCYBERMIAO_DEVICE_DEER=1"}
        fleet.validate_build_options(deer, "deer")
        for changed in ({**deer, "fqbn": fleet.FQBN.replace("8M", "16M")},
                        {**deer, "hardwareFolders": "/esp32/3.0.0"},
                        {**deer, "customBuildProperties": deer["customBuildProperties"] + ",compiler.cpp.extra_flags=-DCYBERMIAO_DEVICE_DEER=0"}):
            with self.subTest(options=changed), self.assertRaises(fleet.FleetError):
                fleet.validate_build_options(changed, "deer")

    def test_manifest_and_payload_tampering_are_detected(self):
        result = self.fixture.prepare()
        with self.assertRaises(fleet.FleetError):
            fleet.load_package(self.fixture.package, "f" * 64)
        (self.fixture.package / "application.bin").write_bytes(image(b"another valid image"))
        with self.assertRaisesRegex(fleet.FleetError, "摘要"):
            fleet.load_package(self.fixture.package, result["manifest_sha256"])

    def test_invalid_image_headers_segments_checksum_and_digest_are_refused(self):
        valid = self.fixture.candidate
        variants = [valid[1:], valid[:40], valid + b"\xff", b"x" * 24]
        for offset in (1, 12, 23, 32, len(valid) - 1):
            candidate = bytearray(valid)
            candidate[offset] ^= 0x80
            variants.append(bytes(candidate))
        for candidate in variants:
            with self.subTest(size=len(candidate)), self.assertRaises(fleet.FleetError):
                fleet.validate_image(candidate)
        fleet.validate_image(valid)

    def test_partition_layout_md5_and_extra_data_are_refused(self):
        original = table()
        for offset in (0, 3, 4, 30, 145, 3000):
            modified = bytearray(original)
            modified[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(fleet.FleetError):
                fleet.partition_sector(bytes(modified))
        self.assertEqual(len(fleet.partition_sector(original)), 4096)

    def test_scan_only_enumerates_and_explicit_probe_requires_one_port(self):
        factory = Mock(side_effect=AssertionError("must not open serial"))
        result = fleet.scan_ports(list_ports=lambda: [SimpleNamespace(device="SIMULATED", vid=0x303A, pid=0x1001)],
                                  serial_factory=factory)
        self.assertFalse(result["ports_opened"])
        factory.assert_not_called()
        with self.assertRaises(fleet.FleetError):
            fleet.scan_ports(probe=True, serial_factory=factory)

    def test_default_upgrade_plan_never_opens_device_or_requires_persona(self):
        self.fixture.prepare()
        tool = Mock(side_effect=AssertionError("must not run esptool"))
        result = self.fixture.upgrade(tool, apply=False, personality=None, probe=tool, tool_factory=tool)
        self.assertTrue(result["plan_only"])
        self.assertFalse(result["ready_for_device"])
        self.assertFalse(result["hardware_accessed"])
        self.assertFalse(result["personality_snapshot_verified"])
        tool.assert_not_called()
        self.assertFalse(self.fixture.backup.exists())

    def test_apply_missing_personality_wiring_or_unique_backup_directory_never_opens_device(self):
        self.fixture.prepare()
        for overrides in ({"personality": None}, {"same_wiring_confirmed": False}):
            probe = Mock()
            with self.subTest(overrides=overrides), self.assertRaises(fleet.FleetError):
                self.fixture.upgrade(Mock(), probe=probe, **overrides)
            probe.assert_not_called()
        self.fixture.backup.mkdir()
        probe = Mock()
        with self.assertRaises(fleet.FleetError):
            self.fixture.upgrade(Mock(), probe=probe)
        probe.assert_not_called()

    def test_personality_reference_alone_or_wrong_device_is_not_accepted(self):
        self.fixture.prepare()
        for key, value in (("verified_original", False), ("device_id", "deer-665544332211"),
                           ("personality_sha256", "f" * 64), ("personality_file", "../outside.txt")):
            original = self.fixture.persona_data[key]
            self.fixture.persona_data[key] = value
            self.fixture.save_persona()
            probe = Mock()
            with self.subTest(field=key), self.assertRaises(fleet.FleetError):
                self.fixture.upgrade(Mock(), probe=probe)
            probe.assert_not_called()
            self.fixture.persona_data[key] = original
        self.fixture.save_persona()
        record = fleet.load_personality(self.fixture.persona, expected_id=DEVICE, expected_type="cat")
        self.assertTrue(record["offline_snapshot_verified"])
        self.assertFalse(record["backend_personality_verified"])
        self.assertNotIn("personality_reference", record)

    def test_backend_export_snapshot_is_accepted_without_copying_or_printing_prompt(self):
        prompt = "这是原有独立人格的模拟快照，不是设备真实配置。"
        exported = {"schema": 1, "device_id": DEVICE, "kind": "cat", "name": "模拟猫",
                    "version": "cat-original-v1", "prompt": prompt, "sha256": fleet.digest(prompt.encode()),
                    "created_at": 12345.0}
        self.fixture.persona.write_text(json.dumps(exported, ensure_ascii=False), encoding="utf-8")
        result = fleet.load_personality(self.fixture.persona, expected_id=DEVICE, expected_type="cat")
        self.assertEqual(result["personality_sha256"], exported["sha256"])
        self.assertEqual(result["source_version"], "cat-original-v1")
        self.assertEqual(result["snapshot_format"], "persona-store-v1")
        self.assertTrue(result["offline_snapshot_verified"])
        self.assertFalse(result["backend_personality_verified"])
        self.assertNotIn(prompt, json.dumps(result, ensure_ascii=False))
        for key, value in (("kind", "deer"), ("prompt", "changed"), ("sha256", "0" * 64),
                           ("created_at", float("nan")), ("device_id", "cat-000000000000")):
            bad = {**exported, key: value}
            self.fixture.persona.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
            with self.subTest(key=key), self.assertRaises(fleet.FleetError):
                fleet.load_personality(self.fixture.persona, expected_id=DEVICE, expected_type="cat")

    def test_success_writes_app0_only_and_verifies_protected_regions_before_reset(self):
        self.fixture.prepare()
        tool = FakeTool(self.fixture.full_flash())
        result = self.fixture.upgrade(tool)
        self.assertTrue(result["usb_after_boot_verified"])
        self.assertFalse(result["physical_acceptance_complete"])
        self.assertFalse(result["backend_personality_verified"])
        operations = [call[0] for call in tool.calls]
        self.assertEqual(operations, ["inspect", "read-flash", "verify-flash", "write-flash", "verify-flash", "reset"])
        write = next(call for call in tool.calls if call[0] == "write-flash")
        self.assertEqual(write[-2], "0x10000")
        self.assertNotIn("--force", write)
        self.assertNotIn("--erase-all", write)
        verify = tool.calls[-2]
        self.assertEqual(verify[1::2], ("0x0", "0x10000", "0x290000"))
        self.assertEqual((self.fixture.backup / "full-flash.secret.bin").read_bytes(), tool.full)
        self.assertEqual((self.fixture.backup / "original-app0.secret.bin").read_bytes(), tool.full[fleet.APP_OFFSET:fleet.ASSET_OFFSET])
        receipt_text = (self.fixture.backup / "upgrade-receipt.json").read_text(encoding="utf-8")
        self.assertNotIn("PRIVATE-NVS-DATA", receipt_text)
        self.assertNotIn("原有的独立性格", receipt_text)

    def test_unknown_previous_application_is_backed_up_but_never_written(self):
        self.fixture.prepare()
        tool = FakeTool(self.fixture.full_flash(application=image(b"unknown old application")))
        with self.assertRaisesRegex(fleet.FleetError, "白名单"):
            self.fixture.upgrade(tool)
        self.assertNotIn("write-flash", [call[0] for call in tool.calls])
        self.assertTrue((self.fixture.backup / "backup.secret.json").exists())
        receipt = json.loads((self.fixture.backup / "upgrade-receipt.json").read_text(encoding="utf-8"))
        self.assertFalse(receipt["flash_written"])

    def test_partition_mismatch_incomplete_backup_or_failed_readback_prevents_write(self):
        self.fixture.prepare()
        full = bytearray(self.fixture.full_flash())
        full[fleet.TABLE_OFFSET] ^= 1
        for number, tool in enumerate((FakeTool(bytes(full)), FakeTool(bytes(full)[:-1]),
                                       FakeTool(self.fixture.full_flash(), fail="verify-flash"))):
            with self.subTest(number=number), self.assertRaises(fleet.FleetError):
                self.fixture.upgrade(tool, backup_directory=self.fixture.root / ("backup-" + str(number)))
            self.assertNotIn("write-flash", [call[0] for call in tool.calls])

    def test_backup_works_without_provisioning_protocol_and_never_writes_flash(self):
        tool = FakeTool(self.fixture.full_flash(application=image(b"unreviewed original program")))
        with patch.object(fleet, "make_private_directory", side_effect=lambda path: path.mkdir()), patch.object(fleet, "probe_device", side_effect=AssertionError("not supported by original program")):
            result = fleet.backup_device(port="SIMULATED", executable=Path("simulated"), output=self.fixture.backup,
                                         device_id=DEVICE, profile="cat", tool_factory=lambda *args: tool)
        self.assertTrue(result["backup_verified"])
        self.assertFalse(result["flash_written"])
        self.assertEqual([call[0] for call in tool.calls], ["inspect", "read-flash", "verify-flash", "reset"])

    def test_private_directory_permission_failure_stops_before_flash_read(self):
        tool = FakeTool(self.fixture.full_flash())
        with patch.object(fleet, "make_private_directory", side_effect=fleet.FleetError("permissions")), self.assertRaises(fleet.FleetError):
            fleet.create_snapshot(tool, self.fixture.backup, device_id=DEVICE, profile="cat")
        self.assertEqual(tool.calls, [])

    def test_wrong_identity_missing_resources_psram_or_active_tts_stops_before_esptool(self):
        self.fixture.prepare()
        for field, value in (("device_type", "deer"), ("pending_reboot", True), ("tts_playing", True),
                             ("psram_bytes", 0), ("filesystem_ready", False), ("animation_frames", 0)):
            status, diagnostics = state()
            (status if field in status else diagnostics)[field] = value
            factory = Mock()
            with self.subTest(field=field), self.assertRaises(fleet.FleetError):
                self.fixture.upgrade(Mock(), probe=lambda port: (status, diagnostics), tool_factory=factory)
            factory.assert_not_called()

    def test_write_failure_receipt_does_not_claim_no_flash_change_or_auto_rollback(self):
        self.fixture.prepare()
        tool = FakeTool(self.fixture.full_flash(), fail="write-flash")
        with self.assertRaises(fleet.FleetError):
            self.fixture.upgrade(tool)
        self.assertNotIn(("reset",), tool.calls)
        receipt = json.loads((self.fixture.backup / "upgrade-receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["flash_written"], "possibly")
        self.assertEqual(receipt["phase"], "write_started")
        self.assertFalse(receipt["automatic_rollback"])

    def test_after_boot_configuration_change_is_not_reported_as_success(self):
        self.fixture.prepare()
        before = state()
        after_status, after_diagnostics = state()
        after_status["credential_configured"] = False
        tool = FakeTool(self.fixture.full_flash())
        with self.assertRaisesRegex(fleet.FleetError, "配置状态"):
            self.fixture.upgrade(tool, probe=Mock(side_effect=[before, (after_status, after_diagnostics)]))
        receipt = json.loads((self.fixture.backup / "upgrade-receipt.json").read_text(encoding="utf-8"))
        self.assertTrue(receipt["application_verified"])
        self.assertFalse(receipt["ok"])

    def test_esptool_rejects_mac_chip_capacity_and_encryption_mismatch_without_raw_output(self):
        base = "esptool v5.3.1\nChip type: ESP32-S3 (QFN56)\nMAC: " + MAC + "\nDetected flash size: 8MB\n"
        security = "MAC: " + MAC + "\nSecure Boot: Disabled\nFlash Encryption: Disabled\n"
        for info, secure in ((base.replace("v5.3.1", "v4.9.0"), security),
                             (base.replace("ESP32-S3", "ESP32-C3"), security),
                             (base.replace("8MB", "16MB"), security),
                             (base.replace(MAC, "11:22:33:44:55:77"), security),
                             (base, security.replace("Flash Encryption: Disabled", "Flash Encryption: Enabled")),
                             (base, security.replace(MAC, "11:22:33:44:55:77"))):
            outputs = iter([info, secure])
            runner = Mock(side_effect=lambda *a, **kw: SimpleNamespace(returncode=0, stdout=next(outputs).encode(), stderr=b""))
            with self.subTest(info=info, secure=secure), self.assertRaises(fleet.FleetError):
                fleet.Esptool(Path("simulated"), "SIMULATED", runner=runner).inspect(DEVICE, "cat")
        outputs = iter([base, security])
        runner = Mock(side_effect=lambda *a, **kw: SimpleNamespace(returncode=0, stdout=next(outputs).encode(), stderr=b""))
        self.assertEqual(fleet.Esptool(Path("simulated"), "SIMULATED", runner=runner).inspect(DEVICE, "cat"), MAC)
        failed = Mock(return_value=SimpleNamespace(returncode=1, stdout=b"SECRET-RAW-UART-OUTPUT", stderr=b""))
        with self.assertRaises(fleet.FleetError) as error:
            fleet.Esptool(Path("simulated"), "SIMULATED", runner=failed).invoke("flash-id")
        self.assertNotIn("SECRET", str(error.exception))

    def test_flash_read_and_verify_use_slow_rom_without_stub_or_raw_diff(self):
        output = ("esptool v5.3.1\nMAC: " + MAC + "\n").encode()
        runner = Mock(return_value=SimpleNamespace(returncode=0, stdout=output, stderr=b""))
        tool = fleet.Esptool(Path("simulated"), "SIMULATED", runner=runner)
        for operation in ("read-flash", "verify-flash", "flash-id", "get-security-info", "read-mac"):
            tool.invoke(operation)
            command = runner.call_args.args[0]
            with self.subTest(operation=operation):
                self.assertIn("--no-stub", command)
                self.assertEqual(command[command.index("--baud") + 1], "115200")
                self.assertEqual(command[command.index("--after") + 1], "no-reset")
                self.assertNotIn("--diff", command)
        tool.invoke("write-flash", "0x10000", "simulated.bin")
        command = runner.call_args.args[0]
        self.assertNotIn("--no-stub", command)
        self.assertEqual(command[command.index("--baud") + 1], "460800")
        runner.reset_mock()
        with self.assertRaises(fleet.FleetError):
            tool.invoke("erase-flash")
        runner.assert_not_called()

    def test_cli_apply_needs_explicit_same_wiring_confirmation_and_hides_system_error(self):
        output = io.StringIO()
        with redirect_stderr(output):
            code = fleet.main(["upgrade", "--package", "not-used", "--manifest-sha256", "a" * 64,
                               "--device-id", DEVICE, "--apply"])
        self.assertEqual(code, 1)
        self.assertIn("same-wiring-confirmed", output.getvalue())
        output = io.StringIO()
        with patch.object(fleet, "scan_ports", side_effect=OSError("SECRET ERROR")), redirect_stderr(output):
            code = fleet.main(["scan"])
        self.assertEqual(code, 1)
        self.assertNotIn("SECRET", output.getvalue())


if __name__ == "__main__":
    unittest.main()
