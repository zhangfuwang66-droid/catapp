"""同机 HTTPS 反向代理后的账号模式入口；后端仍只监听回环地址。"""

import ipaddress
import os
import re
from urllib.parse import urlsplit

from cloud_test_entry import prepare_environment as prepare_test_environment, run_server


def public_url_from_environment():
    url = os.getenv("CYBERMIAO_PUBLIC_URL", "").strip()
    message = "CYBERMIAO_PUBLIC_URL 必须是使用域名或 IPv4 的 HTTPS 站点地址，不含路径或凭据"
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        port = parsed.port
        if (parsed.scheme != "https" or not parsed.netloc or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"} or any(char.isspace() for char in url)
                or "." not in host or len(host) > 253
                or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                           for label in host.split("."))
                or (port is not None and not 1 <= port <= 65535)):
            raise ValueError(message)
        # 全数字和点组成的主机必须是规范 IPv4，拒绝错误地址及前导零写法。
        if re.fullmatch(r"[0-9.]+", host):
            ipaddress.IPv4Address(host)
        # 不接受空端口或其他解析后会消失的地址写法。
        authority = host if port is None else f"{host}:{port}"
        if parsed.netloc.lower() != authority:
            raise ValueError(message)
    except ValueError:
        raise ValueError(message) from None
    return f"https://{host}" + (f":{port}" if port not in {None, 443} else "")


def prepare_environment():
    # 验证失败时不加载应用、不创建数据，也不回退为匿名或明文公网服务。
    public_url = public_url_from_environment()
    data_dir, port = prepare_test_environment()
    os.environ["CYBERMIAO_PUBLIC_URL"] = public_url
    return data_dir, port


def main():
    return run_server(prepare_environment, proxy_headers=True)


if __name__ == "__main__":
    raise SystemExit(main())
