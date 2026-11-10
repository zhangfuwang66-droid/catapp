"""仅供 SSH 隧道访问的账号模式测试入口。"""

import os
from pathlib import Path
import sys


def prepare_environment():
    directory = os.getenv("CYBERMIAO_DATA_DIR", "").strip()
    if not directory or not Path(directory).is_absolute():
        raise ValueError("CYBERMIAO_DATA_DIR 必须是独立数据目录的绝对路径")
    data_dir = Path(directory).resolve()
    try:
        port = int(os.getenv("CYBERMIAO_CLOUD_TEST_PORT", "8081"))
    except ValueError:
        raise ValueError("CYBERMIAO_CLOUD_TEST_PORT 必须是有效端口") from None
    if not 1 <= port <= 65535:
        raise ValueError("CYBERMIAO_CLOUD_TEST_PORT 必须是有效端口")

    credentials_dir = os.getenv("CREDENTIALS_DIRECTORY")
    if credentials_dir:
        try:
            key = (Path(credentials_dir) / "dashscope_api_key").read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            key = ""
        except (OSError, UnicodeError):
            raise ValueError("无法读取 systemd 交付的 API Key") from None
    else:
        key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if "\n" in key or "\r" in key:
        raise ValueError("API Key 格式无效")

    os.environ.update({
        "DASHSCOPE_API_KEY": key,
        "CYBERMIAO_DATA_DIR": str(data_dir),
        "CYBERMIAO_MEMORY_DB_PATH": str(data_dir / "memory.sqlite3"),
        "CYBERMIAO_BACKUP_DIR": str(data_dir / "backups"),
        "CYBERMIAO_ACCESS_MODE": "accounts",
        "CYBERMIAO_PUBLIC_URL": "",
        "CYBERMIAO_MEMORY_DEBUG_API": "0",
        "ASR_DEBUG_RAW": "0",
    })
    return data_dir, port


def run_server(prepare, *, proxy_headers=False):
    if os.name != "nt":
        os.umask(0o077)
    try:
        data_dir, port = prepare()
        from runtime_logging import configure_logging
        configure_logging(data_dir / "logs")
    except (OSError, ValueError) as exc:
        print(f"云端入口启动失败：{exc}", file=sys.stderr)
        return 1

    import uvicorn
    from app import app

    uvicorn.run(app, host="127.0.0.1", port=port, workers=1, log_config=None,
                log_level="info", access_log=False, proxy_headers=proxy_headers,
                forwarded_allow_ips="127.0.0.1")
    return 0


def main():
    return run_server(prepare_environment)


if __name__ == "__main__":
    raise SystemExit(main())
