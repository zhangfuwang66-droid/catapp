"""托盘线程只向主线程投递命令，不直接操作 Tk。"""
import threading

try:
    import pystray
    from PIL import Image
except ImportError:
    pystray = None


class SystemTray:
    def __init__(self, icon_path, commands):
        self.available = pystray is not None
        self.ready = threading.Event()
        self.icon_path = icon_path
        self.commands = commands
        self.icon = None
        self.thread = None

    def start(self):
        if not self.available:
            self.commands.put(("tray_error", "缺少 pystray，托盘功能不可用"))
            return
        with Image.open(self.icon_path) as image:
            icon_image = image.copy()

        def action(command):
            return lambda icon, item: self.commands.put((command, None))

        self.icon = pystray.Icon("CyberMiao", icon_image, "赛博小喵 Cyber Miao", pystray.Menu(
            pystray.MenuItem("显示主窗口", action("show"), default=True),
            pystray.MenuItem("启动服务器", action("start")),
            pystray.MenuItem("停止服务器", action("stop")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", action("exit")),
        ))

        def setup(icon):
            icon.visible = True
            self.ready.set()

        def run():
            try:
                self.icon.run(setup=setup)
            except Exception as exc:
                self.available = False
                self.commands.put(("tray_error", f"托盘启动失败：{exc}"))

        self.thread = threading.Thread(target=run, daemon=True, name="CyberMiaoTray")
        self.thread.start()

    def stop(self):
        if self.icon:
            self.icon.stop()
        if self.thread:
            self.thread.join(timeout=2)
