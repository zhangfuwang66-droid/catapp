"""同一套 exe 内验证桌面交互、原生推理与服务启停；不调用云端。"""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import traceback
from urllib.request import urlopen


def close_gui(gui):
    import tkinter as tk

    gui.exit_action()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            if not gui.root.winfo_exists():
                return
            gui.root.update()
        except tk.TclError:
            return
        time.sleep(0.025)
    raise TimeoutError("桌面窗口未正常退出")


def run_check(report_path):
    report_path = Path(report_path).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {"ok": False, "checks": [], "cloud_api_tested": False, "hardware_tested": False}
    output = report_path.with_suffix(".log").open("w", encoding="utf-8")
    sys.stdout = sys.stderr = output
    manager = None
    gui = None
    try:
        with tempfile.TemporaryDirectory(prefix="小喵 exe 数据 ") as directory:
            # Windows runner 的临时目录可能使用 8.3 短路径；与服务端统一为规范路径。
            data_dir = Path(directory).resolve()
            os.environ["CYBERMIAO_DATA_DIR"] = str(data_dir)
            os.environ["DASHSCOPE_API_KEY"] = "local-bundle-check-only"
            os.environ["MPLBACKEND"] = "Agg"
            from app_paths import server_root
            sys.path.insert(0, str(server_root()))
            import app as server_app
            import cv2
            import numpy as np
            from config import CyberMiaoConfig
            from server_manager import NetworkUtils, ServerManager

            assert server_app.HOST_CAMERA_AVAILABLE and server_app.MEDIAPIPE_AVAILABLE and server_app.PIL_AVAILABLE
            image = np.zeros((284, 240, 3), dtype=np.uint8)
            encoded, jpeg = cv2.imencode(".jpg", image)
            assert encoded and cv2.imdecode(jpeg, cv2.IMREAD_COLOR).shape == image.shape
            report["checks"].append("OpenCV JPEG 编解码")
            tracker = server_app._build_hand_tracker()
            assert tracker is not None, "MediaPipe 原生模型初始化失败"
            tracker.detect(image)
            if hasattr(tracker, "_landmarker"):
                tracker._landmarker.close()
            elif hasattr(tracker, "_hands"):
                tracker._hands.close()
            report["checks"].append("MediaPipe 模型初始化及空白帧推理")
            assert Path(server_app.RECORDS_DIR) == data_dir / "records", (
                f"录音目录不匹配：实际 {server_app.RECORDS_DIR}，预期 {data_dir / 'records'}"
            )
            report["checks"].append("中文及空格数据目录")
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            config = CyberMiaoConfig(data_dir / "settings.ini")
            config.update("local-bundle-check-only", str(port), False)
            manager = ServerManager(config, data_dir)

            def wait_for(condition, timeout=70):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    manager.poll()
                    if condition():
                        return
                    if manager.state == "error":
                        raise RuntimeError(manager.log_path.read_text(encoding="utf-8"))
                    time.sleep(0.05)
                raise TimeoutError("服务状态等待超时：" + manager.state)

            assert manager.start_server()
            first_pid = manager.process.pid
            wait_for(lambda: manager.server_running)
            with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as response:
                assert response.read() == b"OK"
            with urlopen(f"http://127.0.0.1:{port}/", timeout=3) as response:
                assert response.status == 200 and len(response.read()) > 1000
            report["checks"].append("独立服务进程、健康检查和网页")
            from websockets.sync.client import connect
            with connect(f"ws://127.0.0.1:{port}/ws/viewer", proxy=None):
                manager.stop_server()
                wait_for(lambda: manager.process is None, timeout=20)
            assert NetworkUtils.is_port_available(port)
            report["checks"].append("WebSocket 连接时停止并释放端口")
            config.update("local-bundle-replacement", str(port), False)
            assert manager.start_server() and manager.process.pid != first_pid
            wait_for(lambda: manager.server_running)
            with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as response:
                assert response.read() == b"OK"
            manager.stop_server()
            wait_for(lambda: manager.process is None, timeout=20)
            report["checks"].append("同端口重启及再次健康检查")
            assert manager.start_server()
            manager.stop_server()
            wait_for(lambda: manager.process is None, timeout=20)
            report["checks"].append("启动中取消")
            manager.close()
            manager = None

            # 必须在冻结后的解释器中创建实际界面，验证主题、字体和图标资源。
            from launcher import CyberMiaoGUI
            gui = CyberMiaoGUI()
            manager = gui.server_manager
            gui.root.update()
            errors = []
            gui.root.report_callback_exception = lambda *args: errors.append(str(args[1]))
            gui.nav_buttons["settings"].invoke()
            gui.api_key_entry.delete(0, "end")
            gui.api_key_entry.insert(0, "local-bundle-gui-check")
            gui.port_entry.delete(0, "end")
            gui.port_entry.insert(0, str(port))
            gui.auto_browser_var.set(False)
            gui.settings_save_btn.invoke()
            assert "配置已保存" in gui.save_result.get()
            gui.visibility_btn.invoke()
            assert not gui.api_key_entry.cget("show")
            gui.visibility_btn.invoke()
            gui.show_logs()
            gui.root.update()
            assert gui.logs_panel.winfo_ismapped()
            gui.nav_buttons["about"].invoke()
            gui.root.update()
            gui.nav_buttons["home"].invoke()
            report["checks"].append("新版界面、主题字体图标、页面切换和配置保存")

            def wait_for_gui(condition, timeout=70):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    gui.root.update()
                    assert not errors, errors
                    if condition():
                        return
                    if manager.state == "error":
                        raise RuntimeError(manager.log_path.read_text(encoding="utf-8"))
                    time.sleep(0.025)
                raise TimeoutError("桌面操作等待超时：" + manager.state)

            gui.start_btn.invoke()
            wait_for_gui(lambda: manager.server_running)
            with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as response:
                assert response.read() == b"OK"
            gui.stop_btn.invoke()
            wait_for_gui(lambda: manager.process is None, timeout=20)
            assert NetworkUtils.is_port_available(port)
            assert not errors, errors
            report["checks"].append("GUI 按钮启动、HTTP 健康检查和停止释放端口")
            close_gui(gui)
            gui = None
            manager = None
        report["ok"] = True
    except BaseException:
        report["error"] = traceback.format_exc()
    finally:
        if gui:
            close_gui(gui)
        if manager:
            manager.stop_server()
            deadline = time.monotonic() + 20
            while manager.process is not None and time.monotonic() < deadline:
                manager.poll()
                time.sleep(0.05)
            if manager.process is None:
                manager.close()
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        output.flush()
    return 0 if report["ok"] else 1
