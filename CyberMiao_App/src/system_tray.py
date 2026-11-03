# -*- coding: utf-8 -*-
"""
系统托盘支持
使用 pystray 实现系统托盘图标和菜单
"""
import sys
from pathlib import Path
from typing import Optional, Callable

try:
    import pystray
    from pystray import MenuItem as item
    from PIL import Image
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False
    print("WARNING: pystray not available, system tray disabled")


class SystemTray:
    """系统托盘管理器"""

    def __init__(self, icon_path: Optional[Path] = None):
        if not TRAY_AVAILABLE:
            self.available = False
            return

        self.available = True
        self.icon_path = icon_path
        self.tray_icon: Optional[pystray.Icon] = None

        # 回调函数
        self.on_show: Optional[Callable] = None
        self.on_exit: Optional[Callable] = None
        self.on_start_server: Optional[Callable] = None
        self.on_stop_server: Optional[Callable] = None

    def create_menu(self):
        """创建托盘菜单"""
        return pystray.Menu(
            item('显示主窗口', self._show_window),
            item('启动服务器', self._start_server),
            item('停止服务器', self._stop_server),
            pystray.Menu.SEPARATOR,
            item('退出', self._exit_app)
        )

    def _show_window(self, icon, query):
        """显示主窗口"""
        if self.on_show:
            self.on_show()

    def _start_server(self, icon, query):
        """启动服务器"""
        if self.on_start_server:
            self.on_start_server()

    def _stop_server(self, icon, query):
        """停止服务器"""
        if self.on_stop_server:
            self.on_stop_server()

    def _exit_app(self, icon, query):
        """退出应用"""
        if self.tray_icon:
            self.tray_icon.stop()
        if self.on_exit:
            self.on_exit()

    def run(self, title: str = "赛博小喵"):
        """运行托盘图标"""
        if not self.available:
            return

        # 加载图标
        if self.icon_path and self.icon_path.exists():
            icon_image = Image.open(self.icon_path)
        else:
            # 创建默认图标
            icon_image = Image.new('RGB', (64, 64), color=(41, 128, 185))

        # 创建托盘图标
        self.tray_icon = pystray.Icon(
            name="CyberMiao",
            icon=icon_image,
            title=title,
            menu=self.create_menu()
        )

        # 运行（会阻塞）
        self.tray_icon.run()

    def stop(self):
        """停止托盘图标"""
        if self.tray_icon:
            self.tray_icon.stop()
