"""新版桌面工作流回归；隔离配置，不调用云端或访问硬件。"""
from pathlib import Path
import os
import socket
import sys
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from launcher import CyberMiaoGUI


@unittest.skipUnless(sys.platform == "win32", "桌面发行版面向 Windows")
class DesktopWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="小喵 GUI 测试 ")
        self.environment = patch.dict(os.environ, CYBERMIAO_DATA_DIR=self.directory.name)
        self.environment.start()
        self.gui = CyberMiaoGUI()
        self.errors = []
        self.gui.root.report_callback_exception = lambda *args: self.errors.append(str(args[1]))
        self.gui.root.update()

    def wait_for(self, condition, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.gui.root.update()
            if condition():
                return
            time.sleep(.025)
        self.fail("等待 GUI 状态超时：" + self.gui.server_manager.state)

    def tearDown(self):
        self.gui.exit_action()
        try:
            deadline = time.monotonic() + 20
            while self.gui.root.winfo_exists() and time.monotonic() < deadline:
                self.gui.root.update()
                time.sleep(.025)
        except tk.TclError:
            pass
        self.environment.stop()
        self.directory.cleanup()
        self.assertEqual(self.errors, [])

    def configure(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        self.gui.api_key_entry.delete(0, "end")
        self.gui.api_key_entry.insert(0, "local-gui-check-only")
        self.gui.port_entry.delete(0, "end")
        self.gui.port_entry.insert(0, str(port))
        self.gui.auto_browser_var.set(False)
        self.gui.settings_save_btn.invoke()
        self.assertTrue(self.gui.config.has_api_key())
        self.assertIn("配置已保存", self.gui.save_result.get())
        return port

    def test_first_launch_validation_and_navigation(self):
        self.assertEqual(self.gui.current_page, "settings")
        self.gui.settings_save_btn.invoke()
        self.assertIn("保存失败", self.gui.save_result.get())
        self.gui.nav_buttons["home"].invoke()
        self.gui.start_btn.invoke()
        self.assertEqual(self.gui.current_page, "settings")
        self.assertIsNone(self.gui.server_manager.process)
        self.configure()
        self.gui.visibility_btn.invoke()
        self.assertEqual(self.gui.api_key_entry.cget("show"), "")
        self.gui.visibility_btn.invoke()
        self.assertTrue(self.gui.api_key_entry.cget("show"))
        self.gui.auto_browser_switch.toggle()
        self.assertTrue(self.gui.auto_browser_var.get())
        self.gui.nav_buttons["home"].invoke()
        self.gui.root.update()
        self.assertEqual(self.gui.current_page, "home")
        self.assertFalse(self.gui.setup_notice.winfo_ismapped())
        self.gui.show_logs()
        self.gui.root.update()
        self.assertTrue(self.gui.logs_panel.winfo_ismapped())
        self.gui.server_manager.log("检查异常 local-gui-check-only")
        self.assertIn("检查异常", self.gui.log_text.get("1.0", "end"))
        self.assertNotIn("local-gui-check-only", self.gui.log_text.get("1.0", "end"))
        saved_log = self.gui.server_manager.log_path.read_bytes()
        self.gui.clear_log_view()
        self.assertEqual(self.gui.log_text.get("1.0", "end").strip(), "")
        self.assertEqual(self.gui.server_manager.log_path.read_bytes(), saved_log)
        self.gui.nav_buttons["about"].invoke()
        self.assertEqual(self.gui.current_page, "about")

    def test_buttons_start_restart_tray_and_stop(self):
        port = self.configure()
        self.gui.nav_buttons["home"].invoke()
        self.gui.start_btn.invoke()
        self.wait_for(lambda: self.gui.server_manager.state in {"running", "error"})
        self.assertEqual(self.gui.server_manager.state, "running")
        self.assertEqual(self.gui.browser_btn.cget("state"), "normal")
        self.assertEqual(self.gui.start_btn.cget("state"), "disabled")
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as response:
            self.assertEqual(response.read(), b"OK")
        first_pid = self.gui.server_manager.process.pid
        self.gui.restart_btn.invoke()
        self.wait_for(lambda: self.gui.server_manager.state == "running" and self.gui.server_manager.process.pid != first_pid)
        self.assertIn(str(port), self.gui.device_address_var.get())
        if self.gui.tray.available:
            self.wait_for(self.gui.tray.ready.is_set, timeout=10)
            self.gui.hide_to_tray()
            self.gui.root.update()
            self.assertEqual(self.gui.root.state(), "withdrawn")
            self.gui.commands.put(("show", None))
            self.wait_for(lambda: self.gui.root.state() == "normal")
        self.gui.stop_btn.invoke()
        self.wait_for(lambda: self.gui.server_manager.process is None)
        self.assertEqual(self.gui.server_manager.state, "stopped")
        with socket.socket() as listener:
            listener.bind(("0.0.0.0", port))


if __name__ == "__main__":
    unittest.main()
