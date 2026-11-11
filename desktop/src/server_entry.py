"""服务子进程入口。普通 import 使 PyInstaller 能分析服务器的依赖。"""
import asyncio
import io
import logging
import os
import sys
import threading
import traceback

from app_paths import server_root


class EventStream(io.TextIOBase):
    def __init__(self, events):
        self.events = events
        self.pending = ""
        self.lock = threading.RLock()

    @property
    def encoding(self):
        return "utf-8"

    def write(self, text):
        with self.lock:
            self.pending += text
            while "\n" in self.pending:
                line, self.pending = self.pending.split("\n", 1)
                self.emit(line.rstrip())
        return len(text)

    def emit(self, line):
        if line:
            try:
                self.events.put_nowait(("log", line))
            except Exception:
                pass  # UI 忙时不能让音频/网络线程被日志反压。

    def flush(self):
        with self.lock:
            self.emit(self.pending)
            self.pending = ""


def run_server(stop_event, events, options, data_dir):
    stream = EventStream(events)
    sys.stdout = sys.stderr = stream  # windowed exe 的标准流原本为 None。
    logging.basicConfig(level=logging.INFO, stream=stream, format="%(levelname)s %(message)s", force=True)
    try:
        os.environ["DASHSCOPE_API_KEY"] = options["api_key"]
        os.environ["CYBERMIAO_DATA_DIR"] = str(data_dir)
        os.environ.setdefault("MPLBACKEND", "Agg")
        sys.path.insert(0, str(server_root()))

        # 必须保留为正常导入；不要再用 exec_module 绕过依赖分析。
        from app import app, HOST_CAMERA_AVAILABLE, MEDIAPIPE_AVAILABLE, PIL_AVAILABLE, np
        import uvicorn

        missing = [name for name, available in {
            "OpenCV": HOST_CAMERA_AVAILABLE, "MediaPipe": MEDIAPIPE_AVAILABLE,
            "Pillow": PIL_AVAILABLE, "NumPy": np is not None,
        }.items() if not available]
        if missing:
            raise RuntimeError("运行依赖加载失败：" + "、".join(missing))
        print("运行依赖已加载：OpenCV、MediaPipe、Pillow、NumPy", flush=True)
        if stop_event.is_set():
            return
        server = uvicorn.Server(uvicorn.Config(
            app, host=options["host"], port=options["port"], log_config=None,
            access_log=False, loop="asyncio", http="h11", ws="websockets-sansio",
            timeout_graceful_shutdown=5, proxy_headers=False,
            ws_max_size=1024 * 1024, ws_max_queue=8,
        ))

        def monitor_parent():
            import multiprocessing
            parent = multiprocessing.parent_process()
            while not stop_event.wait(0.2):
                if parent is not None and not parent.is_alive():
                    break
            server.should_exit = True

        threading.Thread(target=monitor_parent, daemon=True).start()

        async def serve():
            task = asyncio.create_task(server.serve())
            while not task.done() and not server.started:
                await asyncio.sleep(0.05)
            if server.started and not stop_event.is_set():
                events.put(("ready", None))
            await task

        asyncio.run(serve())
    except BaseException:
        events.put(("error", traceback.format_exc()))
    finally:
        stream.flush()
