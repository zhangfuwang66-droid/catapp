"""桌面界面的颜色、组件与可随 DPI 缩放的线性图标。"""
from functools import lru_cache

import customtkinter as ctk
from PIL import Image, ImageDraw


COLORS = {
    "background": "#ffffff", "sidebar": "#f5f6f8", "surface": "#ffffff",
    "soft": "#f7f8fa", "line": "#e5e7ec", "text": "#20252d", "muted": "#7c8492",
    "accent": "#158879", "accent_hover": "#107364", "accent_soft": "#e8f4f0",
    "hover": "#eceef2", "danger": "#c25050", "disabled": "#a5abb5",
}


def font(size=13, bold=False, mono=False):
    return ctk.CTkFont(family="Consolas" if mono else "Microsoft YaHei UI",
                       size=size, weight="bold" if bold else "normal")


def frame(parent, **kwargs):
    return ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0, **kwargs)


def card(parent, **kwargs):
    return ctk.CTkFrame(parent, fg_color=COLORS["surface"], corner_radius=12,
                        border_width=1, border_color=COLORS["line"], **kwargs)


def label(parent, text="", size=13, bold=False, color=None, **kwargs):
    return ctk.CTkLabel(parent, text=text, font=font(size, bold),
                        text_color=color or COLORS["text"], height=kwargs.pop("height", 22),
                        anchor=kwargs.pop("anchor", "w"), **kwargs)


def button(parent, text, command, *, icon_name=None, primary=False, width=100, **kwargs):
    color = "#ffffff" if primary else COLORS["muted"]
    return ctk.CTkButton(
        parent, text=text, command=command, width=width, height=36, corner_radius=8,
        fg_color=COLORS["accent"] if primary else COLORS["surface"],
        hover_color=COLORS["accent_hover"] if primary else COLORS["soft"],
        border_width=0 if primary else 1, border_color=COLORS["line"],
        text_color="#ffffff" if primary else COLORS["text"],
        text_color_disabled="#d4d9dc" if primary else COLORS["disabled"],
        font=font(13, primary), image=icon(icon_name, color) if icon_name else None,
        compound="left", **kwargs,
    )


def icon(name, color=None, size=18):
    # PhotoImage 只能属于创建它的 Tcl 解释器；仅缓存原始像素。
    image = _icon_pixels(name, color or COLORS["muted"])
    return ctk.CTkImage(light_image=image, dark_image=image, size=(size, size))


