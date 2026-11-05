"""通过 USB 配发设备凭证或配置网络；不会烧录固件，不在命令参数中接受密码。"""

from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit
import uuid

PREFIX = b"CMIAO1 "
DEVICE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
TOKEN = re.compile(r"[A-Za-z0-9_-]{32,128}\Z")
PUBLIC_FIELDS = {"protocol", "device_id", "credential_configured", "pending_reboot", "identity_saved",
                 "has_wifi", "server_url", "mode", "setup_ssid", "ok"}


class ProvisioningError(ValueError):
    pass


def validate_server_url(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 300 or '?' in value or '#' in value:
        raise ProvisioningError("服务器地址长度无效，或包含查询参数和片段")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        raise ProvisioningError("端口需为 1～65535") from None
    host = parsed.hostname or ""
    if (parsed.scheme not in {"http", "https"} or not host or len(host) > 253 or parsed.username is not None
            or parsed.password is not None or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or not re.fullmatch(r"[A-Za-z0-9.-]+", host) or (port is not None and port == 0)):
        raise ProvisioningError("服务器地址需为 http/https 域名或 IPv4，可带端口，不能含路径、凭据或查询参数")
    if any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in host.split('.')):
        raise ProvisioningError("服务器主机名格式无效")
    if re.fullmatch(r"[0-9.]+", host):
        import ipaddress
        try:
            ipaddress.IPv4Address(host)
        except ValueError:
            raise ProvisioningError("IPv4 地址无效") from None
    return f"{parsed.scheme}://{host}:{port or (443 if parsed.scheme == 'https' else 80)}"


def load_credential(path: Path) -> dict:
    if not path.name.endswith(".secret.json") or path.stat().st_size > 4096:
        raise ProvisioningError("请使用身份管理工具生成的 .secret.json 凭证文件")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        raise ProvisioningError("凭证文件格式无效") from None
    if (not isinstance(data, dict) or data.get("version") != 1
            or not isinstance(data.get("device_id"), str) or not DEVICE_ID.fullmatch(data["device_id"])
            or not isinstance(data.get("device_token"), str) or not TOKEN.fullmatch(data["device_token"])):
        raise ProvisioningError("凭证文件内容无效")
    return {"device_id": data["device_id"], "device_token": data["device_token"]}


class ProvisioningClient:
    def __init__(self, stream, *, timeout=10):
        self.stream = stream
        self.timeout = timeout
        self.buffer = b""

    def exchange(self, operation, **fields):
        request_id = uuid.uuid4().hex
        encoded = PREFIX + json.dumps({"op": operation, "request_id": request_id, **fields}, ensure_ascii=False).encode("utf-8")
        if len(encoded) > 1536:
            raise ProvisioningError("配置超过设备接收上限")
        payload = encoded + b"\n"
        if self.stream.write(payload) != len(payload):
            raise ProvisioningError("USB 写入未完成，未确认配置成功")
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            self.buffer += self.stream.read(256)
            if len(self.buffer) > 8192:
                self.buffer = b""
                continue
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                marker = line.find(PREFIX)
                if marker < 0:
                    continue
                try:
                    response = json.loads(line[marker + len(PREFIX):])
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(response, dict) or response.get("request_id") != request_id:
                    continue
                if response.get("protocol") != 1 or not isinstance(response.get("device_id"), str) or not DEVICE_ID.fullmatch(response["device_id"]):
                    raise ProvisioningError("设备身份协议不匹配")
                if response.get("ok") is not True:
                    # 固件仅返回预定义错误，原始请求和设备输出从不转发到终端。
                    raise ProvisioningError("设备拒绝操作，请核对身份、网络配置及是否正在重启")
                return {key: value for key, value in response.items() if key in PUBLIC_FIELDS}
        raise ProvisioningError("设备未响应身份协议；请确认端口与新版固件，旧固件不能使用此工具")

    def provision(self, credential):
        status = self.exchange("status")
        if status["device_id"] != credential["device_id"]:
            raise ProvisioningError("凭证编号与 USB 设备不一致，未写入凭证")
        result = self.exchange("provision", **credential)
        if result["device_id"] != status["device_id"] or not result.get("identity_saved") or not result.get("pending_reboot"):
            raise ProvisioningError("设备未确认保存凭证，不能视为成功")
        return result


def main(arguments=None, *, serial_factory=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="USB 串口，例如 COM3")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("setup", help="在设备上打开本地配置热点")
    provision = commands.add_parser("provision")
    provision.add_argument("--credential-file", required=True, type=Path)
    configure = commands.add_parser("configure")
    configure.add_argument("--server-url", required=True)
    configure.add_argument("--mode", choices=("local", "accounts"), required=True)
    configure.add_argument("--wifi", action="store_true", help="交互输入 Wi-Fi，密码隐藏输入")
    options = parser.parse_args(arguments)
    try:
        credential = load_credential(options.credential_file) if options.command == "provision" else None
        server_url = validate_server_url(options.server_url) if options.command == "configure" else None
        fields = {}
        if options.command == "configure" and options.wifi:
            fields["wifi_ssid"] = input("Wi-Fi 名称：").strip()
            fields["wifi_password"] = getpass.getpass("Wi-Fi 密码（不显示，开放网络直接回车）：")
            if not 1 <= len(fields["wifi_ssid"].encode("utf-8")) <= 32 or len(fields["wifi_password"].encode("utf-8")) > 64:
                raise ProvisioningError("Wi-Fi 名称或密码长度无效")
        if serial_factory is None:
            try:
                import serial
            except ImportError:
                raise ProvisioningError("请先安装 firmware/tools/requirements.txt 中的 pyserial") from None
            stream = serial.Serial(port=None, baudrate=115200, timeout=.2, write_timeout=3)
            stream.dtr = False; stream.rts = False; stream.port = options.port; stream.open()
        else:
            stream = serial_factory(options.port)
        try:
            client = ProvisioningClient(stream)
            if options.command == "provision":
                result = client.provision(credential)
            elif options.command == "configure":
                status = client.exchange("status")
                if options.mode == "accounts" and not status.get("credential_configured"):
                    raise ProvisioningError("请先配发设备凭证，重启后再配置账号模式")
                if not options.wifi and not status.get("has_wifi"):
                    raise ProvisioningError("设备没有 Wi-Fi 配置，请使用 --wifi 交互输入")
                result = client.exchange("configure", server_url=server_url, mode=options.mode, **fields)
                if not result.get("pending_reboot"):
                    raise ProvisioningError("设备未确认重启，不能视为配置成功")
            else:
                result = client.exchange(options.command)
        finally:
            stream.close()
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
