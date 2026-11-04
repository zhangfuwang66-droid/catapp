"""独立服务进程的启动、就绪、停止与日志。UI 线程定期调用 poll。"""
import logging
from logging.handlers import RotatingFileHandler
import multiprocessing
import queue
import re
import socket
import time

from server_entry import run_server


class NetworkUtils:
    @staticmethod
    def get_local_ip():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
                connection.connect(("8.8.8.8", 80))
                return connection.getsockname()[0]
        except OSError:
            return "127.0.0.1"

    @staticmethod
    def is_port_available(port, host="0.0.0.0"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                listener.bind((host, port))
            return True
        except OSError:
            return False


class ServerManager:
    def __init__(self, config, data_dir):
        self.config = config
        self.data_dir = data_dir
        self.process = None
        self.events = None
        self.stop_event = None
        self.state = "stopped"
        self.log_callback = None
        self.started_at = 0
        self.stop_deadline = 0
        self.active_port = None
        self._secret = ""
        self._context = multiprocessing.get_context("spawn")
        log_dir = data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_dir / "CyberMiao.log"
        self._handler = RotatingFileHandler(self.log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
        self._handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))

    @property
    def server_running(self):
        return self.state == "running"

    def set_log_callback(self, callback):
        self.log_callback = callback

    def log(self, message):
        for secret in {self._secret, self.config.get("API", "dashscope_api_key")} - {""}:
            message = str(message).replace(secret, "[API Key 已隐藏]")
        message = re.sub(r"sk-[A-Za-z0-9_-]+", "[API Key 已隐藏]", str(message))
        self._handler.emit(logging.LogRecord("CyberMiao", logging.INFO, "", 0, message, (), None))
        if self.log_callback:
            self.log_callback(message)

    def start_server(self):
        self.poll()
        if self.process is not None:
            return False
        try:
            if not self.config.has_api_key():
                raise ValueError("请先在配置页填写并保存 DashScope API Key")
            options = self.config.server_options()
            if not NetworkUtils.is_port_available(options["port"], options["host"]):
                raise ValueError(f"端口 {options['port']} 已被占用，请修改端口或关闭占用程序")
            self._secret = options["api_key"]
            self.active_port = options["port"]
            self.events = self._context.Queue(maxsize=4000)
            self.stop_event = self._context.Event()
            self.process = self._context.Process(
                target=run_server, args=(self.stop_event, self.events, options, self.data_dir), daemon=True,
                name="CyberMiaoServer",
            )
            self.state = "starting"
            self.started_at = time.monotonic()
            self.process.start()
            self.log("正在启动服务器，请等待依赖加载和端口监听完成…")
            return True
        except Exception as exc:
            if self.process is not None and self.process.pid is None:
                self.process.close()
                self.process = None
            if self.events is not None and self.process is None:
                self.events.close()
                self.events = None
            self.state = "error"
            self.log(f"启动失败：{exc}")
            return False

    def stop_server(self):
        if self.process is None or self.state == "stopping":
            return
        self.stop_event.set()
        self.state = "stopping"
        self.stop_deadline = time.monotonic() + 10
        self.log("正在停止服务器并释放连接…")

    def _drain_events(self):
        if self.events is None:
            return
        for _ in range(1000):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.log(value)
            elif kind == "error":
                if self.state != "stopping":
                    self.state = "error"
                self.log("服务进程异常：\n" + value)
            elif kind == "ready" and self.state == "starting":
                self.state = "running"
                self.log(f"服务器已就绪：http://127.0.0.1:{self.active_port}")

    def poll(self):
        self._drain_events()
        process = self.process
        if process is None:
            return
        if not process.is_alive():
            process.join()
            self._drain_events()
            expected = self.state == "stopping"
            self.log("服务器已停止，端口已释放" if expected else f"服务进程已退出（代码 {process.exitcode}）")
            self.state = "stopped" if expected else "error"
            process.close()
            self.process = None
            self.events.close()
            self.events = None
            return
        now = time.monotonic()
        if self.state == "starting" and now - self.started_at > 90:
            self.log("服务启动超时，正在回收进程")
            self.stop_server()
        elif self.state == "stopping" and now > self.stop_deadline:
            self.log("优雅停止超时，正在结束本应用的服务进程")
            process.terminate()
            self.stop_deadline = now + 5

    def close(self):
        if self.process is not None:
            raise RuntimeError("请等待服务器停止后再关闭日志")
        self._handler.close()
