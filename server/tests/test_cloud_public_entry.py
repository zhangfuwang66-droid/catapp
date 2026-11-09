"""验证 HTTPS 反代边界；仅运行本地模拟账号，不访问真实模型或设备。"""

import asyncio
from contextlib import ExitStack
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

import cloud_public_entry
from cloud_test_entry import prepare_environment as prepare_test_environment
from identity_store import IdentityStore
from memory_store import MemoryStore
import test_cloud_test_entry as tunnel_tests


SERVER_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_URL = "https://cat.example.test"


class PublicConfigurationTests(unittest.TestCase):
    def test_https_domain_and_ipv4_origins_are_accepted_and_normalized(self):
        valid = {
            PUBLIC_URL: PUBLIC_URL,
            "https://CAT.Example.Test:443/": PUBLIC_URL,
            "https://cat.example.test:8443/": PUBLIC_URL + ":8443",
            "https://192.0.2.5": "https://192.0.2.5",
            "https://192.0.2.5:443/": "https://192.0.2.5",
            "https://192.0.2.5:8443/": "https://192.0.2.5:8443",
        }
        for value, expected in valid.items():
            with self.subTest(value=value), patch.dict(os.environ, {"CYBERMIAO_PUBLIC_URL": value}, clear=True):
                self.assertEqual(cloud_public_entry.public_url_from_environment(), expected)
        for value in ("", "http://cat.example.test", "http://192.0.2.5", "https://[::1]",
                      "https://localhost", PUBLIC_URL + "/console", PUBLIC_URL + "?secret=example",
                      PUBLIC_URL + "#fragment", "https://name:password@cat.example.test",
                      PUBLIC_URL + ":0", PUBLIC_URL + ":65536", PUBLIC_URL + ":",
                      "https://cat_example.test", "https://-cat.example.test", "https://猫.example.test",
                      "https://cat.\nexample.test", "https://[bad-address", "https://cat.example.test\\evil",
                      "https://999.0.2.5", "https://192.000.2.5", "https://192.2.5",
                      "https://3221225989", "https://192.0.2.5.", "https://192.0.2.5:",
                      "https://192.0.2.5/path", "https://name:password@192.0.2.5"):
            with self.subTest(value=value), patch.dict(os.environ, {"CYBERMIAO_PUBLIC_URL": value}, clear=True):
                with self.assertRaises(ValueError):
                    cloud_public_entry.public_url_from_environment()

    def test_invalid_origin_fails_before_loading_credentials_or_data(self):
        with patch.dict(os.environ, {"CYBERMIAO_PUBLIC_URL": "http://cat.example.test"}, clear=True), \
                patch.object(cloud_public_entry, "prepare_test_environment") as prepare:
            with self.assertRaises(ValueError):
                cloud_public_entry.prepare_environment()
            prepare.assert_not_called()

    def test_public_and_tunnel_entries_keep_distinct_origin_settings(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            "CYBERMIAO_DATA_DIR": folder, "CYBERMIAO_PUBLIC_URL": PUBLIC_URL,
            "DASHSCOPE_API_KEY": "simulated-key", "CYBERMIAO_ACCESS_MODE": "local",
        }, clear=True):
            root, port = cloud_public_entry.prepare_environment()
            self.assertEqual(root, Path(folder).resolve())
            self.assertEqual(port, 8081)
            self.assertEqual(os.environ["CYBERMIAO_ACCESS_MODE"], "accounts")
            self.assertEqual(os.environ["CYBERMIAO_PUBLIC_URL"], PUBLIC_URL)
            self.assertEqual(os.environ["CYBERMIAO_MEMORY_DEBUG_API"], "0")
            prepare_test_environment()
            self.assertEqual(os.environ["CYBERMIAO_PUBLIC_URL"], "")

    def test_forwarded_headers_are_ignored_for_untrusted_peers(self):
        async def observe(scope, receive, send):
            return scope["scheme"], scope["client"][0]

        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            "CYBERMIAO_DATA_DIR": folder, "CYBERMIAO_PUBLIC_URL": PUBLIC_URL,
            "DASHSCOPE_API_KEY": "simulated-key",
        }, clear=True), patch.dict(sys.modules, {"app": SimpleNamespace(app=observe)}), \
                patch("runtime_logging.configure_logging"), patch("uvicorn.run") as run:
            self.assertEqual(cloud_public_entry.main(), 0)
        config = run.call_args.kwargs
        self.assertEqual(config["host"], "127.0.0.1")
        self.assertEqual(config["workers"], 1)
        self.assertTrue(config["proxy_headers"])
        middleware = ProxyHeadersMiddleware(observe, trusted_hosts=config["forwarded_allow_ips"])
        for peer, scheme, host in (("127.0.0.1", "https", "198.51.100.9"),
                                   ("203.0.113.8", "http", "203.0.113.8")):
            scope = {"type": "http", "scheme": "http", "client": (peer, 1234),
                     "headers": [(b"x-forwarded-proto", b"https"), (b"x-forwarded-for", b"198.51.100.9")]}
            self.assertEqual(asyncio.run(middleware(scope, None, None)), (scheme, host))


