"""赛博小喵桌面控制台。Tk 控件只在主线程访问。"""
import multiprocessing
import os
import queue
from pathlib import Path
import sys
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
import traceback
import webbrowser

from app_paths import resource_root, user_data_dir
from config import CyberMiaoConfig
from server_manager import NetworkUtils, ServerManager
from system_tray import SystemTray


class CyberMiaoGUI:
    STATUS = {"stopped": "已停止", "starting": "正在启动…", "running": "运行中", "stopping": "正在停止…", "error": "启动或运行失败"}
    COLORS = {
        "background": "#eef3f4",
        "surface": "#ffffff",
        "sidebar": "#18343b",
        "sidebar_hover": "#26474e",
        "sidebar_active": "#315860",
        "text": "#193239",
        "muted": "#71858a",
        "line": "#d9e3e4",
        "accent": "#118a84",
        "accent_hover": "#0d7774",
        "coral": "#db815d",
        "log_background": "#14262d",
        "log_text": "#d4e2e4",
    }

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("赛博小喵 Cyber Miao")
        self.root.geometry("1040x760")
        self.root.minsize(900, 680)
        self.root.protocol("WM_DELETE_WINDOW", self.exit_action)
        self.root.configure(background=self.COLORS["background"])
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
        self.commands = queue.Queue()
        self.tray = SystemTray(self.icon_path, self.commands)
        try:
            self.tray.start()
        except Exception as exc:
            self.commands.put(("tray_error", str(exc)))
        self.server_manager.set_log_callback(self.append_log)
        self.root.report_callback_exception = self.report_error
        self.append_log("欢迎使用赛博小喵。填写 API Key 后即可启动本地服务器。")
        self.append_log(f"用户数据目录：{self.data_dir}")
        if not self.config.has_api_key():
            self.show_page("settings")
        self.root.after(100, self.tick)

    def create_widgets(self):
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        c = self.COLORS
        style.configure("TFrame", background=c["background"])
        style.configure("App.TFrame", background=c["background"])
        style.configure("Sidebar.TFrame", background=c["sidebar"])
        style.configure("Surface.TFrame", background=c["surface"])
        style.configure("Info.TFrame", background=c["surface"])
        style.configure("TLabel", background=c["background"], foreground=c["text"], font=("Microsoft YaHei UI", 10))
        style.configure("Muted.TLabel", background=c["background"], foreground=c["muted"], font=("Microsoft YaHei UI", 9))
        style.configure("Sidebar.TLabel", background=c["sidebar"], foreground="#f3f8f8")
        style.configure("Brand.TLabel", background=c["sidebar"], foreground="#f7fbfb", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("BrandSub.TLabel", background=c["sidebar"], foreground="#a9c1c3", font=("Segoe UI", 8, "bold"))
        style.configure("PageTitle.TLabel", background=c["background"], foreground=c["text"], font=("Microsoft YaHei UI", 19, "bold"))
        style.configure("SectionTitle.TLabel", background=c["background"], foreground=c["text"], font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("InfoTitle.TLabel", background=c["surface"], foreground=c["muted"], font=("Microsoft YaHei UI", 9))
        style.configure("InfoValue.TLabel", background=c["surface"], foreground=c["text"], font=("Segoe UI", 12, "bold"))
        style.configure("TButton", padding=(12, 9), borderwidth=0, relief="flat", font=("Microsoft YaHei UI", 9))
        style.configure("Primary.TButton", background=c["accent"], foreground="#ffffff", padding=(18, 10), font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Primary.TButton", background=[("disabled", "#bdcbcc"), ("active", c["accent_hover"])], foreground=[("disabled", "#f4f7f7")])
        style.configure("Soft.TButton", background="#e4ecec", foreground=c["text"])
        style.map("Soft.TButton", background=[("disabled", "#edf1f1"), ("active", "#d7e3e3")])
        style.configure("Danger.TButton", background="#f5e9e5", foreground="#a34f39")
        style.map("Danger.TButton", background=[("disabled", "#f3eeee"), ("active", "#efd8d1")])
        style.configure("Nav.TButton", background=c["sidebar"], foreground="#c1d1d2", padding=(16, 12), anchor="w", font=("Microsoft YaHei UI", 10))
        style.map("Nav.TButton", background=[("active", c["sidebar_hover"])], foreground=[("active", "#ffffff")])
        style.configure("NavActive.TButton", background=c["sidebar_active"], foreground="#ffffff", padding=(16, 12), anchor="w", font=("Microsoft YaHei UI", 10, "bold"))
        style.map("NavActive.TButton", background=[("active", c["sidebar_active"])])
        style.configure("Field.TEntry", padding=(10, 9), fieldbackground="#ffffff", foreground=c["text"], bordercolor=c["line"], lightcolor=c["line"], darkcolor=c["line"])
        style.configure("TCheckbutton", background=c["surface"], foreground=c["text"], font=("Microsoft YaHei UI", 9))
        style.map("TCheckbutton", background=[("active", c["surface"])])
        for name, bg, fg in (
            ("Stopped", "#e8eeee", "#62777b"),
            ("Starting", "#fff0df", "#9c642b"),
            ("Running", "#e1f3ed", "#17755e"),
            ("Stopping", "#fff0df", "#9c642b"),
            ("Error", "#f8e8e5", "#a43f35"),
        ):
            style.configure(f"Badge{name}.TFrame", background=bg, padding=(12, 7))
            style.configure(f"Badge{name}.TLabel", background=bg, foreground=fg, font=("Microsoft YaHei UI", 9, "bold"))

        shell = ttk.Frame(self.root, style="App.TFrame")
        shell.pack(fill="both", expand=True)
        sidebar = ttk.Frame(shell, style="Sidebar.TFrame", width=218, padding=(18, 22, 18, 16))
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        brand = ttk.Frame(sidebar, style="Sidebar.TFrame")
        brand.pack(fill="x", pady=(4, 27))
        try:
            logo_path = resource_root() / "assets" / "icon.png"
            self.brand_image = tk.PhotoImage(file=str(logo_path))
            factor = max(1, (self.brand_image.width() + 39) // 40)
            if factor > 1:
                self.brand_image = self.brand_image.subsample(factor, factor)
            ttk.Label(brand, image=self.brand_image, style="Sidebar.TLabel").pack(side="left", padx=(0, 10))
        except tk.TclError:
            ttk.Label(brand, text="CM", style="Brand.TLabel").pack(side="left", padx=(0, 10))
        brand_text = ttk.Frame(brand, style="Sidebar.TFrame")
        brand_text.pack(side="left", fill="y")
        ttk.Label(brand_text, text="赛博小喵", style="Brand.TLabel").pack(anchor="w")
        ttk.Label(brand_text, text="CYBER MIAO", style="BrandSub.TLabel").pack(anchor="w", pady=(2, 0))
        ttk.Separator(sidebar, orient="horizontal").pack(fill="x", pady=(0, 17))

        self.nav_buttons = {}
        for page, label in (("home", "工作台"), ("settings", "连接设置"), ("about", "关于")):
            button = ttk.Button(sidebar, text=label, style="Nav.TButton", command=lambda p=page: self.show_page(p))
            button.pack(fill="x", pady=3)
            self.nav_buttons[page] = button

        sidebar_footer = ttk.Frame(sidebar, style="Sidebar.TFrame")
        sidebar_footer.pack(side="bottom", fill="x", pady=(16, 0))
        ttk.Separator(sidebar_footer, orient="horizontal").pack(fill="x", pady=(0, 13))
        ttk.Label(sidebar_footer, text="本机服务端口", style="BrandSub.TLabel").pack(anchor="w")
        self.sidebar_port_var = tk.StringVar(value="8081")
        ttk.Label(sidebar_footer, textvariable=self.sidebar_port_var, style="Sidebar.TLabel", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(4, 0))

        content = ttk.Frame(shell, style="App.TFrame")
        content.pack(side="left", fill="both", expand=True)
        topbar = ttk.Frame(content, style="App.TFrame", padding=(28, 23, 28, 15))
        topbar.pack(fill="x")
        title_block = ttk.Frame(topbar, style="App.TFrame")
        title_block.pack(side="left", fill="x", expand=True)
        self.page_title_var = tk.StringVar(value="工作台")
        self.page_subtitle_var = tk.StringVar(value="本地服务状态与设备连接信息")
        ttk.Label(title_block, textvariable=self.page_title_var, style="PageTitle.TLabel").pack(anchor="w")
        ttk.Label(title_block, textvariable=self.page_subtitle_var, style="Muted.TLabel").pack(anchor="w", pady=(4, 0))
        self.status_var = tk.StringVar(value="已停止")
        self.status_badge = ttk.Frame(topbar, style="BadgeStopped.TFrame")
        self.status_badge.pack(side="right", padx=(12, 0))
        self.status_dot = ttk.Label(self.status_badge, text="●", style="BadgeStopped.TLabel")
        self.status_dot.pack(side="left", padx=(0, 7))
        self.status_label = ttk.Label(self.status_badge, textvariable=self.status_var, style="BadgeStopped.TLabel")
        self.status_label.pack(side="left")

        self.page_host = ttk.Frame(content, style="App.TFrame")
        self.page_host.pack(fill="both", expand=True)
        self.main_panel = ttk.Frame(self.page_host, style="App.TFrame", padding=(28, 8, 28, 24))
        self.config_panel = ttk.Frame(self.page_host, style="App.TFrame", padding=(28, 8, 28, 24))
        self.about_panel = ttk.Frame(self.page_host, style="App.TFrame", padding=(28, 8, 28, 24))
        self.pages = {"home": self.main_panel, "settings": self.config_panel, "about": self.about_panel}

        self.local_address_var = tk.StringVar()
        self.device_address_var = tk.StringVar()
        self.port_display_var = tk.StringVar()
        self.update_address()

        address_row = ttk.Frame(self.main_panel, style="App.TFrame")
        address_row.pack(fill="x", pady=(0, 20))
        for column in range(3):
            address_row.columnconfigure(column, weight=1, uniform="address")
        for column, (title, variable) in enumerate((
            ("本机访问", self.local_address_var),
            ("ESP32 接入地址", self.device_address_var),
            ("服务端口", self.port_display_var),
        )):
            tile = ttk.Frame(address_row, style="Info.TFrame", padding=(15, 13))
            tile.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 7, 0 if column == 2 else 7))
            ttk.Label(tile, text=title, style="InfoTitle.TLabel").pack(anchor="w")
            ttk.Label(tile, textvariable=variable, style="InfoValue.TLabel").pack(anchor="w", pady=(7, 0))

        action_header = ttk.Frame(self.main_panel, style="App.TFrame")
        action_header.pack(fill="x", pady=(0, 9))
        ttk.Label(action_header, text="服务控制", style="SectionTitle.TLabel").pack(side="left")
        controls = ttk.Frame(action_header, style="App.TFrame")
        controls.pack(side="right")
        self.start_btn = ttk.Button(controls, text="启动服务器", style="Primary.TButton", command=self.start_server_action)
        self.start_btn.pack(side="left", padx=(0, 7))
        self.stop_btn = ttk.Button(controls, text="停止", style="Danger.TButton", command=self.stop_server_action, state="disabled")
        self.stop_btn.pack(side="left", padx=4)
        self.restart_btn = ttk.Button(controls, text="重启", style="Soft.TButton", command=self.restart_action, state="disabled")
        self.restart_btn.pack(side="left", padx=4)
        self.browser_btn = ttk.Button(controls, text="打开网页", style="Soft.TButton", command=self.open_browser_action, state="disabled")
        self.browser_btn.pack(side="left", padx=(4, 0))
        self.tray_controls = controls
        self.tray_btn = ttk.Button(controls, text="收起到托盘", style="Soft.TButton", command=self.hide_to_tray, state="disabled")
        self.tray_btn.pack(side="left", padx=(7, 0))

        log_header = ttk.Frame(self.main_panel, style="App.TFrame")
        log_header.pack(fill="x", pady=(8, 9))
        ttk.Label(log_header, text="运行日志", style="SectionTitle.TLabel").pack(side="left")
        ttk.Label(log_header, text="实时输出", style="Muted.TLabel").pack(side="left", padx=(10, 0))
        ttk.Button(log_header, text="打开日志目录", style="Soft.TButton", command=lambda: os.startfile(str(self.server_manager.log_path.parent))).pack(side="right")
        self.log_text = scrolledtext.ScrolledText(
            self.main_panel, height=12, font=("Consolas", 9), state="disabled", wrap="word",
            background=c["log_background"], foreground=c["log_text"], insertbackground="#67d7c5",
            selectbackground="#315860", selectforeground="#ffffff", relief="flat", borderwidth=0,
            highlightthickness=0, padx=14, pady=12,
        )
        self.log_text.pack(fill="both", expand=True)

        self._create_settings_page(style)
        self._create_about_page(style)
        self.show_page("home")
        self.update_status_appearance("stopped")

    def _create_settings_page(self, style):
        c = self.COLORS
        panel = self.config_panel
        ttk.Label(panel, text="服务连接", style="SectionTitle.TLabel").pack(anchor="w", pady=(0, 5))
        ttk.Label(panel, text="设置 DashScope 凭据和本机服务选项。", style="Muted.TLabel").pack(anchor="w", pady=(0, 16))
        form = ttk.Frame(panel, style="Surface.TFrame", padding=(22, 20))
        form.pack(fill="x", anchor="n")
        form.columnconfigure(0, weight=1)

        ttk.Label(form, text="DashScope API Key", background=c["surface"], foreground=c["text"], font=("Microsoft YaHei UI", 10, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.api_key_entry = ttk.Entry(form, show="*", style="Field.TEntry")
        self.api_key_entry.grid(row=1, column=0, sticky="ew")
        self.api_key_entry.insert(0, self.config.get("API", "dashscope_api_key"))
        ttk.Button(form, text="显示 / 隐藏", style="Soft.TButton", command=self.toggle_api_key_visibility).grid(row=1, column=1, padx=(9, 0))
        ttk.Button(form, text="打开阿里云百炼控制台", style="Soft.TButton", command=lambda: webbrowser.open("https://bailian.console.aliyun.com/")).grid(row=2, column=0, sticky="w", pady=(9, 23))

        ttk.Separator(form, orient="horizontal").grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 19))
        ttk.Label(form, text="服务端口", background=c["surface"], foreground=c["text"], font=("Microsoft YaHei UI", 10, "bold")).grid(row=4, column=0, sticky="w", pady=(0, 8))
        self.port_entry = ttk.Entry(form, width=18, style="Field.TEntry")
        self.port_entry.grid(row=5, column=0, sticky="w")
        self.port_entry.insert(0, self.config.get("Server", "port", "8081"))
        self.auto_browser_var = tk.BooleanVar(value=self.config.get("UI", "auto_open_browser", "true") == "true")
        ttk.Checkbutton(form, text="服务器就绪后自动打开网页", variable=self.auto_browser_var).grid(row=6, column=0, sticky="w", pady=(18, 20))
        buttons = ttk.Frame(form, style="Surface.TFrame")
        buttons.grid(row=7, column=0, columnspan=2, sticky="ew")
        ttk.Button(buttons, text="保存配置", style="Primary.TButton", command=self.save_config_action).pack(side="left")
        self.save_result = tk.StringVar()
        ttk.Label(buttons, textvariable=self.save_result, background=c["surface"], foreground=c["accent"], wraplength=440).pack(side="left", padx=(14, 0))
        ttk.Label(
            form,
            text="API Key 保存在本机用户目录的 settings.ini 中，请妥善保管。\n服务运行时修改配置，需重启服务器后生效。",
            background=c["surface"], foreground=c["muted"], wraplength=650, justify="left",
        ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(19, 0))

    def _create_about_page(self, style):
        c = self.COLORS
        panel = self.about_panel
        content = ttk.Frame(panel, style="Surface.TFrame", padding=(24, 22))
        content.pack(fill="x", anchor="n")
        brand = ttk.Frame(content, style="Surface.TFrame")
        brand.pack(fill="x")
        try:
            logo_path = resource_root() / "assets" / "icon.png"
            self.about_image = tk.PhotoImage(file=str(logo_path))
            factor = max(1, (self.about_image.width() + 55) // 56)
            if factor > 1:
                self.about_image = self.about_image.subsample(factor, factor)
            ttk.Label(brand, image=self.about_image).pack(side="left", padx=(0, 16))
        except tk.TclError:
            pass
        text = ttk.Frame(brand, style="Surface.TFrame")
        text.pack(side="left", fill="y")
        ttk.Label(text, text="Cyber Miao", background=c["surface"], foreground=c["text"], font=("Segoe UI", 19, "bold")).pack(anchor="w")
        ttk.Label(text, text="ESP32 AI 桌面猫 · 桌面控制台", background=c["surface"], foreground=c["muted"]).pack(anchor="w", pady=(5, 0))
        ttk.Separator(content, orient="horizontal").pack(fill="x", pady=20)
        ttk.Label(
            content,
            text="桌面程序负责保存配置并管理本地 FastAPI 服务。网页提供语音、摄像头和动作控制入口。\n\n云端对话需要有效的 DashScope API Key 和网络；硬件功能需要 ESP32 已连接到同一局域网。",
            background=c["surface"], foreground=c["text"], justify="left", wraplength=700,
        ).pack(anchor="w")
        ttk.Button(content, text="打开用户数据目录", style="Soft.TButton", command=lambda: os.startfile(str(self.data_dir))).pack(anchor="w", pady=(20, 0))

    def show_page(self, page):
        for panel in self.pages.values():
            panel.pack_forget()
        self.pages[page].pack(fill="both", expand=True)
        titles = {
            "home": ("工作台", "本地服务状态与设备连接信息"),
            "settings": ("连接设置", "管理 API Key 与本机服务选项"),
            "about": ("关于赛博小喵", "Cyber Miao · ESP32 AI 桌面猫"),
        }
        self.page_title_var.set(titles[page][0])
        self.page_subtitle_var.set(titles[page][1])
        for name, button in self.nav_buttons.items():
            button.configure(style="NavActive.TButton" if name == page else "Nav.TButton")

    def update_status_appearance(self, state):
        style_name = {
            "stopped": "BadgeStopped.TFrame",
            "starting": "BadgeStarting.TFrame",
            "running": "BadgeRunning.TFrame",
            "stopping": "BadgeStopping.TFrame",
            "error": "BadgeError.TFrame",
        }.get(state, "BadgeStopped.TFrame")
        label_style = style_name.replace("TFrame", "TLabel")
        self.status_badge.configure(style=style_name)
        self.status_dot.configure(style=label_style)
        self.status_label.configure(style=label_style)

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
        self.local_address_var.set(f"http://127.0.0.1:{port}")
        self.device_address_var.set(f"{ip}:{port}")
        self.port_display_var.set(str(port))
        self.sidebar_port_var.set(str(port))

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
            self.show_page("home")

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

    def hide_to_tray(self):
        if self.tray.available and self.tray.ready.is_set() and not self.closing:
            self.root.withdraw()

    def show_window(self):
        self.root.deiconify()
        self.root.lift()

    def handle_tray_commands(self):
        actions = {"show": self.show_window, "start": self.start_server_action,
                   "stop": self.stop_server_action, "exit": self.exit_action}
        for _ in range(30):
            try:
                command, value = self.commands.get_nowait()
            except queue.Empty:
                break
            if command == "tray_error":
                self.server_manager.log(value)
                self.show_window()
            elif command in actions:
                actions[command]()

    def tick(self):
        self.handle_tray_commands()
        self.server_manager.poll()
        state = self.server_manager.state
        if self.closing and self.server_manager.process is None:
            self.tray.stop()
            self.server_manager.close()
            self.root.destroy()
            return
        if self.restart_pending and self.server_manager.process is None:
            self.restart_pending = False
            self.server_manager.start_server()
            state = self.server_manager.state
        if state != self.previous_state:
            self.update_address()
            self.update_status_appearance(state)
            if state == "running" and self.config.get("UI", "auto_open_browser", "true") == "true":
                self.open_browser_action()
            self.previous_state = state
        self.status_var.set("正在退出…" if self.closing else self.STATUS[state])
        idle = self.server_manager.process is None and not self.closing
        self.start_btn.configure(state="normal" if idle else "disabled")
        self.stop_btn.configure(state="normal" if state in {"starting", "running"} and not self.closing else "disabled")
        self.restart_btn.configure(state="normal" if state == "running" and not self.closing else "disabled")
        self.browser_btn.configure(state="normal" if state == "running" and not self.closing else "disabled")
        self.tray_btn.configure(state="normal" if self.tray.available and self.tray.ready.is_set() and not self.closing else "disabled")
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
    if len(sys.argv) == 3 and sys.argv[1] == "--check-runtime":
        from bundle_check import run_check
        sys.exit(run_check(sys.argv[2]))
    main()