@lru_cache(maxsize=100)
def _icon_pixels(name, color):
    """使用统一 24px 网格绘制图标，避免依赖系统表情字体。"""
    color = color or COLORS["muted"]
    scale = 4
    image = Image.new("RGBA", (24 * scale, 24 * scale))
    draw = ImageDraw.Draw(image)

    def line(points, width=1.7):
        draw.line([(round(x * scale), round(y * scale)) for x, y in points],
                  fill=color, width=round(width * scale), joint="curve")

    def box(bounds, radius=2):
        draw.rounded_rectangle(tuple(round(v * scale) for v in bounds),
                               radius=round(radius * scale), outline=color, width=6)

    def ellipse(bounds):
        draw.ellipse(tuple(round(v * scale) for v in bounds), outline=color, width=6)

    if name == "home":
        for x, y in ((3, 3), (14, 3), (3, 14), (14, 14)):
            box((x, y, x + 7, y + 7), 1.5)
    elif name == "settings":
        for y, x in ((5, 9), (12, 16), (19, 7)):
            line([(3, y), (x - 2, y)])
            line([(x + 2, y), (21, y)])
            ellipse((x - 2, y - 2, x + 2, y + 2))
    elif name == "server":
        for y in (3, 14):
            box((3, y, 21, y + 7), 2)
            line([(7, y + 3.5), (8, y + 3.5)], 2)
            line([(15, y + 3.5), (18, y + 3.5)])
    elif name == "play":
        line([(8, 4), (19, 12), (8, 20), (8, 4)])
    elif name == "stop":
        box((6, 6, 18, 18), 1)
    elif name == "refresh":
        draw.arc((4 * scale, 4 * scale, 20 * scale, 20 * scale), 40, 325, fill=color, width=6)
        line([(20, 4), (20, 10), (14, 10)])
    elif name in {"external", "arrow"}:
        if name == "external":
            line([(11, 5), (5, 5), (5, 20), (19, 20), (19, 14)])
            line([(14, 3), (21, 3), (21, 10)])
            line([(11, 13), (21, 3)])
        else:
            line([(4, 12), (20, 12)])
            line([(14, 6), (20, 12), (14, 18)])
    elif name == "monitor":
        box((3, 4, 21, 17), 2)
        line([(12, 17), (12, 21)])
        line([(8, 21), (16, 21)])
    elif name == "network":
        box((9, 2, 15, 8), 1)
        line([(12, 8), (12, 12), (4, 12), (4, 16)])
        line([(12, 12), (20, 12), (20, 16)])
        box((1, 16, 7, 22), 1)
        box((17, 16, 23, 22), 1)
    elif name == "folder":
        line([(3, 8), (3, 5), (9, 5), (12, 8), (21, 8), (21, 20), (3, 20), (3, 8), (21, 8)])
    elif name == "copy":
        box((8, 8, 21, 21), 2)
        line([(16, 5), (16, 3), (3, 3), (3, 16), (5, 16)])
    elif name == "check":
        line([(5, 12), (10, 17), (20, 7)], 2)
    elif name in {"eye", "eye_off"}:
        line([(2, 12), (6, 7), (12, 5), (18, 7), (22, 12), (18, 17), (12, 19), (6, 17), (2, 12)])
        ellipse((9, 9, 15, 15))
        if name == "eye_off":
            line([(3, 3), (21, 21)], 2)
    elif name == "info":
        ellipse((3, 3, 21, 21))
        line([(12, 11), (12, 17)])
        line([(12, 7), (12, 7.5)], 2)
    elif name == "log":
        box((4, 3, 20, 21), 2)
        for y in (8, 12, 16):
            line([(8, y), (16, y)])
    elif name == "tray":
        line([(3, 15), (3, 21), (21, 21), (21, 15)])
        line([(12, 3), (12, 15), (7, 10)])
        line([(12, 15), (17, 10)])
    elif name == "key":
        ellipse((2, 2, 13, 13))
        line([(12, 12), (21, 21), (23, 19)])
        line([(17, 17), (19, 15)])
    else:
        raise ValueError(f"未知图标：{name}")
    return image


class Toggle(ctk.CTkButton):
    """带内嵌旋钮的开关，随变量和 DPI 更新，并支持键盘操作。"""

    def __init__(self, parent, variable):
        self.variable = variable
        self.images = {}
        for enabled in (False, True):
            image = Image.new("RGBA", (176, 96))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((0, 0, 175, 95), radius=48,
                                   fill=COLORS["accent"] if enabled else "#c5ccd6")
            left = 92 if enabled else 12
            draw.ellipse((left, 12, left + 71, 83), fill="#ffffff")
            self.images[enabled] = ctk.CTkImage(image, image, size=(44, 24))
        super().__init__(parent, text="", image=self.images[bool(variable.get())],
                         width=48, height=30, corner_radius=12, fg_color="transparent",
                         hover_color=COLORS["soft"], command=self.toggle)
        self._trace = variable.trace_add("write", self._sync)
        self.bind("<space>", lambda _: self.toggle())
        self.bind("<Return>", lambda _: self.toggle())
        self._canvas.configure(takefocus=True)
        self.bind("<FocusIn>", lambda _: self.configure(fg_color=COLORS["accent_soft"]))
        self.bind("<FocusOut>", lambda _: self.configure(fg_color="transparent"))

    def _sync(self, *_):
        self.configure(image=self.images[bool(self.variable.get())])

    def toggle(self):
        self.variable.set(not self.variable.get())

    def destroy(self):
        self.variable.trace_remove("write", self._trace)
        super().destroy()


class Scrollable(ctk.CTkScrollableFrame):
    """内容溢出时显示滚动条，保留槽位以免页面左右跳动。"""

    def __init__(self, parent):
        super().__init__(parent, corner_radius=0, fg_color=COLORS["background"])
        self._overflow = None
        self._parent_canvas.configure(yscrollcommand=self._set_scrollbar)

    def _set_scrollbar(self, first, last):
        self._scrollbar.set(first, last)
        overflow = float(first) > 0 or float(last) < 1
        if overflow != self._overflow:
            self._overflow = overflow
            self.configure(scrollbar_button_color=COLORS["line"] if overflow else COLORS["background"],
                           scrollbar_button_hover_color="#ccd1da" if overflow else COLORS["background"])
