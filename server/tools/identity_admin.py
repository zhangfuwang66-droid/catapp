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
    options = parser.parse_args(arguments)
    try:
        if options.command != "user-add" and not options.database.expanduser().is_file():
            raise ValueError("数据库不存在，请检查路径")
        store = IdentityStore(options.database)
        if options.command == "users":
            result = {"ok": True, "users": store.list_users()}
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
