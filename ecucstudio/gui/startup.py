"""Make sure a main window really appears: on a screen, not minimized, in front; tell run.bat it is up."""
from __future__ import annotations

import os
import re
import sys
import tkinter as tk

_GEOM = re.compile(r"^(\d+)x(\d+)(?:([+-]-?\d+)([+-]-?\d+))?$")


def screen_area(win) -> tuple[int, int, int, int]:
    """(x, y, width, height) of the whole desktop (all monitors)."""
    if sys.platform == "win32":
        try:
            import ctypes
            m = ctypes.windll.user32.GetSystemMetrics
            w, h = m(78), m(79)                # SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN
            if w > 0 and h > 0:
                return m(76), m(77), w, h      # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN
        except (AttributeError, OSError):
            pass
    return 0, 0, win.winfo_screenwidth(), win.winfo_screenheight()


def safe_geometry(win, saved: str | None, default: str = "1500x900") -> str:
    """*saved* when the window would be visible on the current monitors, else *default* centred on the main
    screen. Positions saved while minimized (-32000) or on a monitor that is no longer connected are dropped."""
    vx, vy, vw, vh = screen_area(win)
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    m = _GEOM.match((saved or "").replace(" ", ""))
    if m and m.group(3) is not None:
        w, h = int(m.group(1)), int(m.group(2))
        # Tk offsets: "+x" from the left / top edge (x may be negative: "+-1"), "-x" from the right / bottom
        x = int(m.group(3)[1:]) if m.group(3)[0] == "+" else sw - w - int(m.group(3)[1:])
        y = int(m.group(4)[1:]) if m.group(4)[0] == "+" else sh - h - int(m.group(4)[1:])
        visible = (w >= 400 and h >= 300 and x + w > vx + 100 and x < vx + vw - 100
                   and y >= vy - 10 and y < vy + vh - 60)
        if visible:
            return f"{min(w, vw)}x{min(h, vh)}+{x}+{y}"     # "+-1" = 1 pixel left of the screen edge
    size = m if m and m.group(3) is None and int(m.group(1)) >= 400 else _GEOM.match(default)
    w, h = (int(size.group(1)), int(size.group(2))) if size else (1500, 900)
    w, h = min(w, int(sw * 0.95)), min(h, int(sh * 0.9))
    return f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}"


def bring_to_front(win):
    try:
        win.deiconify()
        win.lift()
        win.attributes("-topmost", True)
        win.after(500, lambda: _untop(win))
        win.focus_force()
    except tk.TclError:
        pass


def _untop(win):
    try:
        win.attributes("-topmost", False)
    except tk.TclError:
        pass


def announce(win, name: str):
    """Once the event loop runs: log where the window is and create the file run.bat waits for
    (environment variable ECUCSTUDIO_READY)."""
    def done():
        try:
            win.update_idletasks()
            text = (f"{name} window shown at {win.winfo_rootx()},{win.winfo_rooty()} size "
                    f"{win.winfo_width()}x{win.winfo_height()} ({win.state()})")
        except tk.TclError as exc:
            text = f"{name} window: {exc}"
        try:
            from ..__main__ import write_log
            write_log(text)
        except ImportError:
            pass
        ready = os.environ.pop("ECUCSTUDIO_READY", "")
        if ready:
            try:
                with open(ready, "w", encoding="utf-8") as fh:
                    fh.write(text + "\n")
            except OSError:
                pass
    win.after(300, done)


def show_main_window(win, name: str, saved_geometry: str | None = None, default: str = "1500x900",
                     zoomed: bool = False):
    """Place *win* on a visible screen area, bring it to the front and announce it."""
    win.geometry(safe_geometry(win, saved_geometry, default))
    if zoomed:
        try:
            win.state("zoomed")
        except tk.TclError:
            pass
    bring_to_front(win)
    announce(win, name)
