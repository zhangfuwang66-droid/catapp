"""同款 ESP32-S3 设备的离线固件包、端口枚举、私密备份和受限主程序升级。"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import time

from provision_device import ProvisioningClient, ProvisioningError

FLASH_BYTES = 0x800000
TABLE_OFFSET = 0x8000
TABLE_BYTES = 0x1000
APP_OFFSET = 0x10000
APP_BYTES = 0x280000
ASSET_OFFSET = APP_OFFSET + APP_BYTES
CORE_VERSION = "3.3.12"
FQBN = "esp32:esp32:esp32s3:FlashSize=8M,PSRAM=opi,PartitionScheme=custom,CDCOnBoot=cdc,USBMode=hwcdc,FlashMode=qio"
PARTITIONS = (
    ("nvs", 1, 2, 0x9000, 0x5000),
    ("otadata", 1, 0, 0xE000, 0x2000),
    ("app0", 0, 0x10, APP_OFFSET, APP_BYTES),
    ("spiffs", 1, 0x82, ASSET_OFFSET, 0x560000),
)
HEX_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
DEVICE_ID = re.compile(r"(cat|deer)-[0-9A-F]{12}\Z")
HARDWARE = {"chip": "ESP32-S3", "flash_bytes": FLASH_BYTES, "psram_bytes": FLASH_BYTES,
            "board_family": "cybermiao-s3-8m-opi-same-wiring"}


class FleetError(ValueError):
    """只包含可公开显示的预定义错误。"""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path, maximum=256 * 1024):
    if path.stat().st_size > maximum:
        raise FleetError("JSON 文件超过上限")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeError, ValueError):
        raise FleetError("JSON 文件格式无效") from None


def write_json(path: Path, data):
    # 回执更新用同目录原子替换，不能把半份文件误认为完整备份。
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def validate_image(data: bytes):
    if not 24 <= len(data) <= APP_BYTES or data[0] != 0xE9 or struct.unpack_from("<H", data, 12)[0] != 9:
        raise FleetError("程序不是允许大小的 ESP32-S3 应用镜像；不能使用 merged.bin")
    if not 1 <= data[1] <= 16 or data[23] not in {0, 1}:
        raise FleetError("ESP 应用段数或摘要标志无效")
    position, checksum = 24, 0xEF
    for _ in range(data[1]):
        if position + 8 > len(data):
            raise FleetError("应用段头被截断")
        _, size = struct.unpack_from("<II", data, position)
        position += 8
        if size > APP_BYTES or position + size > len(data):
            raise FleetError("应用段被截断或超出分区")
        for byte in data[position:position + size]:
            checksum ^= byte
        position += size
    checksum_position = ((position + 16) // 16) * 16 - 1
    end = checksum_position + 1
    expected_length = end + (32 if data[23] else 0)
    if (expected_length != len(data) or checksum_position >= len(data)
            or any(data[position:checksum_position]) or data[checksum_position] != checksum):
        raise FleetError("应用校验和、填充或长度无效；不接受全分区备份作为候选应用")
    if data[23] and hashlib.sha256(data[:end]).digest() != data[end:]:
        raise FleetError("ESP 应用内置 SHA-256 不符")


def partition_sector(data: bytes) -> bytes:
    """只接受仓库已知分区和有效 MD5 结尾；拒绝未知分区与加密标志。"""
    if not 160 <= len(data) <= TABLE_BYTES or len(data) % 32:
        raise FleetError("分区表长度无效")
    entries = []
    for offset in range(0, len(data), 32):
        entry = data[offset:offset + 32]
        if entry[:2] == b"\xeb\xeb":
            if (entry[2:16] != b"\xff" * 14
                    or entry[16:] != hashlib.md5(data[:offset]).digest()
                    or any(byte != 255 for byte in data[offset + 32:])):
                raise FleetError("分区表 MD5 或尾部无效")
            break
        magic, kind, subtype, start, size, label, flags = struct.unpack("<HBBII16sI", entry)
        if magic != 0x50AA or flags:
            raise FleetError("分区表记录或安全标志不受支持")
        try:
            name = label.rstrip(b"\0").decode("ascii")
        except UnicodeError:
            raise FleetError("分区名称无效") from None
        entries.append((name, kind, subtype, start, size))
    else:
        raise FleetError("分区表缺少 MD5 记录")
    if tuple(entries) != PARTITIONS:
        raise FleetError("分区布局不兼容；必须单独审查，不能自动升级")
    return data.ljust(TABLE_BYTES, b"\xff")


def validate_partition_source(path: Path):
    rows = []
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            fields = [field.strip() for field in line.split(",")]
            if len(fields) < 5 or any(fields[5:]):
                raise ValueError
            name, kind, subtype, start, size = fields[:5]
            kind_value = {"data": 1, "app": 0}[kind]
            subtype_value = {"nvs": 2, "ota": 0, "ota_0": 0x10, "spiffs": 0x82}[subtype]
            rows.append((name, kind_value, subtype_value, int(start, 0), int(size, 0)))
    except (KeyError, ValueError):
        raise FleetError("源码分区 CSV 格式或标志不受支持") from None
    if tuple(rows) != PARTITIONS:
        raise FleetError("源码分区 CSV 与已审核的烧录范围不同")


def run_git(root: Path, *arguments) -> bytes:
    result = subprocess.run(["git", *arguments], cwd=root, capture_output=True, timeout=30)
    if result.returncode:
        raise FleetError("Git 来源核对失败；请在项目仓库中运行")
    return result.stdout


def verify_sources(root: Path, build: Path):
    sketch = root / "firmware/esp32_servo_control"
    paths = sorted(path for path in sketch.rglob("*") if path.suffix in {".ino", ".cpp", ".h", ".csv"})
    if not paths:
        raise FleetError("没有找到固件源码")
    hashes = {}
    for path in paths:
        relative = path.relative_to(root).as_posix()
        original = path.read_bytes()
        committed = run_git(root, "show", "HEAD:" + relative)
        # Windows 工作树的 CRLF 与仓库 LF 等价；其它字符必须完全一致。
        normalize = lambda value: value.decode("utf-8-sig").replace("\r\n", "\n").rstrip()
        if normalize(original) != normalize(committed):
            raise FleetError("固件源码尚未提交，请先保存对应 Git 版本")
        hashes[relative] = digest(committed)
        if path.suffix == ".csv":
            continue
        name = path.name + ".cpp.merged" if path.suffix == ".ino" else path.name
        copied_path = build / "sketch" / path.relative_to(sketch).parent / name
        if not copied_path.is_file():
            raise FleetError("构建目录缺少源码副本，请重新完整编译")
        copied = normalize(copied_path.read_bytes())
        if path.suffix == ".ino":
            lines = copied.split("\n", 2)
            if len(lines) < 3 or not lines[0].startswith("#include") or not lines[1].startswith("#line"):
                raise FleetError("Arduino 草图构建副本格式不受支持")
            copied = lines[2]
        elif copied.startswith("#line 1 "):
            copied = copied.split("\n", 1)[1]
        if normalize(original) != copied:
            raise FleetError("构建副本与已提交源码不同，请重新编译")
    return hashes


def validate_build_options(options, profile):
    if not isinstance(options, dict) or options.get("fqbn") != FQBN:
        raise FleetError("编译板型或 8MB OPI PSRAM/USB/分区选项不匹配")
    folders = str(options.get("hardwareFolders", "")).replace("\\", "/").split(",")
    if not folders or any(not path.rstrip("/").endswith("/esp32/" + CORE_VERSION) for path in folders):
        raise FleetError("ESP32 核心版本不是已审核的版本")
    properties = options.get("customBuildProperties", "")
    if not isinstance(properties, str):
        raise FleetError("编译宏记录无效")
    properties = properties.split(",")
    expected = {"upload.maximum_size=2621440"}
    if profile == "deer":
        expected.add("compiler.cpp.extra_flags=-DCYBERMIAO_DEVICE_DEER=1")
    # 不允许其它未审核的覆盖项，避免存在两个互相覆盖的 profile 宏。
    if len(properties) != len(expected) or set(properties) != expected:
        raise FleetError("编译宏与设备类型不匹配或包含未审核的覆盖项")


def prepare_package(*, root: Path, build: Path, profile: str, output: Path,
                    approved_images: list[Path], compile_log: Path):
    if profile not in {"cat", "deer"}:
        raise FleetError("设备类型无效")
    if output.exists():
        raise FleetError("输出目录已存在；固件包不能原地覆盖")
    options = read_json(build / "build.options.json")
    validate_build_options(options, profile)
    validate_partition_source(root / "firmware/esp32_servo_control/partitions.csv")
    hashes = verify_sources(root, build)
    image = (build / "esp32_servo_control.ino.bin").read_bytes()
    table = (build / "esp32_servo_control.ino.partitions.bin").read_bytes()
    validate_image(image)
    partition_sector(table)
    log = compile_log.read_bytes()
    log_text = log.decode("utf-8", errors="replace")
    if not re.search(r"Sketch uses\s+\d+\s+bytes", log_text) or re.search(r"Error during build|fatal error:", log_text, re.I):
        raise FleetError("编译日志未包含成功的体积报告，或包含构建错误")
    approved = []
    for path in approved_images:
        previous = path.read_bytes()
        validate_image(previous)
        record = {"sha256": digest(previous), "bytes": len(previous)}
        if record not in approved:
            approved.append(record)
    if not approved:
        raise FleetError("请显式提供已人工核准的旧应用镜像；不能仅凭芯片型号升级")
    manifest = {
        "version": 1, "device_type": profile, "hardware": HARDWARE,
        "application": {"file": "application.bin", "sha256": digest(image), "bytes": len(image),
                        "offset": APP_OFFSET, "partition_bytes": APP_BYTES},
        "partitions": {"file": "partitions.bin", "sha256": digest(table), "bytes": len(table)},
        "approved_previous_applications": approved,
        "source_commit": run_git(root, "rev-parse", "HEAD").decode().strip(),
        "source_sha256": hashes, "build_source_verified": True,
        "fqbn": FQBN, "core_version": CORE_VERSION, "compile_log_sha256": digest(log),
        "protected_regions": [[0, APP_OFFSET], [ASSET_OFFSET, FLASH_BYTES]],
        "personality_policy": "external-profile-required",
        "hardware_accessed": False, "physical_acceptance_complete": False,
        "prepared_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    output.mkdir(parents=True, exist_ok=False)
    (output / "application.bin").write_bytes(image)
    (output / "partitions.bin").write_bytes(table)
    write_json(output / "manifest.json", manifest)
    return {"ok": True, "package": str(output), "manifest_sha256": digest((output / "manifest.json").read_bytes()),
            "device_type": profile, "source_commit": manifest["source_commit"],
            "hardware_accessed": False, "personality_verified": False, "ready_for_device": False}


def load_package(directory: Path, manifest_sha256: str):
    raw = (directory / "manifest.json").read_bytes()
    if not HEX_DIGEST.fullmatch(manifest_sha256) or digest(raw) != manifest_sha256:
        raise FleetError("固件包清单摘要不符；请使用审核时单独记录的 SHA-256")
    manifest = read_json(directory / "manifest.json")
    if (not isinstance(manifest, dict) or manifest.get("version") != 1
            or manifest.get("device_type") not in {"cat", "deer"} or manifest.get("hardware") != HARDWARE
            or manifest.get("fqbn") != FQBN or manifest.get("core_version") != CORE_VERSION
            or manifest.get("personality_policy") != "external-profile-required"
            or manifest.get("protected_regions") != [[0, APP_OFFSET], [ASSET_OFFSET, FLASH_BYTES]]):
        raise FleetError("固件包格式、硬件或保留策略不兼容")
    blobs = {}
    for field, filename in (("application", "application.bin"), ("partitions", "partitions.bin")):
        metadata = manifest.get(field)
        if not isinstance(metadata, dict) or metadata.get("file") != filename:
            raise FleetError("固件包文件名不受支持")
        blob = (directory / filename).read_bytes()
        if digest(blob) != metadata.get("sha256") or len(blob) != metadata.get("bytes"):
            raise FleetError("固件包文件摘要或长度不符")
        blobs[field] = blob
    if manifest["application"].get("offset") != APP_OFFSET or manifest["application"].get("partition_bytes") != APP_BYTES:
        raise FleetError("应用写入区域不兼容")
    validate_image(blobs["application"])
    partition_sector(blobs["partitions"])
    approved = manifest.get("approved_previous_applications")
    if not isinstance(approved, list) or not approved:
        raise FleetError("固件包没有已核准旧程序白名单")
    for item in approved:
        if (not isinstance(item, dict) or set(item) != {"sha256", "bytes"}
                or not isinstance(item["sha256"], str) or not HEX_DIGEST.fullmatch(item["sha256"])
                or type(item["bytes"]) is not int or not 24 <= item["bytes"] <= APP_BYTES):
            raise FleetError("旧程序白名单无效")
    return manifest, blobs


def load_personality(path: Path, *, expected_id: str, expected_type: str):
    record = read_json(path, 64 * 1024)
    if isinstance(record, dict) and record.get("schema") == 1:
        # PersonaStore.export_private 的完整私密锁档快照可直接使用，不另抄一份 prompt。
        # 这里只做离线一致性验证；格式正确不等于目标云服务此刻加载了它。
        required = {"schema", "device_id", "kind", "name", "version", "prompt", "sha256", "created_at"}
        prompt, version, created = record.get("prompt"), record.get("version"), record.get("created_at")
        if (set(record) != required or type(record["schema"]) is not int
                or record.get("device_id") != expected_id or record.get("kind") != expected_type
                or not isinstance(prompt, str) or not 1 <= len(prompt) <= 20000
                or not isinstance(record.get("name"), str) or not 1 <= len(record["name"]) <= 80
                or not isinstance(version, str) or not 1 <= len(version) <= 128
                or record.get("sha256") != digest(prompt.encode("utf-8"))
                or type(created) not in {int, float} or not math.isfinite(created) or created < 0):
            raise FleetError("PersonaStore 原人格锁档快照无效或与设备不匹配")
        reference = "backend://device-personality/" + expected_id + "/v1"
        return {"device_id": expected_id, "device_type": expected_type,
                "personality_sha256": record["sha256"], "reference_sha256": digest(reference.encode()),
                "source_version": version, "snapshot_format": "persona-store-v1",
                "offline_snapshot_verified": True, "backend_personality_verified": False}
    if not isinstance(record, dict) or record.get("device_id") != expected_id or record.get("device_type") != expected_type:
        raise FleetError("人格档案与设备不匹配")
    reference = record.get("personality_reference")
    fingerprint = record.get("personality_sha256")
    if (not isinstance(reference, str) or not 1 <= len(reference) <= 300 or "\n" in reference
            or not isinstance(fingerprint, str) or not HEX_DIGEST.fullmatch(fingerprint)
            or record.get("verified_original") is not True):
        raise FleetError("原人格尚未人工核对；仅填写 backend 引用不代表已验证")
    filename = record.get("personality_file")
    if not isinstance(filename, str) or Path(filename).name != filename or filename in {"", ".", ".."}:
        raise FleetError("请提供与档案同目录的原人格快照文件")
    payload = path.parent / filename
    if not 1 <= payload.stat().st_size <= 1024 * 1024 or digest(payload.read_bytes()) != fingerprint:
        raise FleetError("原人格快照缺失、过大或摘要不符")
    # 不返回原文或引用中的潜在私密文本；引用从不当成在线验证结果。
    return {"device_id": expected_id, "device_type": expected_type, "personality_sha256": fingerprint,
            "reference_sha256": digest(reference.encode()), "offline_snapshot_verified": True,
            "backend_personality_verified": False}


def open_stream(port):
    try:
        import serial
    except ImportError:
        raise FleetError("请先安装 firmware/tools/requirements.txt") from None
    stream = serial.Serial(port=None, baudrate=115200, timeout=.2, write_timeout=3)
    stream.dtr = False
    stream.rts = False
    stream.port = port
    stream.open()
    return stream


def probe_device(port, *, serial_factory=None):
    stream = (serial_factory or open_stream)(port)
    try:
        client = ProvisioningClient(stream)
        status = client.exchange("status")
        diagnostic_response = client.exchange("diagnostics")
        if diagnostic_response.get("device_id") != status.get("device_id"):
            raise FleetError("两次 USB 查询的设备身份不一致")
        return status, diagnostic_response["diagnostics"]
    finally:
        stream.close()


def scan_ports(*, probe=False, port=None, list_ports=None, serial_factory=None):
    if probe:
        if not port:
            raise FleetError("主动查询必须指定单个 --port，不能批量打开未知串口")
        status, diagnostics = probe_device(port, serial_factory=serial_factory)
        return {"ok": True, "port": port, "device_id": status.get("device_id"),
                "device_type": status.get("device_type"), "has_wifi": status.get("has_wifi"),
                "credential_configured": status.get("credential_configured"), "diagnostics": diagnostics,
                "flash_written": False}
    if port:
        raise FleetError("指定 --port 时需明确选择 --probe")
    if list_ports is None:
        try:
            from serial.tools.list_ports import comports
        except ImportError:
            raise FleetError("请先安装 firmware/tools/requirements.txt") from None
        list_ports = comports
    # 只枚举公开设备名与 VID/PID；不输出 USB 序列号或任意描述文本。
    return {"ok": True, "ports": [{"port": item.device, "vid": item.vid, "pid": item.pid}
                                    for item in list_ports()], "ports_opened": False, "flash_written": False}


def validate_device(status, diagnostics, profile, expected_id):
    if (not DEVICE_ID.fullmatch(expected_id) or not expected_id.startswith(profile + "-")
            or status.get("device_id") != expected_id or status.get("device_type") != profile):
        raise FleetError("USB 设备编号或类型不匹配；禁止跨猫/鹿身份升级")
    if status.get("pending_reboot") is not False:
        raise FleetError("设备状态不稳定或等待重启，请完成当前配置后重试")
    if diagnostics.get("tts_playing") is not False:
        raise FleetError("请先在控制台暂停语音，确认设备没有播放后再升级")
    if (diagnostics.get("psram_bytes") != FLASH_BYTES or diagnostics.get("filesystem_ready") is not True
            or type(diagnostics.get("animation_sets")) is not int or diagnostics["animation_sets"] <= 0
            or type(diagnostics.get("animation_frames")) is not int or diagnostics["animation_frames"] <= 0):
        raise FleetError("PSRAM、文件系统或表情资源未就绪，需先单独检查")


def make_private_directory(path: Path):
    path.mkdir(parents=True, exist_ok=False, mode=0o700)
    if os.name == "nt":
        result = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, timeout=10)
        try:
            row = next(csv.reader(io.StringIO(result.stdout.decode("utf-8", errors="replace"))))
            sid = row[-1]
        except (StopIteration, IndexError):
            raise FleetError("无法确认私密备份目录的 Windows 访问权限") from None
        if result.returncode or not re.fullmatch(r"S-1-[0-9-]+", sid):
            raise FleetError("无法确认私密备份目录的 Windows 访问权限")
        acl = subprocess.run(["icacls", str(path.resolve()), "/inheritance:r", "/grant:r",
                              "*" + sid + ":(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"],
                             capture_output=True, timeout=15)
        if acl.returncode:
            raise FleetError("私密备份目录权限设置失败，未开始读取 Flash")
    else:
        path.chmod(0o700)


class Esptool:
    def __init__(self, executable: Path, port: str, *, runner=None):
        self.executable = executable
        self.port = port
        self.runner = runner or subprocess.run
        self.mac = None

    def invoke(self, operation, *arguments, after="no-reset", timeout=180):
        if operation not in {"flash-id", "get-security-info", "read-flash", "verify-flash", "read-mac", "write-flash"}:
            raise FleetError("禁止未审核的 esptool 操作")
        # 已知此 USB-Serial/JTAG 板的 stub 批量读取会中断。所有只读操作走
        # 115200 ROM；verify-flash 在 esptool 5.3.1 使用 ROM SPI_FLASH_MD5，
        # 不加 --diff（失败时不读取/打印原始 Flash 字节）。只有应用写入使用 stub。
        rom = operation != "write-flash"
        command = [str(self.executable), "--chip", "esp32s3", "--port", self.port, "--baud", "115200" if rom else "460800",
                   "--before", "default-reset", "--after", after, "--connect-attempts", "2",
                   *(["--no-stub"] if rom else []), operation, *map(str, arguments)]
        result = self.runner(command, capture_output=True, timeout=timeout)
        if result.returncode:
            # 不回显原始串口输出；不使用 --force、全盘擦除或加密覆盖选项。
            raise FleetError("esptool 操作失败：" + operation + "；保留备份，勿盲目重试写入")
        output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        macs = set(re.findall(r"MAC:\s*([0-9a-f]{2}(?::[0-9a-f]{2}){5})", output.lower(), re.I))
        if len(macs) != 1 or (self.mac is not None and self.mac not in macs):
            raise FleetError("esptool 未确认同一芯片 MAC，立即停止")
        self.mac = next(iter(macs))
        return output

    def inspect(self, expected_id, profile):
        info = self.invoke("flash-id")
        if not re.search(r"\besptool v5\.3\.1\b", info, re.I):
            raise FleetError("esptool 版本不是已审核的 5.3.1")
        if not re.search(r"Chip type:\s*ESP32-S3\b", info, re.I) or not re.search(r"Detected flash size:\s*8MB\b", info, re.I):
            raise FleetError("实际芯片或 Flash 容量不是 ESP32-S3 / 8MB")
        derived = profile + "-" + "".join(reversed(self.mac.split(":"))).upper()
        if derived != expected_id:
            raise FleetError("芯片 MAC 与 USB 设备编号不符，未写入")
        security = self.invoke("get-security-info")
        if (not re.search(r"Secure Boot:\s*Disabled\b", security, re.I)
                or not re.search(r"Flash Encryption:\s*Disabled\b", security, re.I)
                or re.search(r"Secure Download Mode:\s*Enabled\b", security, re.I)):
            raise FleetError("安全启动、Flash 加密或下载安全状态不受支持，禁止通用升级")
        return self.mac

    def reset(self):
        self.invoke("read-mac", after="hard-reset")


def create_snapshot(tool, directory: Path, *, device_id: str, profile: str):
    make_private_directory(directory)
    full_path = directory / "full-flash.secret.bin"
    tool.invoke("read-flash", "0x0", hex(FLASH_BYTES), full_path, timeout=900)
    full = full_path.read_bytes()
    if len(full) != FLASH_BYTES:
        raise FleetError("完整 Flash 备份长度不符；禁止写入")
    # 不仅计算本地摘要，还与当前芯片回读比较；比较前不启动应用。
    tool.invoke("verify-flash", "0x0", full_path, timeout=900)
    files = {"original-app0.secret.bin": full[APP_OFFSET:ASSET_OFFSET],
             "protected-head.secret.bin": full[:APP_OFFSET],
             "protected-assets-tail.secret.bin": full[ASSET_OFFSET:]}
    for name, data in files.items():
        (directory / name).write_bytes(data)
    metadata = {"version": 1, "device_id": device_id, "device_type": profile, "mac": tool.mac,
                "flash_bytes": FLASH_BYTES, "full_backup_sha256": digest(full), "backup_verified": True,
                "original_app0_sha256": digest(files["original-app0.secret.bin"]),
                "restore_offset": APP_OFFSET, "restore_bytes": APP_BYTES,
                "created_at_utc": datetime.now(timezone.utc).isoformat(), "flash_written": False}
    write_json(directory / "backup.secret.json", metadata)
    return full, metadata


def backup_device(*, port, executable: Path, output: Path, device_id, profile, tool_factory=Esptool):
    if not DEVICE_ID.fullmatch(device_id) or not device_id.startswith(profile + "-"):
        raise FleetError("备份编号与设备类型不匹配")
    if output.exists():
        raise FleetError("备份目录已存在；请为每次备份使用独立目录")
    tool = tool_factory(executable, port)
    tool.inspect(device_id, profile)
    _, metadata = create_snapshot(tool, output, device_id=device_id, profile=profile)
    tool.reset()
    return {"ok": True, "device_id": device_id, "backup_directory": str(output),
            "backup_sha256": metadata["full_backup_sha256"], "backup_verified": True,
            "flash_written": False, "device_restarted": True}


def approved_application(full, manifest):
    candidates = [manifest["application"], *manifest["approved_previous_applications"]]
    for candidate in candidates:
        size = candidate["bytes"]
        if digest(full[APP_OFFSET:APP_OFFSET + size]) == candidate["sha256"]:
            return candidate["sha256"]
    raise FleetError("已安装主程序不在审核白名单；备份保留，需先审查来源，未写入")


def upgrade_device(*, directory: Path, manifest_sha256: str, device_id: str, personality: Path | None,
                   port=None, executable: Path | None = None, backup_directory: Path | None = None,
                   apply=False, same_wiring_confirmed=False, tool_factory=Esptool, probe=probe_device, sleeper=time.sleep):
    manifest, blobs = load_package(directory, manifest_sha256)
    profile = manifest["device_type"]
    if not DEVICE_ID.fullmatch(device_id) or not device_id.startswith(profile + "-"):
        raise FleetError("设备编号与固件类型不匹配")
    persona = load_personality(personality, expected_id=device_id, expected_type=profile) if personality else None
    plan = {"ok": True, "plan_only": not apply, "device_id": device_id, "device_type": profile,
            "application_sha256": manifest["application"]["sha256"], "write_offset": APP_OFFSET,
            "personality_snapshot_verified": persona is not None, "backend_personality_verified": False,
            "same_wiring_confirmation_required": True, "hardware_accessed": False,
            "flash_written": False, "ready_for_device": False,
            "steps": ["核对同款接线、设备身份与离线人格档案", "核验芯片、PSRAM、分区和旧程序白名单",
                      "私密保存并回读校验完整 8MB Flash", "仅写 app0 并在首次启动前校验保护区域",
                      "重启后核对身份、网络配置和资源计数，另行做云端人格与实物验收"]}
    if not apply:
        return plan
    if not same_wiring_confirmed:
        raise FleetError("请先确认 PCB 和外设接线完全相同，再指定 --same-wiring-confirmed")
    if not all((port, executable, backup_directory, persona)):
        raise FleetError("执行升级需 --port、--esptool、--backup-dir 及已核对的 --personality 档案")
    if backup_directory.exists():
        raise FleetError("备份目录已存在；请为每台设备的每次升级使用独立目录")
    status, diagnostics = probe(port)
    validate_device(status, diagnostics, profile, device_id)
    tool = tool_factory(executable, port)
    tool.inspect(device_id, profile)
    full, metadata = create_snapshot(tool, backup_directory, device_id=device_id, profile=profile)
    receipt_path = backup_directory / "upgrade-receipt.json"
    receipt = {**plan, "plan_only": False, "hardware_accessed": True, "phase": "backup_verified",
               "backup_verified": True, "backup_sha256": metadata["full_backup_sha256"],
               "manifest_sha256": manifest_sha256, "personality": persona,
               "physical_acceptance_complete": False, "cloud_modified": False}
    write_json(receipt_path, receipt)
    try:
        if full[TABLE_OFFSET:TABLE_OFFSET + TABLE_BYTES] != partition_sector(blobs["partitions"]):
            raise FleetError("设备现有分区与候选包不一致；未写入")
        previous = approved_application(full, manifest)
        receipt["previous_application_sha256"] = previous
        receipt["phase"] = "write_started"
        # 在启动写入前记为“可能已写”，避免断电回执仍显示没有写入。
        receipt["flash_written"] = "possibly"
        write_json(receipt_path, receipt)
        # 包文件也先拷贝进私密本次快照，避免审查后源目录被并发覆盖。
        frozen_image = backup_directory / "candidate-application.bin"
        frozen_image.write_bytes(blobs["application"])
        tool.invoke("write-flash", "--flash-mode", "keep", "--flash-freq", "keep", "--flash-size", "keep",
                    hex(APP_OFFSET), frozen_image, timeout=900)
        tool.invoke("verify-flash", "0x0", backup_directory / "protected-head.secret.bin",
                    hex(APP_OFFSET), frozen_image, hex(ASSET_OFFSET), backup_directory / "protected-assets-tail.secret.bin",
                    timeout=900)
        receipt.update(phase="protected_regions_verified", flash_written=True, application_verified=True,
                       protected_regions_verified_before_first_boot=True)
        write_json(receipt_path, receipt)
        tool.reset()
        # USB 原生 CDC 复位可能重新枚举；此处只重试原指定端口，不猜测另一个设备。
        after_status = after_diagnostics = None
        for _ in range(30):
            try:
                after_status, after_diagnostics = probe(port)
                break
            except (OSError, ProvisioningError, FleetError):
                sleeper(1)
        if after_status is None:
            raise FleetError("程序已校验，但重启后原端口未响应；请检查重新枚举和实物，不自动重写")
        validate_device(after_status, after_diagnostics, profile, device_id)
        for field in ("has_wifi", "credential_configured", "server_url", "mode"):
            if status.get(field) != after_status.get(field):
                raise FleetError("重启后配置状态与升级前不同，请保留备份并人工检查")
        for field in ("animation_sets", "animation_frames"):
            if diagnostics[field] != after_diagnostics[field]:
                raise FleetError("重启后表情资源计数变化，请人工验收，不能视为完成")
        receipt.update(phase="usb_verified_physical_acceptance_pending", usb_after_boot_verified=True, ok=True)
        write_json(receipt_path, receipt)
    except Exception:
        receipt.update(ok=False, automatic_rollback=False)
        write_json(receipt_path, receipt)
        raise
    # 只返回公开结论；完整 Flash、原始串口输出及人格正文从不输出。
    return {key: receipt[key] for key in ("ok", "device_id", "phase", "flash_written", "backup_verified",
                                         "application_verified", "protected_regions_verified_before_first_boot",
                                         "usb_after_boot_verified", "physical_acceptance_complete",
                                         "backend_personality_verified", "cloud_modified")}


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="离线冻结已提交源码对应的构建产物，不访问设备")
    prepare.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[2])
    prepare.add_argument("--build-dir", required=True, type=Path)
    prepare.add_argument("--profile", required=True, choices=("cat", "deer"))
    prepare.add_argument("--output", required=True, type=Path)
    prepare.add_argument("--compile-log", required=True, type=Path)
    prepare.add_argument("--approved-previous-image", required=True, action="append", type=Path)
    scan = commands.add_parser("scan", help="默认仅枚举端口；--probe --port 才查询一个设备")
    scan.add_argument("--probe", action="store_true")
    scan.add_argument("--port")
    backup = commands.add_parser("backup", help="核对芯片后只读备份；会让设备暂停并重启，不写 Flash")
    backup.add_argument("--port", required=True)
    backup.add_argument("--esptool", required=True, type=Path)
    backup.add_argument("--output", required=True, type=Path)
    backup.add_argument("--device-id", required=True)
    backup.add_argument("--profile", required=True, choices=("cat", "deer"))
    upgrade = commands.add_parser("upgrade", help="默认只输出计划；--apply 才访问并升级设备")
    upgrade.add_argument("--package", required=True, type=Path)
    upgrade.add_argument("--manifest-sha256", required=True)
    upgrade.add_argument("--device-id", required=True)
    upgrade.add_argument("--personality", type=Path)
    upgrade.add_argument("--port")
    upgrade.add_argument("--esptool", type=Path)
    upgrade.add_argument("--backup-dir", type=Path)
    upgrade.add_argument("--same-wiring-confirmed", action="store_true")
    upgrade.add_argument("--apply", action="store_true")
    options = parser.parse_args(arguments)
    try:
        if options.command == "prepare":
            result = prepare_package(root=options.source_root, build=options.build_dir, profile=options.profile,
                                     output=options.output, approved_images=options.approved_previous_image,
                                     compile_log=options.compile_log)
        elif options.command == "scan":
            result = scan_ports(probe=options.probe, port=options.port)
        elif options.command == "backup":
            result = backup_device(port=options.port, executable=options.esptool, output=options.output,
                                   device_id=options.device_id, profile=options.profile)
        else:
            if options.apply and not options.same_wiring_confirmed:
                raise FleetError("请先确认 PCB 和外设接线完全相同，再指定 --same-wiring-confirmed")
            result = upgrade_device(directory=options.package, manifest_sha256=options.manifest_sha256,
                                    device_id=options.device_id, personality=options.personality, port=options.port,
                                    executable=options.esptool, backup_directory=options.backup_dir, apply=options.apply,
                                    same_wiring_confirmed=options.same_wiring_confirmed)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError) as error:
        # 系统/子进程异常不回显 argv、原始输出或私密文件内容。
        message = str(error) if isinstance(error, (FleetError, ProvisioningError)) else "操作失败；请检查文件路径、权限和工具环境，未确认成功"
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
