"""赛博小喵桌面控制台。Tk 控件只在主线程访问。"""
from datetime import datetime
import multiprocessing
import os
from pathlib import Path
import queue
import sys

# 文档解析子进程只加载解析器，避免创建桌面窗口或启动后端。
if __name__ == "__main__" and len(sys.argv) == 4 and sys.argv[1] == "--parse-document":
    from document_parser import worker
    sys.exit(worker(sys.argv[2], sys.argv[3]))

import tkinter as tk
from tkinter import messagebox
import traceback
import webbrowser

import customtkinter as ctk
from PIL import Image

from app_paths import app_version, resource_root, user_data_dir
from config import CyberMiaoConfig
from server_manager import NetworkUtils, ServerManager
from system_tray import SystemTray
from ui_theme import COLORS as C, Scrollable, Toggle, button, card, font, frame, icon, label


class CyberMiaoGUI:
    STATUS = {"stopped": "已停止", "starting": "正在启动", "running": "运行中",
              "stopping": "正在停止", "error": "运行异常"}
    STATE_COLORS = {
        "stopped": ("#f0f1f4", "#7c8492"), "starting": ("#fff6e8", "#b48028"),
        "running": (C["accent_soft"], C["accent"]), "stopping": ("#fff6e8", "#b48028"),
        "error": ("#fceeee", C["danger"]),
    }

    def __init__(self):
        ctk.set_appearance_mode("light")
        self.root = ctk.CTk(fg_color=C["background"])
        self.root.title("赛博小喵 Cyber Miao")
        self.root.geometry("1080x720")
        self.root.minsize(900, 620)
        self.root.protocol("WM_DELETE_WINDOW", self.exit_action)
        self.data_dir = user_data_dir()
        legacy = (Path(sys.executable).parent if getattr(sys, "frozen", False) else resource_root()) / "config" / "settings.ini"
        self.config = CyberMiaoConfig(self.data_dir / "settings.ini", legacy)
        self.server_manager = ServerManager(self.config, self.data_dir)
        self.closing = False
        self.restart_pending = False
        self.previous_state = None
        self.current_page = "home"
        self.icon_path = resource_root() / "assets" / "icon.ico"
        if self.icon_path.is_file():
            self.root.iconbitmap(str(self.icon_path))
        self.brand_image = ctk.CTkImage(Image.open(resource_root() / "assets" / "icon.png"), size=(34, 34))
        self.create_widgets()
        self.commands = queue.Queue()
        self.tray = SystemTray(self.icon_path, self.commands)
        try:
            self.tray.start()
        except Exception as exc:
            self.commands.put(("tray_error", str(exc)))
        self.server_manager.set_log_callback(self.append_log)
        self.root.report_callback_exception = self.report_error
        self.append_log("桌面控制台已就绪。")
        if not self.config.has_api_key():
            self.show_page("settings")
        self.root.bind("<Control-1>", lambda _: self.show_page("home"))
        self.root.bind("<Control-2>", lambda _: self.show_page("settings"))
        self.root.bind("<Control-l>", lambda _: self.show_logs())
        self.root.bind("<Control-s>", lambda _: self.save_config_action() if self.current_page == "settings" else None)
        self.root.after(100, self.tick)

    def create_widgets(self):
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(2, weight=1)
        sidebar = ctk.CTkFrame(self.root, width=208, corner_radius=0, fg_color=C["sidebar"])
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.grid_columnconfigure(0, weight=1)
        sidebar.grid_rowconfigure(4, weight=1)
        brand = frame(sidebar)
        brand.grid(row=0, column=0, sticky="ew", padx=19, pady=(26, 30))
        label(brand, image=self.brand_image, width=34).pack(side="left", padx=(0, 10))
        brand_text = frame(brand)
        brand_text.pack(side="left")
        label(brand_text, "赛博小喵", size=17, bold=True).pack(anchor="w")
        label(brand_text, "CYBER MIAO", size=10, color=C["muted"], height=16).pack(anchor="w")
        label(sidebar, "控制台", size=11, color=C["muted"]).grid(row=1, column=0, sticky="w", padx=25, pady=(0, 7))
        self.nav_buttons = {}
        for row, page, title, glyph in ((2, "home", "工作台", "home"), (3, "settings", "连接设置", "settings")):
            nav = ctk.CTkButton(sidebar, text="  " + title, image=icon(glyph), anchor="w", height=39,
                                corner_radius=8, fg_color="transparent", hover_color=C["hover"],
                                text_color=C["text"], font=font(13), command=lambda p=page: self.show_page(p))
            nav.grid(row=row, column=0, sticky="ew", padx=12, pady=3)
            self.nav_buttons[page] = nav
        bottom = frame(sidebar)
        bottom.grid(row=5, column=0, sticky="ew", padx=12, pady=(0, 18))
        ctk.CTkFrame(bottom, height=1, fg_color=C["line"]).pack(fill="x", padx=10, pady=(0, 12))
        for title, glyph, command in (("日志文件夹", "folder", self.open_logs_folder),
                                       ("收起到托盘", "tray", self.hide_to_tray),
                                       ("关于", "info", lambda: self.show_page("about"))):
            nav = ctk.CTkButton(bottom, text="  " + title, image=icon(glyph), anchor="w", height=36,
                                corner_radius=8, fg_color="transparent", hover_color=C["hover"],
                                text_color=C["muted"], text_color_disabled=C["disabled"], font=font(12), command=command)
            nav.pack(fill="x", pady=2)
            if glyph == "tray":
                self.tray_btn = nav
                nav.configure(state="disabled")
            elif glyph == "info":
                self.nav_buttons["about"] = nav
        footer = frame(bottom)
        footer.pack(fill="x", padx=13, pady=(24, 0))
        self.sidebar_state = label(footer, "● 本地服务已停止", size=11, color=C["muted"])
        self.sidebar_state.pack(anchor="w")
        label(footer, f"v{app_version()}  ·  Windows x64", size=10, color=C["muted"]).pack(anchor="w", pady=(2, 0))

        ctk.CTkFrame(self.root, width=1, corner_radius=0, fg_color=C["line"]).grid(row=0, column=1, sticky="ns")
        content = frame(self.root)
        content.grid(row=0, column=2, sticky="nsew")
        content.grid_rowconfigure(2, weight=1)
        content.grid_columnconfigure(0, weight=1)
        topbar = frame(content, height=83)
        topbar.grid(row=0, column=0, sticky="ew", padx=28)
        topbar.grid_propagate(False)
        topbar.grid_columnconfigure(0, weight=1)
        title_block = frame(topbar)
        title_block.grid(row=0, column=0, sticky="w", pady=17)
        self.page_title_var = tk.StringVar()
        self.page_subtitle_var = tk.StringVar()
        label(title_block, textvariable=self.page_title_var, size=19, bold=True).pack(anchor="w")
        label(title_block, textvariable=self.page_subtitle_var, size=11, color=C["muted"]).pack(anchor="w", pady=(3, 0))
        self.home_actions = frame(topbar)
        self.home_actions.grid(row=0, column=1, sticky="e")
        self.browser_btn = button(self.home_actions, "打开网页", self.open_browser_action, icon_name="external", width=110, state="disabled")
        self.browser_btn.pack(side="left", padx=(0, 10))
        self.start_btn = button(self.home_actions, "启动服务", self.start_server_action, icon_name="play", primary=True, width=116)
        self.start_btn.pack(side="left")
        self.settings_save_btn = button(topbar, "保存配置", self.save_config_action, icon_name="check", primary=True, width=116)
        self.about_link_btn = button(topbar, "GitHub 仓库", self.open_repository, icon_name="external", width=130)
        ctk.CTkFrame(content, height=1, corner_radius=0, fg_color=C["line"]).grid(row=1, column=0, sticky="ew")
        self.page_host = frame(content)
        self.page_host.grid(row=2, column=0, sticky="nsew")
        self.page_host.grid_rowconfigure(0, weight=1)
        self.page_host.grid_columnconfigure(0, weight=1)
        self.main_panel = frame(self.page_host)
        self.config_panel = self.scrollable(self.page_host)
        self.about_panel = self.scrollable(self.page_host)
        self.pages = {"home": self.main_panel, "settings": self.config_panel, "about": self.about_panel}
        self.local_address_var = tk.StringVar()
        self.device_address_var = tk.StringVar()
        self.port_display_var = tk.StringVar()
        self.status_var = tk.StringVar(value="已停止")
        self.create_home_page()
        self.create_settings_page()
        self.create_about_page()
        self.update_address()
        self.refresh_config_state()
        self.show_page("home")

    @staticmethod
    def scrollable(parent):
        return Scrollable(parent)

    def create_home_page(self):
        self.main_panel.grid_rowconfigure(1, weight=1)
        self.main_panel.grid_columnconfigure(0, weight=1)
        tabs_row = frame(self.main_panel)
        tabs_row.grid(row=0, column=0, sticky="ew", padx=28, pady=(22, 10))
        self.home_tabs = ctk.CTkSegmentedButton(
            tabs_row, values=["服务概览", "运行日志"], width=224, height=34, corner_radius=8,
            fg_color="#f0f1f4", selected_color="#ffffff", selected_hover_color="#ffffff",
            unselected_color="#f0f1f4", unselected_hover_color="#e7e9ef",
            text_color=C["text"], font=font(12), dynamic_resizing=False, command=self.switch_home_tab,
        )
        self.home_tabs.pack(side="left")
        self.home_tabs.set("服务概览")
        self.overview_panel = self.scrollable(self.main_panel)
        self.overview_panel.grid(row=1, column=0, sticky="nsew", padx=(19, 12), pady=(0, 16))
        body = frame(self.overview_panel)
        body.pack(fill="x", padx=(3, 9), pady=7)
        self.section_heading(body, "本地服务", "管理运行状态与小喵的连接入口。")
        server_card = card(body)
        server_card.pack(fill="x", pady=(14, 0))
        server_head = frame(server_card)
        server_head.pack(fill="x", padx=20, pady=(19, 17))
        server_icon = ctk.CTkFrame(server_head, width=42, height=42, corner_radius=10, fg_color=C["accent_soft"])
        server_icon.pack(side="left", padx=(0, 12))
        server_icon.pack_propagate(False)
        ctk.CTkLabel(server_icon, text="", image=icon("server", C["accent"], 22)).place(relx=.5, rely=.5, anchor="center")
        server_text = frame(server_head)
        server_text.pack(side="left")
        label(server_text, "小喵本地服务", size=15, bold=True).pack(anchor="w")
        label(server_text, "语音 · 视觉 · 动作控制", size=11, color=C["muted"]).pack(anchor="w", pady=(2, 0))
        self.status_badge = ctk.CTkLabel(server_head, textvariable=self.status_var, height=28, width=88,
                                       corner_radius=14, font=font(11), fg_color="#f0f1f4", text_color=C["muted"])
        self.status_badge.pack(side="right")
        self.separator(server_card)
        server_foot = frame(server_card)
        server_foot.pack(fill="x", padx=20, pady=12)
        self.port_label = label(server_foot, textvariable=self.port_display_var, size=11, color=C["muted"])
        self.port_label.pack(side="left")
        label(server_foot, "   /   ", size=11, color="#c5cad3").pack(side="left")
        self.credential_label = label(server_foot, "", size=11, color=C["muted"])
        self.credential_label.pack(side="left")
        self.stop_btn = button(server_foot, "停止", self.stop_server_action, icon_name="stop", width=74, state="disabled")
        self.stop_btn.pack(side="right")
        self.restart_btn = button(server_foot, "重启", self.restart_action, icon_name="refresh", width=74, state="disabled")
        self.restart_btn.pack(side="right", padx=(0, 8))

        self.setup_notice = ctk.CTkFrame(body, fg_color=C["accent_soft"], corner_radius=10)
        self.setup_notice.pack(fill="x", pady=(12, 0))
        notice_text = frame(self.setup_notice)
        notice_text.pack(side="left", padx=16, pady=12)
        label(notice_text, "先完成连接设置", size=12, bold=True, color=C["accent"]).pack(anchor="w")
        label(notice_text, "保存 API Key 后，即可启动本地服务。", size=11, color=C["accent"]).pack(anchor="w")
        button(self.setup_notice, "去设置", lambda: self.show_page("settings"), icon_name="arrow", width=90).pack(side="right", padx=16)

        section = frame(body)
        section.pack(fill="x", pady=(26, 12))
        label(section, "连接地址", size=14, bold=True).pack(side="left")
        label(section, "ESP32 需与电脑位于同一局域网", size=11, color=C["muted"]).pack(side="right")
        addresses = card(body)
        addresses.pack(fill="x")
        for index, (title, description, glyph, variable) in enumerate((
            ("本机网页", "在这台电脑上打开控制页面", "monitor", self.local_address_var),
            ("设备接入", "填写到 ESP32 的服务器配置", "network", self.device_address_var),
        )):
            if index:
                self.separator(addresses)
            row = frame(addresses)
            row.pack(fill="x", padx=18, pady=15)
            label(row, image=icon(glyph, C["muted"], 21), width=26).pack(side="left", padx=(0, 11))
            info = frame(row)
            info.pack(side="left")
            label(info, title, size=13, bold=True).pack(anchor="w")
            label(info, description, size=10, color=C["muted"]).pack(anchor="w")
            copy = button(row, "复制", lambda v=variable: self.copy_address(v), icon_name="copy", width=71)
            copy.pack(side="right", padx=(14, 0))
            ctk.CTkLabel(row, textvariable=variable, font=font(13, mono=True), text_color=C["text"]).pack(side="right")
        self.copy_result = tk.StringVar()
        label(body, textvariable=self.copy_result, size=11, color=C["accent"]).pack(anchor="e", pady=(3, 0))

        activity = card(body)
        activity.pack(fill="x", pady=(8, 0))
        activity_row = frame(activity)
        activity_row.pack(fill="x", padx=17, pady=(13, 4))
        label(activity_row, "最近活动", size=12, bold=True).pack(side="left")
        ctk.CTkButton(activity_row, text="查看日志", image=icon("arrow", C["muted"], 14), compound="right",
                      width=80, height=24, fg_color="transparent", hover_color=C["soft"],
                      text_color=C["muted"], font=font(11), command=self.show_logs).pack(side="right")
        self.latest_log_var = tk.StringVar(value="等待服务启动")
        self.latest_log_label = label(activity, textvariable=self.latest_log_var, size=11, color=C["muted"], justify="left")
        self.latest_log_label.pack(fill="x", padx=17, pady=(0, 14))
        activity.bind("<Configure>", lambda event: self.latest_log_label.configure(wraplength=max(200, event.width / activity._get_widget_scaling() - 38)))
        self.create_log_panel()

    @staticmethod
    def section_heading(parent, title, subtitle):
        label(parent, title, size=15, bold=True).pack(anchor="w")
        label(parent, subtitle, size=11, color=C["muted"]).pack(anchor="w", pady=(3, 0))

    @staticmethod
    def separator(parent):
        ctk.CTkFrame(parent, height=1, fg_color=C["line"], corner_radius=0).pack(fill="x", padx=1)

    def create_log_panel(self):
        self.logs_panel = card(self.main_panel)
        self.logs_panel.grid(row=1, column=0, sticky="nsew", padx=28, pady=(7, 27))
        self.logs_panel.grid_rowconfigure(2, weight=1)
        self.logs_panel.grid_columnconfigure(0, weight=1)
        header = frame(self.logs_panel)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=12)
        label(header, "运行日志", size=13, bold=True).pack(side="left")
        self.follow_logs = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(header, text="自动滚动", variable=self.follow_logs, width=90, height=24,
                        checkbox_width=16, checkbox_height=16, corner_radius=4, border_width=1,
                        fg_color=C["accent"], hover_color=C["accent_hover"], border_color="#c7ccd4",
                        text_color=C["muted"], font=font(11)).pack(side="left", padx=(16, 0))
        button(header, "日志文件夹", self.open_logs_folder, icon_name="folder", width=112).pack(side="right")
        button(header, "清空视图", self.clear_log_view, width=85).pack(side="right", padx=(0, 8))
        ctk.CTkFrame(self.logs_panel, height=1, fg_color=C["line"]).grid(row=1, column=0, sticky="ew", padx=1)
        self.log_text = ctk.CTkTextbox(
            self.logs_panel, font=font(12, mono=True), fg_color="#fafbfc", text_color="#4e596b",
            corner_radius=0, border_width=0, border_spacing=14, wrap="word", state="disabled",
            scrollbar_button_color="#d5dae2", scrollbar_button_hover_color="#b9c1cd",
        )
        self.log_text.grid(row=2, column=0, sticky="nsew", padx=1, pady=(0, 1))
        self.log_text.tag_config("time", foreground="#98a0ae")
        self.log_text.tag_config("error", foreground=C["danger"])
        self.log_text.tag_config("ready", foreground=C["accent"])
        self.logs_panel.grid_remove()

    def create_settings_page(self):
        body = frame(self.config_panel)
        body.pack(fill="x", padx=(20, 18), pady=(22, 24))
        self.section_heading(body, "模型服务", "配置小喵使用的云端语音与视觉服务。")
        credentials = card(body)
        credentials.pack(fill="x", pady=(14, 25))
        provider = frame(credentials)
        provider.pack(fill="x", padx=20, pady=(17, 16))
        badge = ctk.CTkFrame(provider, width=38, height=38, corner_radius=9, fg_color=C["accent_soft"])
        badge.pack(side="left", padx=(0, 11))
        badge.pack_propagate(False)
        label(badge, image=icon("key", C["accent"], 20), width=20).place(relx=.5, rely=.5, anchor="center")
        text = frame(provider)
        text.pack(side="left")
        label(text, "DashScope", size=14, bold=True).pack(anchor="w")
        label(text, "阿里云百炼", size=11, color=C["muted"]).pack(anchor="w")
        ctk.CTkButton(provider, text="获取 API Key", width=110, height=30,
                      fg_color="transparent", hover_color=C["soft"], text_color=C["accent"],
                      font=font(11), image=icon("external", C["accent"], 14), compound="right",
                      command=lambda: webbrowser.open("https://bailian.console.aliyun.com/")).pack(side="right")
        self.separator(credentials)
        key_form = frame(credentials)
        key_form.pack(fill="x", padx=20, pady=(16, 18))
        label(key_form, "API Key", size=12, bold=True).pack(anchor="w", pady=(0, 8))
        key_row = frame(key_form)
        key_row.pack(fill="x")
        self.api_key_entry = ctk.CTkEntry(key_row, show="•", height=40, corner_radius=8, border_width=1,
                                         fg_color="#ffffff", border_color="#dce0e6", text_color=C["text"],
                                         font=font(13), placeholder_text="输入你的 DashScope API Key")
        self.api_key_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.api_key_entry.insert(0, self.config.get("API", "dashscope_api_key"))
        self.visibility_btn = button(key_row, "显示", self.toggle_api_key_visibility, icon_name="eye", width=76)
        self.visibility_btn.pack(side="right")
        label(key_form, "凭据保存在当前电脑，请妥善保管。", size=11, color=C["muted"]).pack(anchor="w", pady=(8, 0))

        self.section_heading(body, "本地服务", "设置访问端口和启动偏好。")
        preferences = card(body)
        preferences.pack(fill="x", pady=(14, 0))
        port_row = frame(preferences)
        port_row.pack(fill="x", padx=20, pady=17)
        port_text = frame(port_row)
        port_text.pack(side="left")
        label(port_text, "服务端口", size=13, bold=True).pack(anchor="w")
        label(port_text, "浏览器和 ESP32 通过这个端口连接", size=11, color=C["muted"]).pack(anchor="w", pady=(2, 0))
        self.port_entry = ctk.CTkEntry(port_row, width=104, height=37, corner_radius=8, border_width=1,
                                      fg_color=C["surface"], border_color="#dce0e6", text_color=C["text"],
                                      font=font(13, mono=True), justify="center")
        self.port_entry.pack(side="right")
        self.port_entry.insert(0, self.config.get("Server", "port", "8081"))
        self.separator(preferences)
        browser_row = frame(preferences)
        browser_row.pack(fill="x", padx=20, pady=17)
        browser_text = frame(browser_row)
        browser_text.pack(side="left")
        label(browser_text, "自动打开网页", size=13, bold=True).pack(anchor="w")
        label(browser_text, "服务就绪后，在浏览器打开控制页面", size=11, color=C["muted"]).pack(anchor="w", pady=(2, 0))
        self.auto_browser_var = tk.BooleanVar(value=self.config.get("UI", "auto_open_browser", "true") == "true")
        self.auto_browser_switch = Toggle(browser_row, self.auto_browser_var)
        self.auto_browser_switch.pack(side="right")
        label(body, "运行时修改配置，需要重启服务后生效。", size=11, color=C["muted"]).pack(anchor="w", pady=(15, 0))
        self.save_result = tk.StringVar()
        self.save_feedback = label(body, textvariable=self.save_result, size=12, color=C["accent"], wraplength=580, justify="left")
        self.save_feedback.pack(anchor="w", pady=(12, 0))

    def create_about_page(self):
        body = frame(self.about_panel)
        body.pack(fill="x", padx=(20, 18), pady=26)
        about = card(body)
        about.pack(fill="x")
        header = frame(about)
        header.pack(fill="x", padx=24, pady=25)
        self.about_image = ctk.CTkImage(Image.open(resource_root() / "assets" / "icon.png"), size=(64, 64))
        label(header, image=self.about_image, width=64).pack(side="left", padx=(0, 18))
        title = frame(header)
        title.pack(side="left")
        label(title, "赛博小喵 Cyber Miao", size=21, bold=True).pack(anchor="w")
        label(title, "ESP32 AI 桌面猫 · 桌面控制台", size=12, color=C["muted"]).pack(anchor="w", pady=(4, 0))
        self.separator(about)
        details = frame(about)
        details.pack(fill="x", padx=24, pady=20)
        for key, value in (("软件版本", f"v{app_version()}"), ("运行平台", "Windows x64"), ("使用方式", "本地服务 · 可信局域网")):
            row = frame(details)
            row.pack(fill="x", pady=5)
            label(row, key, size=12, color=C["muted"]).pack(side="left")
            label(row, value, size=12).pack(side="right")
        description = label(body, "桌面控制台负责保存配置和管理本地服务。语音、摄像头与动作控制请进入网页使用。\n云端对话需要有效的 DashScope API Key；硬件功能需要 ESP32 已接入。",
                            size=12, color=C["muted"], wraplength=620, justify="left")
        description.pack(anchor="w", pady=(22, 18))
        button(body, "打开用户数据目录", lambda: os.startfile(str(self.data_dir)), icon_name="folder", width=170).pack(anchor="w")

    def show_page(self, page):
        self.current_page = page
        for panel in self.pages.values():
            panel.grid_remove()
        self.pages[page].grid(row=0, column=0, sticky="nsew")
        titles = {"home": ("工作台", "本地服务与设备连接"), "settings": ("连接设置", "模型凭据与启动偏好"),
                  "about": ("关于", "认识你的桌面小喵")}
        self.page_title_var.set(titles[page][0])
        self.page_subtitle_var.set(titles[page][1])
        for name, nav in self.nav_buttons.items():
            selected = name == page
            glyph = {"home": "home", "settings": "settings", "about": "info"}[name]
            nav.configure(fg_color="#e5eeec" if selected else "transparent",
                          hover_color="#dceae6" if selected else C["hover"],
                          text_color=C["accent"] if selected else C["muted"],
                          image=icon(glyph, C["accent"] if selected else C["muted"]))
        for actions in (self.home_actions, self.settings_save_btn, self.about_link_btn):
            actions.grid_remove()
        {"home": self.home_actions, "settings": self.settings_save_btn, "about": self.about_link_btn}[page].grid(row=0, column=1, sticky="e")

    def switch_home_tab(self, tab):
        if tab == "运行日志":
            self.overview_panel.grid_remove()
            self.logs_panel.grid()
        else:
            self.logs_panel.grid_remove()
            self.overview_panel.grid()

    def show_logs(self):
        self.show_page("home")
        self.home_tabs.set("运行日志")
        self.switch_home_tab("运行日志")

    def update_status_appearance(self, state):
        bg, fg = self.STATE_COLORS[state]
        self.status_badge.configure(fg_color=bg, text_color=fg)
        self.sidebar_state.configure(text=f"● 本地服务{self.STATUS[state]}", text_color=fg)
        self.start_btn.configure(text={"starting": "启动中…", "stopping": "停止中…", "running": "服务已启动"}.get(state, "启动服务"))

    def refresh_config_state(self):
        configured = self.config.has_api_key()
        self.credential_label.configure(text="API Key 已配置" if configured else "API Key 未配置",
                                         text_color=C["accent"] if configured else C["muted"])
        if configured:
            self.setup_notice.pack_forget()

    def append_log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, timestamp + "  ", "time")
        tag = "error" if any(word in message for word in ("失败", "异常", "ERROR", "Traceback")) else "ready" if "已就绪" in message else ""
        self.log_text.insert(tk.END, message + "\n", tag)
        if int(self.log_text.index("end-1c").split(".")[0]) > 1500:
            self.log_text.delete("1.0", "200.0")
        if self.follow_logs.get():
            self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")
        last_line = message.strip().splitlines()[-1] if message.strip() else ""
        self.latest_log_var.set(timestamp + "  " + last_line[:140])

    def clear_log_view(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")

    def update_address(self):
        port = self.server_manager.active_port if self.server_manager.process else self.config.get("Server", "port", "8081")
        self.local_address_var.set(f"http://127.0.0.1:{port}")
        self.device_address_var.set(f"{NetworkUtils.get_local_ip()}:{port}")
        self.port_display_var.set(f"端口 {port}")

    def copy_address(self, variable):
        self.root.clipboard_clear()
        self.root.clipboard_append(variable.get())
        self.copy_result.set("地址已复制")
        self.root.after(2500, lambda: self.copy_result.set(""))

    def toggle_api_key_visibility(self):
        visible = bool(self.api_key_entry.cget("show"))
        self.api_key_entry.configure(show="" if visible else "•")
        self.visibility_btn.configure(text="隐藏" if visible else "显示", image=icon("eye_off" if visible else "eye"))

    def save_config_action(self):
        try:
            self.config.update(self.api_key_entry.get(), self.port_entry.get(), self.auto_browser_var.get())
            self.save_feedback.configure(text_color=C["accent"])
            self.save_result.set("配置已保存。" + ("重启服务后生效。" if self.server_manager.process else "现在可以到工作台启动服务。"))
            self.server_manager.log("配置已保存")
            self.update_address()
            self.refresh_config_state()
        except (ValueError, OSError) as exc:
            self.save_feedback.configure(text_color=C["danger"])
            self.save_result.set(f"保存失败：{exc}")

    def start_server_action(self):
        if self.closing:
            return
        if not self.config.has_api_key():
            self.show_page("settings")
            self.save_feedback.configure(text_color=C["danger"])
            self.save_result.set("请先填写并保存 API Key。")
            self.api_key_entry.focus_set()
            return
        if self.server_manager.start_server():
            self.show_page("home")
            self.home_tabs.set("服务概览")
            self.switch_home_tab("服务概览")
        else:
            self.show_logs()

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

    @staticmethod
    def open_repository():
        webbrowser.open("https://github.com/zhangfuwang66-droid/catapp")

    def open_logs_folder(self):
        os.startfile(str(self.server_manager.log_path.parent))

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
            # Tk 不会自动取消所有 after 回调；销毁前清理，避免退出时调用失效控件。
            for pending in self.root.tk.call("after", "info"):
                # 回调可能由子控件注册；保留命令，让所属控件在 destroy 中回收。
                self.root.tk.call("after", "cancel", pending)
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
        self.status_var.set("正在退出…" if self.closing else "● " + self.STATUS[state])
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