class PublicProcessTests(unittest.TestCase):
    def test_proxy_login_secure_cookie_websocket_and_origin_checks(self):
        self.check_proxy_origin(PUBLIC_URL)

    def test_ipv4_proxy_login_secure_cookie_websocket_and_origin_checks(self):
        self.check_proxy_origin("https://192.0.2.5")

    def test_ipv4_nondefault_port_keeps_origin_boundary(self):
        self.check_proxy_origin("https://192.0.2.5:8443")

    def check_proxy_origin(self, public_url):
        with tempfile.TemporaryDirectory(prefix="cybermiao-proxy-test-") as folder, ExitStack() as stack:
            root = Path(folder)
            database = root / "memory.sqlite3"
            MemoryStore(database)
            identities = IdentityStore(database)
            user = identities.create_user("alice", "alice-test-password")
            device = identities.register_device("cat-alice", "模拟小喵")
            grant = identities.request_pairing(device.device_id, device.token)
            identities.claim_pairing(grant.code, user.id, client_key="fixture")
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            environment = os.environ.copy()
            environment.pop("CREDENTIALS_DIRECTORY", None)
            environment.update({
                "CYBERMIAO_DATA_DIR": folder, "CYBERMIAO_CLOUD_TEST_PORT": str(port),
                "CYBERMIAO_PUBLIC_URL": public_url, "DASHSCOPE_API_KEY": "simulated-proxy-key",
                "HAND_TRACKING_ENABLED": "0", "COVER_INTERACTION_ENABLED": "0",
            })
            process = subprocess.Popen([sys.executable, str(SERVER_ROOT / "cloud_public_entry.py")],
                                       env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
            stack.callback(tunnel_tests.CloudTestProcessTests.stop_process, process)
            client = stack.enter_context(httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=3,
                                                      trust_env=False, headers={"Host": public_url.removeprefix("https://"),
                                                      "X-Forwarded-Proto": "https"}))
            deadline = time.monotonic() + 30
            while True:
                if process.poll() is not None:
                    self.fail("HTTPS 反代测试进程提前退出")
                try:
                    if client.get("/api/ready").status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                if time.monotonic() > deadline:
                    self.fail("HTTPS 反代测试服务未就绪")
                time.sleep(0.1)

            self.assertEqual(client.get("/api/auth/mode").json()["mode"], "accounts")
            self.assertEqual(client.get("/api/knowledge").status_code, 401)
            credentials = {"username": "alice", "password": "alice-test-password"}
            for other_origin in ("https://other.example.test", public_url.replace("https://", "http://"),
                                 "https://192.0.2.5:8443" if public_url == "https://192.0.2.5"
                                 else "https://192.0.2.5"):
                if other_origin != public_url:
                    self.assertEqual(client.post("/api/auth/login", json=credentials,
                                     headers={"Origin": other_origin}).status_code, 403)
            login = client.post("/api/auth/login", json=credentials, headers={"Origin": public_url})
            self.assertEqual(login.status_code, 200)
            for flag in ("secure", "httponly", "samesite=strict"):
                self.assertIn(flag, login.headers["set-cookie"].lower())
            # TLS 由反向代理终结；测试客户端到回环上游是 HTTP，手动转发该模拟会话。
            cookie = login.headers["set-cookie"].split(";", 1)[0]
            headers = {"Cookie": cookie, "Origin": public_url}
            self.assertEqual(client.get("/api/auth/session", headers=headers).status_code, 200)
            self.assertEqual(client.get("/api/knowledge", headers=headers).status_code, 200)
            ws_url = f"ws://127.0.0.1:{port}/ws/devices/cat-alice/viewer"
            with connect(ws_url, additional_headers={**headers, "X-Forwarded-Proto": "https"},
                         open_timeout=5, close_timeout=5, proxy=None):
                pass
            with self.assertRaises(InvalidStatus):
                with connect(ws_url, additional_headers={**headers, "Origin": "https://other.example.test",
                             "X-Forwarded-Proto": "https"}, open_timeout=5, close_timeout=5, proxy=None):
                    pass
            self.assertEqual(client.post("/api/auth/logout", headers=headers).status_code, 403)
            headers["X-CSRF-Token"] = login.json()["csrf_token"]
            self.assertEqual(client.post("/api/auth/logout", headers={**headers,
                             "Origin": "https://other.example.test"}).status_code, 403)
            self.assertEqual(client.post("/api/auth/logout", headers=headers).status_code, 200)
            self.assertEqual(client.get("/api/auth/session", headers=headers).status_code, 401)


if __name__ == "__main__":
    unittest.main()
