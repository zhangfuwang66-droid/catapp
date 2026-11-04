"""同一套 exe 内验证原生推理、HTTP、WebSocket 与服务重启；不调用云端。"""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import traceback
from urllib.request import urlopen


def run_check(report_path):
    report_path = Path(report_path).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {"ok": False, "checks": [], "cloud_api_tested": False, "hardware_tested": False}
    output = report_path.with_suffix(".log").open("w", encoding="utf-8")
    sys.stdout = sys.stderr = output
    manager = None
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
        report["ok"] = True
    except BaseException:
        report["error"] = traceback.format_exc()
    finally:
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
