"""Colours, fonts and small programmatically drawn icons (no external image files)."""
import tkinter as tk
from tkinter import font as tkfont

COLORS = {
    "error": "#c62828",
    "warning": "#b26a00",
    "info": "#1565c0",
    "improvement": "#2e7d32",
    "notinst": "#9e9e9e",
    "readonly": "#757575",
    "user": "#6a1b9a",
    "link": "#0b57d0",
    "sel_bg": "#dbe8fb",
    "header_bg": "#eef2f7",
}

SEVERITY_COLOR = {4: COLORS["error"], 3: COLORS["error"], 2: COLORS["warning"],
                  1: COLORS["improvement"], 0: COLORS["info"]}
SEVERITY_TAG = {4: "error", 3: "error", 2: "warning", 1: "improvement", 0: "info"}


def _img(size, pixels):
    img = tk.PhotoImage(width=size, height=size)
    for color, coords in pixels.items():
        for (x0, y0, x1, y1) in coords:
            img.put(color, to=(x0, y0, x1, y1))
    return img


class Icons:
    """Created after the Tk root exists (PhotoImage needs an interpreter)."""

    def __init__(self):
        s = 16
        self.module = _img(s, {"#1f4e8c": [(2, 2, 14, 14)], "#8fb5e8": [(4, 4, 12, 12)]})
        self.container = _img(s, {"#b8860b": [(2, 4, 14, 14), (2, 3, 7, 4)], "#f5d372": [(3, 6, 13, 13)]})
        self.group = _img(s, {"#b8860b": [(1, 5, 12, 15), (4, 2, 15, 12)], "#f5d372": [(2, 6, 11, 14)],
                              "#fbe7a8": [(5, 3, 14, 5)]})
        self.choice = _img(s, {"#7b1fa2": [(2, 4, 14, 14)], "#e1bee7": [(3, 6, 13, 13)]})
        self.unknown = _img(s, {"#9e9e9e": [(2, 4, 14, 14)], "#eeeeee": [(3, 6, 13, 13)]})
        self.error = self._dot(COLORS["error"])
        self.warning = self._dot("#f9a825")
        self.info = self._dot(COLORS["info"])
        self.improvement = self._dot(COLORS["improvement"])
        self.bulb = _img(s, {"#f9a825": [(5, 2, 11, 10)], "#795548": [(6, 11, 10, 14)]})
        self.param = _img(s, {"#546e7a": [(5, 5, 11, 11)]})
        self.ref = _img(s, {"#0b57d0": [(3, 7, 13, 9), (10, 5, 12, 11)]})
        self.ok = _img(s, {"#2e7d32": [(3, 8, 6, 11), (6, 10, 8, 13), (8, 5, 13, 10)]})

    @staticmethod
    def _dot(color):
        img = tk.PhotoImage(width=16, height=16)
        for y in range(16):
            for x in range(16):
                if (x - 7.5) ** 2 + (y - 7.5) ** 2 <= 30:
                    img.put(color, (x, y))
        return img

    def severity(self, sev):
        return {4: self.error, 3: self.error, 2: self.warning, 1: self.improvement, 0: self.info}.get(int(sev))


def init_style(root):
    from tkinter import ttk
    style = ttk.Style(root)
    try:
        style.theme_use("vista")
    except tk.TclError:
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
    base = tkfont.nametofont("TkDefaultFont")
    base.configure(size=9)
    style.configure("Treeview", rowheight=20)
    style.configure("Link.TLabel", foreground=COLORS["link"], cursor="hand2")
    style.configure("Crumb.TLabel", foreground=COLORS["link"])
    style.configure("Header.TLabel", font=(base.actual("family"), 10, "bold"))
    style.configure("Status.TLabel", padding=(4, 1))
    return style
