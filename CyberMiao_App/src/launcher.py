# -*- coding: utf-8 -*-
"""
赛博小喵（Cyber Miao）桌面启动器
专业的 GUI 启动和配置程序
"""
import sys
import os
import json
import threading
import webbrowser
import socket
from pathlib import Path
from typing import Optional

# GUI 库
try:
    import tkinter as tk
    from tkinter import ttk, messagebox, scrolledtext
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False
    print("WARNING: tkinter not available, GUI disabled")

# 配置管理
import configparser


class CyberMiaoConfig:
    """配置管理器"""

    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.config = configparser.ConfigParser()
        self.load()

    def load(self):
        """加载配置"""
        if self.config_path.exists():
            self.config.read(self.config_path, encoding='utf-8')
        else:
            # 创建默认配置
            self.config['Server'] = {
                'host': '0.0.0.0',
                'port': '8081',
                'auto_open_browser': 'true'
            }
            self.config['API'] = {
                'dashscope_api_key': ''
            }
            self.config['UI'] = {
                'theme': 'light',
                'language': 'zh_CN'
            }
            self.save()

    def save(self):
        """保存配置"""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, 'w', encoding='utf-8') as f:
            self.config.write(f)

    def get(self, section: str, key: str, fallback: str = '') -> str:
        """获取配置值"""
        return self.config.get(section, key, fallback=fallback)

    def set(self, section: str, key: str, value: str):
        """设置配置值"""
        if not self.config.has_section(section):
            self.config.add_section(section)
        self.config.set(section, key, value)
        self.save()

    def has_api_key(self) -> bool:
        """检查是否已配置 API Key"""
        api_key = self.get('API', 'dashscope_api_key')
        return bool(api_key and api_key.strip() and api_key != 'your_api_key_here')


class NetworkUtils:
    """网络工具类"""

    @staticmethod
    def get_local_ip() -> str:
        """获取本机局域网 IP"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return '127.0.0.1'

    @staticmethod
    def is_port_available(port: int) -> bool:
        """检查端口是否可用"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                return s.connect_ex(('127.0.0.1', port)) != 0
        except Exception:
            return True


