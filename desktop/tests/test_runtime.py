"""本地集成测试；使用无效测试 Key，不请求云端，也不访问摄像头。"""
import multiprocessing
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from config import CyberMiaoConfig
from server_manager import NetworkUtils, ServerManager


def unused_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class ConfigTests(unittest.TestCase):
    def test_legacy_config_is_migrated_outside_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "legacy.ini"
            legacy.write_text("[Server]\nport=8090\nauto_open_browser=false\n[API]\ndashscope_api_key=local-migration-value\n", encoding="utf-8")
            config = CyberMiaoConfig(root / "user" / "settings.ini", legacy)
            self.assertEqual(config.get("UI", "auto_open_browser"), "false")
            self.assertEqual(config.server_options()["port"], 8090)
            self.assertTrue(config.has_api_key())
            self.assertTrue(config.config_path.is_file())

    def test_atomic_validation_and_literal_percent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.ini"
            config = CyberMiaoConfig(path)
            config.update("local-test-%-value", "8081", False)
            saved = path.read_bytes()
            self.assertEqual(CyberMiaoConfig(path).get("API", "dashscope_api_key"), "local-test-%-value")
            for invalid in ("0", "65536", "abc", ""):
                with self.assertRaises(ValueError):
                    config.update("replacement", invalid, True)
                self.assertEqual(path.read_bytes(), saved)
            self.assertEqual(config.get("Server", "port"), "8081")


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="小喵 runtime ")
        self.data_dir = Path(self.directory.name)
        self.config = CyberMiaoConfig(self.data_dir / "settings.ini")
        self.port = unused_port()
        self.config.update("local-smoke-test-value", str(self.port), False)
        self.manager = ServerManager(self.config, self.data_dir)
        self.lines = []
        self.manager.set_log_callback(self.lines.append)

    def wait_for(self, condition, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.manager.poll()
            if condition():
                return
            time.sleep(0.05)
        self.fail("等待服务状态超时：" + self.manager.state + "\n" + "\n".join(self.lines[-20:]))

    def tearDown(self):
        self.manager.stop_server()
        self.wait_for(lambda: self.manager.process is None, timeout=20)
        self.manager.close()
        self.directory.cleanup()

    def test_no_key_and_port_conflict(self):
        self.config.config["API"]["dashscope_api_key"] = ""
        self.assertFalse(self.manager.start_server())
        self.assertIsNone(self.manager.process)
        self.config.update("local-smoke-test-value", str(self.port), False)
        with socket.socket() as listener:
            listener.bind(("0.0.0.0", self.port))
            listener.listen()
            self.assertFalse(self.manager.start_server())
            self.assertIsNone(self.manager.process)

    def test_health_websocket_stop_restart_and_redacted_logs(self):
        self.assertTrue(self.manager.start_server())
        self.assertFalse(self.manager.start_server())
        first_pid = self.manager.process.pid
        self.wait_for(lambda: self.manager.state in {"running", "error"})
        self.assertEqual(self.manager.state, "running", "\n".join(self.lines))
        with urlopen(f"http://127.0.0.1:{self.port}/api/health", timeout=3) as response:
            self.assertEqual(response.read(), b"OK")
        with urlopen(f"http://127.0.0.1:{self.port}/", timeout=3) as response:
            self.assertIn("text/html", response.headers["content-type"])
            self.assertGreater(len(response.read()), 1000)
        self.assertTrue((self.data_dir / "records").is_dir())
        from websockets.sync.client import connect
        with connect(f"ws://127.0.0.1:{self.port}/ws/viewer", origin=f"http://127.0.0.1:{self.port}", proxy=None) as viewer:
            self.manager.stop_server()
            self.wait_for(lambda: self.manager.process is None, timeout=20)
        self.assertTrue(NetworkUtils.is_port_available(self.port))
        self.manager.log("test secret: local-smoke-test-value")
        self.config.update("local-smoke-replacement", str(self.port), False)
        self.assertTrue(self.manager.start_server())
        self.assertNotEqual(first_pid, self.manager.process.pid)
        self.wait_for(lambda: self.manager.state in {"running", "error"})
        self.assertEqual(self.manager.state, "running", "\n".join(self.lines))
        self.manager.stop_server()
        self.wait_for(lambda: self.manager.process is None, timeout=20)
        self.assertNotIn("local-smoke-test-value", self.manager.log_path.read_text(encoding="utf-8"))

    def test_stop_while_starting(self):
        self.assertTrue(self.manager.start_server())
        self.manager.stop_server()
        self.wait_for(lambda: self.manager.process is None, timeout=20)
        self.assertEqual(self.manager.state, "stopped")
        self.assertTrue(NetworkUtils.is_port_available(self.port))


if __name__ == "__main__":
    multiprocessing.freeze_support()
    unittest.main()
