"""只在服务器或本机终端管理测试账号，密码不作为命令参数或普通输出。"""

import argparse
import getpass
import json
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from identity_store import IdentityStore
from runtime_paths import DATA_ROOT


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path(os.getenv("CYBERMIAO_MEMORY_DB_PATH", str(DATA_ROOT / "memory.sqlite3"))))
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("user-add", "user-password"):
        sub = commands.add_parser(command)
        sub.add_argument("--username", required=True)
    commands.add_parser("users")
    for command in ("device-add", "device-rotate", "device-revoke"):
        sub = commands.add_parser(command)
        sub.add_argument("--device-id", required=True)
        if command in {"device-add", "device-rotate"}:
            sub.add_argument("--credential-output", required=True, type=Path)
        if command == "device-add":
            sub.add_argument("--name", default="赛博小喵")
    commands.add_parser("devices")
    options = parser.parse_args(arguments)
    try:
        if options.command not in {"user-add", "device-add"} and not options.database.expanduser().is_file():
            raise ValueError("数据库不存在，请检查路径")
        store = IdentityStore(options.database)
        if options.command == "users":
            result = {"ok": True, "users": store.list_users()}
        elif options.command == "devices":
            result = {"ok": True, "devices": store.list_devices()}
        elif options.command == "device-revoke":
            store.revoke_device(options.device_id)
            result = {"ok": True, "message": "设备已停用，已有凭证已撤销"}
        elif options.command in {"device-add", "device-rotate"}:
            output = options.credential_output.expanduser().resolve()
            if not output.name.endswith(".secret.json"):
                raise ValueError("凭证文件名必须以 .secret.json 结尾，避免误提交 Git")
            output.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    if options.command == "device-add":
                        grant = store.register_device(options.device_id, options.name)
                    else:
                        grant = store.rotate_device_credential(options.device_id)
                    json.dump({"version": 1, "device_id": grant.device_id, "device_token": grant.token}, stream, ensure_ascii=False)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                # 只删除本次独占创建的目标，已有凭证不会被覆盖。
                output.unlink(missing_ok=True)
                raise
            result = {"ok": True, "device_id": grant.device_id, "credential_file": str(output),
                      "message": "凭证仅保存到私密文件，尚未写入设备"}
        else:
            password = getpass.getpass("请输入密码（至少 10 位，不显示）：")
            confirmation = getpass.getpass("请再次输入密码：")
            if password != confirmation:
                raise ValueError("两次密码不一致")
            if options.command == "user-add":
                user = store.create_user(options.username, password)
                result = {"ok": True, "user_id": user.id, "username": user.username}
            else:
                store.change_password(options.username, password)
                result = {"ok": True, "message": "密码已更新，已有登录会话已撤销"}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    if os.name != "nt":
        os.umask(0o077)
    raise SystemExit(main())