class ServerManager:
    """服务器管理器"""

    def __init__(self, config: CyberMiaoConfig):
        self.config = config
        self.server_thread: Optional[threading.Thread] = None
        self.server_running = False
        self.log_callback = None

    def set_log_callback(self, callback):
        """设置日志回调函数"""
        self.log_callback = callback

    def log(self, message: str):
        """输出日志"""
        if self.log_callback:
            self.log_callback(message)
        else:
            print(message)

    def start_server(self):
        """启动服务器"""
        if self.server_running:
            self.log("⚠️ 服务器已在运行中")
            return

        # 检查 API Key
        if not self.config.has_api_key():
            self.log("❌ 请先配置 DashScope API Key")
            return

        # 检查端口
        port = int(self.config.get('Server', 'port', '8081'))
        if not NetworkUtils.is_port_available(port):
            self.log(f"❌ 端口 {port} 已被占用")
            return

        self.server_thread = threading.Thread(target=self._run_server, daemon=True)
        self.server_thread.start()

    def _run_server(self):
        """运行服务器（在独立线程中）"""
        try:
            self.server_running = True
            self.log("🚀 正在启动赛博小喵服务器...")

            # 设置环境变量
            api_key = self.config.get('API', 'dashscope_api_key')
            os.environ['DASHSCOPE_API_KEY'] = api_key

            # 获取服务器模块路径
            if getattr(sys, 'frozen', False):
                # PyInstaller 打包后的路径
                base_path = Path(sys._MEIPASS)
            else:
                # 开发环境路径
                base_path = Path(__file__).parent.parent.parent / 'esp32_servo_control' / 'server'

            # 添加到系统路径
            sys.path.insert(0, str(base_path))

            # 导入并启动服务器
            self.log(f"📦 加载服务器模块: {base_path}")

            # 动态导入 app.py
            import importlib.util
            spec = importlib.util.spec_from_file_location("server_app", base_path / "app.py")
            server_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(server_module)

            # 获取配置
            host = self.config.get('Server', 'host', '0.0.0.0')
            port = int(self.config.get('Server', 'port', '8081'))
            local_ip = NetworkUtils.get_local_ip()

            self.log(f"✅ 服务器启动成功")
            self.log(f"🌐 本地访问: http://127.0.0.1:{port}")
            self.log(f"📱 局域网访问: http://{local_ip}:{port}")
            self.log(f"🤖 ESP32 配置 IP: {local_ip}")

            # 自动打开浏览器
            if self.config.get('UI', 'auto_open_browser', 'true') == 'true':
                threading.Timer(2.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()

            # 启动 uvicorn
            import uvicorn
            uvicorn.run(server_module.app, host=host, port=port, log_level="info")

        except Exception as e:
            self.log(f"❌ 服务器启动失败: {str(e)}")
            import traceback
            self.log(traceback.format_exc())
        finally:
            self.server_running = False

    def stop_server(self):
        """停止服务器"""
        # 注意：uvicorn 在单独线程中运行时不容易优雅停止
        # 这里只是标记状态，实际停止需要杀进程
        self.server_running = False
        self.log("⚠️ 服务器停止（需要重启程序完全停止）")


class CyberMiaoGUI:
    """赛博小喵 GUI 界面"""

    def __init__(self):
        # 确定配置文件路径
        if getattr(sys, 'frozen', False):
            # 打包后：配置文件放在 exe 同目录
            self.config_dir = Path(sys.executable).parent / 'config'
        else:
            # 开发环境
            self.config_dir = Path(__file__).parent.parent / 'config'

        self.config_path = self.config_dir / 'settings.ini'
        self.config = CyberMiaoConfig(self.config_path)
        self.server_manager = ServerManager(self.config)

        # 创建主窗口
        self.root = tk.Tk()
        self.root.title("赛博小喵 Cyber Miao - AI 桌面宠物")
        self.root.geometry("700x600")
        self.root.resizable(True, True)

        # 设置图标（如果有的话）
        try:
            icon_path = Path(__file__).parent.parent / 'assets' / 'icon.ico'
            if icon_path.exists():
                self.root.iconbitmap(str(icon_path))
        except Exception:
            pass

        self.create_widgets()
        self.server_manager.set_log_callback(self.append_log)

        # 启动时检查配置
        if not self.config.has_api_key():
            self.show_first_run_dialog()

    def create_widgets(self):
        """创建界面组件"""
        # 使用 notebook 创建标签页
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill='both', expand=True, padx=10, pady=10)

        # 标签页 1: 主控制面板
        self.create_main_panel(notebook)

        # 标签页 2: 配置
        self.create_config_panel(notebook)

        # 标签页 3: 关于
        self.create_about_panel(notebook)

    def create_main_panel(self, notebook):
        """创建主控制面板"""
        frame = ttk.Frame(notebook)
        notebook.add(frame, text='  主控制台  ')

        # 顶部信息
        info_frame = ttk.LabelFrame(frame, text="系统状态", padding=10)
        info_frame.pack(fill='x', padx=10, pady=5)

        local_ip = NetworkUtils.get_local_ip()
        port = self.config.get('Server', 'port', '8081')

        ttk.Label(info_frame, text=f"🌐 本机 IP: {local_ip}", font=('微软雅黑', 10)).pack(anchor='w', pady=2)
        ttk.Label(info_frame, text=f"🔌 服务端口: {port}", font=('微软雅黑', 10)).pack(anchor='w', pady=2)
        ttk.Label(info_frame, text=f"📱 ESP32 配置 IP: {local_ip}", font=('微软雅黑', 10, 'bold')).pack(anchor='w', pady=2)

        # 控制按钮
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill='x', padx=10, pady=10)

        self.start_btn = ttk.Button(btn_frame, text="🚀 启动服务器", command=self.start_server_action)
        self.start_btn.pack(side='left', padx=5)

        self.open_browser_btn = ttk.Button(btn_frame, text="🌐 打开网页", command=self.open_browser_action)
        self.open_browser_btn.pack(side='left', padx=5)

        ttk.Button(btn_frame, text="🔄 重启", command=self.restart_action).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="❌ 退出", command=self.exit_action).pack(side='right', padx=5)

        # 日志输出
        log_frame = ttk.LabelFrame(frame, text="运行日志", padding=10)
        log_frame.pack(fill='both', expand=True, padx=10, pady=5)

        self.log_text = scrolledtext.ScrolledText(log_frame, height=20, font=('Consolas', 9))
        self.log_text.pack(fill='both', expand=True)

        # 欢迎消息
        self.append_log("=" * 60)
        self.append_log("    赛博小喵 Cyber Miao - AI 桌面宠物")
        self.append_log("    Powered by Qwen 3.5 Omni")
        self.append_log("=" * 60)
        self.append_log("")

        if self.config.has_api_key():
            self.append_log("✅ 配置完成，点击 '启动服务器' 开始使用")
        else:
            self.append_log("⚠️ 请先在 '配置' 标签页中填写 DashScope API Key")

    def create_config_panel(self, notebook):
        """创建配置面板"""
        frame = ttk.Frame(notebook)
        notebook.add(frame, text='  配置  ')

        # API 配置
        api_frame = ttk.LabelFrame(frame, text="API 配置", padding=15)
        api_frame.pack(fill='x', padx=10, pady=10)

        ttk.Label(api_frame, text="DashScope API Key:", font=('微软雅黑', 10)).grid(row=0, column=0, sticky='w', pady=5)
        self.api_key_entry = ttk.Entry(api_frame, width=50, show='*')
        self.api_key_entry.grid(row=0, column=1, padx=10, pady=5)
        self.api_key_entry.insert(0, self.config.get('API', 'dashscope_api_key'))

        ttk.Button(api_frame, text="显示/隐藏", command=self.toggle_api_key_visibility).grid(row=0, column=2, padx=5)

        ttk.Label(api_frame, text="💡 获取地址: https://dashscope.console.aliyun.com/",
                  foreground='blue', cursor='hand2', font=('微软雅黑', 9)).grid(row=1, column=0, columnspan=3, sticky='w', pady=5)

        # 服务器配置
        server_frame = ttk.LabelFrame(frame, text="服务器配置", padding=15)
        server_frame.pack(fill='x', padx=10, pady=10)

        ttk.Label(server_frame, text="端口:", font=('微软雅黑', 10)).grid(row=0, column=0, sticky='w', pady=5)
        self.port_entry = ttk.Entry(server_frame, width=20)
        self.port_entry.grid(row=0, column=1, sticky='w', padx=10, pady=5)
        self.port_entry.insert(0, self.config.get('Server', 'port', '8081'))

        self.auto_browser_var = tk.BooleanVar(value=self.config.get('UI', 'auto_open_browser', 'true') == 'true')
        ttk.Checkbutton(server_frame, text="启动时自动打开浏览器", variable=self.auto_browser_var).grid(
            row=1, column=0, columnspan=2, sticky='w', pady=5)

        # 保存按钮
        ttk.Button(frame, text="💾 保存配置", command=self.save_config_action,
                   style='Accent.TButton').pack(pady=20)

    def create_about_panel(self, notebook):
        """创建关于面板"""
        frame = ttk.Frame(notebook)
        notebook.add(frame, text='  关于  ')

        # 居中显示
        content_frame = ttk.Frame(frame)
        content_frame.place(relx=0.5, rely=0.5, anchor='center')

        ttk.Label(content_frame, text="🐱 赛博小喵", font=('微软雅黑', 24, 'bold')).pack(pady=10)
        ttk.Label(content_frame, text="Cyber Miao - AI Desktop Pet", font=('Arial', 14)).pack(pady=5)
        ttk.Label(content_frame, text="Version 1.0.0", font=('Arial', 10)).pack(pady=10)

        ttk.Separator(content_frame, orient='horizontal').pack(fill='x', pady=15)

        features = [
            "🎤 语音对话 (Qwen 3.5 Omni)",
            "👁️ 视觉问答",
            "🌐 同声传译",
            "🎭 表情动作控制",
            "📡 WiFi 配网"
        ]

        for feature in features:
            ttk.Label(content_frame, text=feature, font=('微软雅黑', 11)).pack(anchor='w', pady=3)

        ttk.Separator(content_frame, orient='horizontal').pack(fill='x', pady=15)
        ttk.Label(content_frame, text="© 2024 Cyber Miao Project", font=('Arial', 9)).pack(pady=5)

    def append_log(self, message: str):
        """追加日志"""
        self.log_text.insert(tk.END, message + '\n')
        self.log_text.see(tk.END)

    def toggle_api_key_visibility(self):
        """切换 API Key 显示/隐藏"""
        if self.api_key_entry['show'] == '*':
            self.api_key_entry['show'] = ''
        else:
            self.api_key_entry['show'] = '*'

    def save_config_action(self):
        """保存配置"""
        try:
            # 保存 API Key
            api_key = self.api_key_entry.get().strip()
            self.config.set('API', 'dashscope_api_key', api_key)

            # 保存端口
            port = self.port_entry.get().strip()
            self.config.set('Server', 'port', port)

            # 保存自动打开浏览器
            auto_browser = 'true' if self.auto_browser_var.get() else 'false'
            self.config.set('UI', 'auto_open_browser', auto_browser)

            messagebox.showinfo("成功", "配置已保存！")
            self.append_log("✅ 配置已保存")

        except Exception as e:
            messagebox.showerror("错误", f"保存配置失败: {str(e)}")
            self.append_log(f"❌ 保存配置失败: {str(e)}")

    def start_server_action(self):
        """启动服务器"""
        self.start_btn['state'] = 'disabled'
        self.server_manager.start_server()
        self.root.after(2000, lambda: self.start_btn.configure(state='normal'))

    def open_browser_action(self):
        """打开浏览器"""
        port = self.config.get('Server', 'port', '8081')
        webbrowser.open(f"http://127.0.0.1:{port}")
        self.append_log("🌐 已在浏览器中打开")

    def restart_action(self):
        """重启程序"""
        if messagebox.askyesno("确认", "确定要重启程序吗？"):
            python = sys.executable
            os.execl(python, python, *sys.argv)

    def exit_action(self):
        """退出程序"""
        if messagebox.askyesno("确认", "确定要退出吗？"):
            self.root.quit()

    def show_first_run_dialog(self):
        """首次运行对话框"""
        response = messagebox.showinfo(
            "欢迎使用赛博小喵",
            "检测到这是首次运行！\n\n"
            "请先在 '配置' 标签页中填写 DashScope API Key\n"
            "获取地址: https://dashscope.console.aliyun.com/",
            icon='info'
        )

    def run(self):
        """运行 GUI"""
        self.root.mainloop()


def main():
    """主函数"""
    if not GUI_AVAILABLE:
        print("ERROR: tkinter not available. Cannot start GUI.")
        sys.exit(1)

    app = CyberMiaoGUI()
    app.run()


if __name__ == '__main__':
    main()



