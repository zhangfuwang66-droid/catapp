"""用独立进程与模拟设备验证云端测试入口，不调用真实模型。"""

from contextlib import ExitStack
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

from cloud_test_entry import prepare_environment
from identity_store import IdentityStore
from memory_store import MemoryStore
from tools.memory_database import backup_status


SERVER_ROOT = Path(__file__).resolve().parents[1]


class CloudTestConfigurationTests(unittest.TestCase):
    def test_requires_absolute_data_directory_and_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                prepare_environment()
            os.environ["CYBERMIAO_DATA_DIR"] = "relative"
            with self.assertRaises(ValueError):
                prepare_environment()
            os.environ["CYBERMIAO_DATA_DIR"] = str(SERVER_ROOT)
            with self.assertRaises(ValueError):
                prepare_environment()

    def test_systemd_credential_takes_precedence_and_invalid_port_fails(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            root = Path(folder)
            (root / "dashscope_api_key").write_text("credential-key\n", encoding="utf-8")
            os.environ.update(CYBERMIAO_DATA_DIR=str(root), CREDENTIALS_DIRECTORY=str(root),
                              DASHSCOPE_API_KEY="environment-key", CYBERMIAO_ACCESS_MODE="local")
            data_dir, port = prepare_environment()
            self.assertEqual(data_dir, root.resolve())
            self.assertEqual(port, 8081)
            self.assertEqual(os.environ["DASHSCOPE_API_KEY"], "credential-key")
            self.assertEqual(os.environ["CYBERMIAO_ACCESS_MODE"], "accounts")
            self.assertEqual(os.environ["CYBERMIAO_MEMORY_DB_PATH"], str(data_dir / "memory.sqlite3"))
            self.assertEqual(os.environ["CYBERMIAO_BACKUP_DIR"], str(data_dir / "backups"))
            os.environ["CYBERMIAO_CLOUD_TEST_PORT"] = "65536"
            with self.assertRaises(ValueError):
                prepare_environment()
            os.environ["CYBERMIAO_CLOUD_TEST_PORT"] = "8081"
            (root / "dashscope_api_key").unlink()
            with self.assertRaises(ValueError):
                prepare_environment()


class CloudTestProcessTests(unittest.TestCase):
    def test_real_process_accounts_devices_websocket_and_backup(self):
        with tempfile.TemporaryDirectory(prefix="cybermiao-cloud-test-") as folder, ExitStack() as stack:
            root = Path(folder)
            data_dir = root / "data"
            database = data_dir / "memory.sqlite3"
            store = MemoryStore(database)
            identities = IdentityStore(database)
            devices = {}
            for name in ("alice", "bob"):
                user = identities.create_user(name, name + "-test-password")
                device = identities.register_device("cat-" + name, name)
                grant = identities.request_pairing(device.device_id, device.token)
                identities.claim_pairing(grant.code, user.id, client_key="fixture")
                conversation = store.create_conversation(user.id, device.device_id)
                last = store.append_message(conversation, "user", name + "-private-message")
                store.save_summary(conversation, last, name + "-private-summary")
                devices[name] = device
            devices["new"] = identities.register_device("cat-new", "new")

            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            environment = os.environ.copy()
            environment.pop("CREDENTIALS_DIRECTORY", None)
            environment.update({
                "CYBERMIAO_DATA_DIR": str(data_dir),
                "CYBERMIAO_CLOUD_TEST_PORT": str(port),
                "CYBERMIAO_ACCESS_MODE": "local",
                "CYBERMIAO_MEMORY_DB_PATH": str(root / "wrong.sqlite3"),
                "CYBERMIAO_BACKUP_DIR": str(root / "wrong-backups"),
                "CYBERMIAO_PUBLIC_URL": "http://invalid.example",
                "DASHSCOPE_API_KEY": "simulated-test-key",
                "HAND_TRACKING_ENABLED": "0",
                "COVER_INTERACTION_ENABLED": "0",
            })
            process = subprocess.Popen([sys.executable, str(SERVER_ROOT / "cloud_test_entry.py")],
                                       env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
            stack.callback(self.stop_process, process)
            url = f"http://127.0.0.1:{port}"
            client = stack.enter_context(httpx.Client(base_url=url, timeout=3, trust_env=False))
            deadline = time.monotonic() + 30
            while True:
                if process.poll() is not None:
                    self.fail("测试服务提前退出")
                try:
                    if client.get("/api/ready").status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                if time.monotonic() > deadline:
                    self.fail("测试服务未在规定时间内就绪")
                time.sleep(0.1)

            self.assertEqual(client.get("/api/health").text, "OK")
            self.assertEqual(client.get("/api/auth/mode").json()["mode"], "accounts")
            self.assertEqual(client.get("/api/memory").status_code, 404)
            self.assertEqual(client.get("/api/devices").status_code, 401)
            self.assertEqual(client.get("/api/devices/cat-alice/binding").status_code, 401)

            device_headers = {"Authorization": "Bearer " + devices["new"].token}
            code = client.post("/api/devices/cat-new/pairing", headers=device_headers).json()["code"]
            self.assertFalse(client.get("/api/devices/cat-new/binding", headers=device_headers).json()["bound"])
            login = client.post("/api/auth/login", headers={"Origin": url},
                                json={"username": "alice", "password": "alice-test-password"})
            self.assertEqual(login.status_code, 200)
            headers = {"Origin": url, "X-CSRF-Token": login.json()["csrf_token"]}
            self.assertEqual(client.post("/api/devices/pair", headers=headers, json={"code": code}).status_code, 200)
            self.assertTrue(client.get("/api/devices/cat-new/binding", headers=device_headers).json()["bound"])
            own = client.get("/api/devices/cat-alice/memory")
            self.assertEqual(own.status_code, 200)
            self.assertIn("alice-private-summary", own.text)
            self.assertNotIn("bob-private-summary", own.text)
            self.assertEqual(client.get("/api/devices/cat-bob/memory").status_code, 404)

            ws_url = f"ws://127.0.0.1:{port}/ws/devices/cat-alice/audio"
            with connect(ws_url, additional_headers={"Authorization": "Bearer " + devices["alice"].token},
                         open_timeout=5, close_timeout=5, proxy=None):
                pass
            with self.assertRaises(InvalidStatus):
                with connect(ws_url, additional_headers={"Authorization": "Bearer " + devices["bob"].token},
                             open_timeout=5, close_timeout=5, proxy=None):
                    pass

            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                result = backup_status(database, data_dir / "backups")
                if result["valid_backups"]:
                    break
                time.sleep(0.1)
            self.assertGreaterEqual(result["valid_backups"], 1)
            self.assertEqual(result["skipped_unverified"], 0)
            self.assertFalse((root / "wrong.sqlite3").exists())
            self.assertFalse((root / "wrong-backups").exists())
            log = (data_dir / "logs" / "CyberMiao.log").read_text(encoding="utf-8")
            for private in ("simulated-test-key", "alice-private-message", devices["alice"].token):
                self.assertNotIn(private, log)

    @staticmethod
    def stop_process(process):
        if process.poll() is None:
            try:
                if os.name == "nt":
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    process.terminate()
                process.wait(timeout=15)
            except (OSError, subprocess.TimeoutExpired):
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                elif process.poll() is None:
                    process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
