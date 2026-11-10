"""仅在服务端显式授权/撤销管理员；不读取模型配置或凭证文件。"""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from identity_store import IdentityStore
from admin_store import AdminStore


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    commands = parser.add_subparsers(dest='command',required=True)
    for name in ('grant','revoke'):
        commands.add_parser(name).add_argument('--username',required=True)
    commands.add_parser('list')
    options = parser.parse_args(arguments)
    try:
        if not options.database.expanduser().is_file():
            raise ValueError('数据库不存在，请核对路径')
        store = AdminStore(IdentityStore(options.database))
        if options.command == 'list':
            result = {'admins':store.roles()}
        else:
            store.set_role(options.username,options.command == 'grant')
            result = {'ok':True,'message':'管理员授权已更新，下一次管理请求立即生效'}
        print(json.dumps(result,ensure_ascii=False))
        return 0
    except (ValueError,OSError,sqlite3.Error):
        print(json.dumps({'ok':False,'error':'操作失败：请检查数据库、账号状态和最后管理员保护'},ensure_ascii=False),file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
