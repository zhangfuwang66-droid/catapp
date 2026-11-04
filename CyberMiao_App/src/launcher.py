"""赛博小喵桌面控制台。Tk 控件只在主线程访问。"""
import multiprocessing
import os
from pathlib import Path
import sys
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
import traceback
import webbrowser

from app_paths import resource_root, user_data_dir
from config import CyberMiaoConfig
from server_manager import NetworkUtils, ServerManager


class CyberMiaoGUI:
    STATUS = {"stopped": "已停止", "starting": "正在启动…", "running": "运行中", "stopping": "正在停止…", "error": "启动或运行失败"}

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("赛博小喵 Cyber Miao")
        self.root.geometry("820x680")
        self.root.minsize(720, 580)
        self.root.protocol("WM_DELETE_WINDOW", self.exit_action)
        self.data_dir = user_data_dir()
        legacy = (Path(sys.executable).parent if getattr(sys, "frozen", False) else resource_root()) / "config" / "settings.ini"
        self.config = CyberMiaoConfig(self.data_dir / "settings.ini", legacy)
        self.server_manager = ServerManager(self.config, self.data_dir)
        self.closing = False
        self.restart_pending = False
        self.previous_state = "stopped"
        self.icon_path = resource_root() / "assets" / "icon.ico"
        if self.icon_path.is_file():
            self.root.iconbitmap(str(self.icon_path))
        self.create_widgets()
        self.server_manager.set_log_callback(self.append_log)
        self.root.report_callback_exception = self.report_error
        self.append_log("欢迎使用赛博小喵。填写 API Key 后即可启动本地服务器。")
        self.append_log(f"用户数据目录：{self.data_dir}")
        if not self.config.has_api_key():
            self.notebook.select(self.config_panel)
        self.root.after(100, self.tick)

    def create_widgets(self):
        style = ttk.Style()
        style.configure("TButton", padding=(10, 6))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"))
        header = ttk.Frame(self.root, padding=(18, 14, 18, 6))
        header.pack(fill="x")
        ttk.Label(header, text="赛博小喵", style="Title.TLabel").pack(side="left")
        self.status_var = tk.StringVar(value="已停止")
        ttk.Label(header, textvariable=self.status_var).pack(side="right")
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=14, pady=12)
        main = ttk.Frame(self.notebook, padding=12)
        self.config_panel = ttk.Frame(self.notebook, padding=18)
        about = ttk.Frame(self.notebook, padding=24)
        self.notebook.add(main, text="  主控制台  ")
        self.notebook.add(self.config_panel, text="  配置  ")
        self.notebook.add(about, text="  关于  ")
        self.address_var = tk.StringVar()
        self.update_address()
        ttk.Label(main, textvariable=self.address_var, justify="left", font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(2, 12))
        controls = ttk.Frame(main)
        controls.pack(fill="x", pady=(0, 10))
        self.start_btn = ttk.Button(controls, text="启动服务器", command=self.start_server_action)
        self.start_btn.pack(side="left", padx=(0, 6))
        self.stop_btn = ttk.Button(controls, text="停止服务器", command=self.stop_server_action, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        self.restart_btn = ttk.Button(controls, text="重启服务器", command=self.restart_action, state="disabled")
        self.restart_btn.pack(side="left", padx=6)
        self.browser_btn = ttk.Button(controls, text="打开网页", command=self.open_browser_action, state="disabled")
        self.browser_btn.pack(side="left", padx=6)
        self.tray_controls = controls
        log_frame = ttk.LabelFrame(main, text="运行日志", padding=8)
        log_frame.pack(fill="both", expand=True)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=18, font=("Consolas", 10), state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True)
        ttk.Button(main, text="打开日志目录", command=lambda: os.startfile(str(self.server_manager.log_path.parent))).pack(anchor="e", pady=(8, 0))
        panel = self.config_panel
        panel.columnconfigure(0, weight=1)
        ttk.Label(panel, text="DashScope API Key", font=("Microsoft YaHei UI", 11, "bold")).grid(row=0, column=0, sticky="w", pady=(4, 8))
        self.api_key_entry = ttk.Entry(panel, show="*", width=52)
        self.api_key_entry.grid(row=1, column=0, sticky="ew")
        self.api_key_entry.insert(0, self.config.get("API", "dashscope_api_key"))
        ttk.Button(panel, text="显示 / 隐藏", command=self.toggle_api_key_visibility).grid(row=1, column=1, padx=(8, 0))
        ttk.Button(panel, text="打开阿里云百炼控制台", command=lambda: webbrowser.open("https://bailian.console.aliyun.com/")).grid(row=2, column=0, sticky="w", pady=10)
        ttk.Label(panel, text="服务端口").grid(row=3, column=0, sticky="w", pady=(14, 6))
        self.port_entry = ttk.Entry(panel, width=16)
        self.port_entry.grid(row=4, column=0, sticky="w")
        self.port_entry.insert(0, self.config.get("Server", "port", "8081"))
        self.auto_browser_var = tk.BooleanVar(value=self.config.get("UI", "auto_open_browser", "true") == "true")
        ttk.Checkbutton(panel, text="服务器就绪后自动打开网页", variable=self.auto_browser_var).grid(row=5, column=0, sticky="w", pady=18)
        ttk.Button(panel, text="保存配置", command=self.save_config_action).grid(row=6, column=0, sticky="w")
        self.save_result = tk.StringVar()
        ttk.Label(panel, textvariable=self.save_result, wraplength=600).grid(row=7, column=0, columnspan=2, sticky="w", pady=12)
        ttk.Label(panel, text="API Key 保存在本机用户目录的 settings.ini 中，请妥善保管。\n服务运行时修改配置，需重启服务器后生效。", wraplength=600, foreground="#555555").grid(row=8, column=0, columnspan=2, sticky="w", pady=10)
        ttk.Label(about, text="Cyber Miao · ESP32 AI 桌面猫", style="Title.TLabel").pack(anchor="w", pady=12)
        ttk.Label(about, text="桌面程序负责保存配置和管理本地 FastAPI 服务。\n网页提供语音、摄像头、表情动作等控制入口。\n\n云端对话需要有效的 DashScope API Key 和网络；\n硬件功能需要 ESP32 已连接到同一局域网。", justify="left", font=("Microsoft YaHei UI", 11)).pack(anchor="w", pady=16)
        ttk.Button(about, text="打开用户数据目录", command=lambda: os.startfile(str(self.data_dir))).pack(anchor="w")

    def append_log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, message + "\n")
        if int(self.log_text.index("end-1c").split(".")[0]) > 1500:
            self.log_text.delete("1.0", "200.0")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def update_address(self):
        port = self.server_manager.active_port if self.server_manager.process else self.config.get("Server", "port", "8081")
        ip = NetworkUtils.get_local_ip()
        self.address_var.set(f"本机访问：http://127.0.0.1:{port}\nESP32 服务器地址：{ip}    端口：{port}")

    def toggle_api_key_visibility(self):
        self.api_key_entry.configure(show="" if self.api_key_entry.cget("show") else "*")

    def save_config_action(self):
        try:
            self.config.update(self.api_key_entry.get(), self.port_entry.get(), self.auto_browser_var.get())
            self.save_result.set("配置已保存。" + ("重启服务器后生效。" if self.server_manager.process else "可前往主控制台启动服务器。"))
            self.server_manager.log("配置已保存")
            self.update_address()
        except (ValueError, OSError) as exc:
            self.save_result.set(f"保存失败：{exc}")

    def start_server_action(self):
        if self.closing:
            return
        if self.server_manager.start_server():
            self.notebook.select(0)

    def stop_server_action(self):
        self.restart_pending = False
        self.server_manager.stop_server()

    def restart_action(self):
        if self.closing:
            return
        if self.server_manager.process:
            self.restart_pending = True
            self.server_manager.stop_server()
        else:
            self.start_server_action()

    def open_browser_action(self):
        if self.server_manager.server_running:
            webbrowser.open(f"http://127.0.0.1:{self.server_manager.active_port}")

    def exit_action(self):
        if self.closing:
            return
        self.closing = True
        self.restart_pending = False
        self.server_manager.stop_server()
        self.status_var.set("正在退出…")

    def tick(self):
        self.server_manager.poll()
        state = self.server_manager.state
        if self.closing and self.server_manager.process is None:
            self.server_manager.close()
            self.root.destroy()
            return
        if self.restart_pending and self.server_manager.process is None:
            self.restart_pending = False
            self.server_manager.start_server()
            state = self.server_manager.state
        if state != self.previous_state:
            self.update_address()
            if state == "running" and self.config.get("UI", "auto_open_browser", "true") == "true":
                self.open_browser_action()
            self.previous_state = state
        self.status_var.set("正在退出…" if self.closing else self.STATUS[state])
        idle = self.server_manager.process is None and not self.closing
        self.start_btn.configure(state="normal" if idle else "disabled")
        self.stop_btn.configure(state="normal" if state in {"starting", "running"} and not self.closing else "disabled")
        self.restart_btn.configure(state="normal" if state == "running" and not self.closing else "disabled")
        self.browser_btn.configure(state="normal" if state == "running" and not self.closing else "disabled")
        self.root.after(100, self.tick)

    def report_error(self, exc_type, value, tb):
        self.server_manager.log("界面异常：\n" + "".join(traceback.format_exception(exc_type, value, tb)))
        messagebox.showerror("赛博小喵", "操作失败，请查看运行日志。", parent=self.root)

    def run(self):
        self.root.mainloop()


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    try:
        CyberMiaoGUI().run()
    except Exception:
        messagebox.showerror("赛博小喵启动失败", "请检查用户数据目录的读写权限和 settings.ini 格式。\n\n" + traceback.format_exc())
        raise


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
