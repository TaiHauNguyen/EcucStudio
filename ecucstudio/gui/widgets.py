"""Eclipse/DaVinci style building blocks: view stacks with tabs, tool buttons, tooltips,
scrollable forms and links."""
import tkinter as tk
from tkinter import ttk

from .theme import COLORS

FONT = ("Segoe UI", 9)
FONT_B = ("Segoe UI", 9, "bold")


class Tooltip:
    def __init__(self, widget, text):
        self.widget, self.text, self.tip = widget, text, None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _show(self, _e=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(tw, text=self.text, background="#ffffe1", relief="solid", borderwidth=1,
                 font=FONT, padx=4, pady=1).pack()

    def _hide(self, _e=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


class ToolButton(tk.Label):
    """Flat 16px icon button that highlights on hover (Eclipse toolbar item)."""

    def __init__(self, master, image, command, tooltip="", bg=None):
        bg = bg or master.cget("background")
        super().__init__(master, image=image, background=bg, borderwidth=1, relief="flat", padx=3, pady=2)
        self._bg = bg
        self.command = command
        self.enabled = True
        self.bind("<Enter>", lambda e: self.enabled and self.configure(relief="groove", background="#e3ecf9"))
        self.bind("<Leave>", lambda e: self.configure(relief="flat", background=self._bg))
        self.bind("<ButtonRelease-1>", lambda e: self.enabled and self.command and self.command())
        Tooltip(self, tooltip)

    def set_enabled(self, on):
        self.enabled = on
        self.configure(state="normal" if on else "disabled")


class ToolSeparator(tk.Frame):
    def __init__(self, master):
        super().__init__(master, width=1, background="#c5c5c5")


class LinkLabel(tk.Label):
    def __init__(self, master, text, command, bg=None, image=None, bold=False):
        bg = bg or COLORS["view_bg"]
        f = ("Segoe UI", 9, "underline bold" if bold else "underline")
        super().__init__(master, text=text, foreground=COLORS["link"], background=bg, cursor="hand2",
                         font=f, image=image, compound="left" if image else None, padx=2 if image else 0)
        self.bind("<Button-1>", lambda e: command())


class ViewStack(tk.Frame):
    """Eclipse view stack: a tab row (icon + title) with min/max buttons and one visible view."""

    def __init__(self, master, app, closable=False):
        super().__init__(master, background=COLORS["tab_border"], padx=1, pady=1)
        self.app = app
        self.closable = closable
        self.bar = tk.Frame(self, background=COLORS["tab_bar"], height=24)
        self.bar.pack(fill="x")
        self.tabs_frame = tk.Frame(self.bar, background=COLORS["tab_bar"])
        self.tabs_frame.pack(side="left", fill="y")
        self.tools = tk.Frame(self.bar, background=COLORS["tab_bar"])
        self.tools.pack(side="right")
        self.body = tk.Frame(self, background=COLORS["view_bg"])
        self.body.pack(fill="both", expand=True)
        self.views = []          # (key, title, icon, widget, tab_frame, tab_label)
        self.current = None
        self.on_change = None
        self._max_btn = tk.Label(self.tools, text="□", background=COLORS["tab_bar"], foreground="#555",
                                 font=("Segoe UI", 9), cursor="hand2", padx=4)
        self._max_btn.pack(side="right")
        self._max_btn.bind("<Button-1>", lambda e: app.toggle_maximize(self))
        Tooltip(self._max_btn, "Maximize / Restore")
        self.view_tools = tk.Frame(self.tools, background=COLORS["tab_bar"])
        self.view_tools.pack(side="right")

    def add(self, key, title, icon, widget, select=False):
        tab = tk.Frame(self.tabs_frame, background=COLORS["tab_inactive"], padx=6, pady=2,
                       highlightthickness=0)
        tab.pack(side="left", fill="y", padx=(0, 1))
        lbl = tk.Label(tab, text=" " + title, image=icon, compound="left", background=COLORS["tab_inactive"],
                       font=FONT)
        lbl.pack(side="left")
        for w in (tab, lbl):
            w.bind("<Button-1>", lambda e, k=key: self.select(k))
        close = None
        if self.closable:
            close = tk.Label(tab, text=" ×", background=COLORS["tab_inactive"], foreground="#666",
                             font=("Segoe UI", 9), cursor="hand2")
            close.pack(side="left")
            close.bind("<Button-1>", lambda e, k=key: self.close(k))
        self.views.append([key, title, icon, widget, tab, lbl, close])
        if select or self.current is None:
            self.select(key)
        return widget

    def has(self, key):
        return any(v[0] == key for v in self.views)

    def widget(self, key):
        for v in self.views:
            if v[0] == key:
                return v[3]
        return None

    def set_title(self, key, title):
        for v in self.views:
            if v[0] == key:
                v[1] = title
                v[5].configure(text=" " + title)

    def select(self, key):
        for v in self.views:
            k, _t, _i, w, tab, lbl, close = v
            active = k == key
            bg = COLORS["tab_active"] if active else COLORS["tab_inactive"]
            tab.configure(background=bg, highlightthickness=0)
            lbl.configure(background=bg, font=FONT_B if active else FONT)
            if close is not None:
                close.configure(background=bg)
            if active:
                w.pack(in_=self.body, fill="both", expand=True)
            else:
                w.pack_forget()
        self.current = key
        for c in self.view_tools.winfo_children():
            c.destroy()
        w = self.widget(key)
        if w is not None and hasattr(w, "build_view_tools"):
            w.build_view_tools(self.view_tools)
        if self.on_change:
            self.on_change(key)

    def close(self, key):
        for i, v in enumerate(self.views):
            if v[0] == key:
                w = v[3]
                if hasattr(w, "can_close") and not w.can_close():
                    return
                v[4].destroy()
                w.pack_forget()
                w.destroy()
                self.views.pop(i)
                if self.current == key:
                    self.current = None
                    if self.views:
                        self.select(self.views[max(0, i - 1)][0])
                return


class ScrollableFrame(tk.Frame):
    """A vertically scrollable white frame (form editors)."""

    def __init__(self, master, bg=None):
        bg = bg or COLORS["view_bg"]
        super().__init__(master, background=bg)
        self.canvas = tk.Canvas(self, background=bg, highlightthickness=0, borderwidth=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, background=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _wheel(self, e):
        if self.canvas.winfo_exists():
            self.canvas.yview_scroll(int(-e.delta / 120), "units")

    def clear(self):
        for w in self.inner.winfo_children():
            w.destroy()
        self.canvas.yview_moveto(0)


class FilterEntry(ttk.Entry):
    """Entry with a grey <Filter> placeholder like Eclipse filter boxes."""

    def __init__(self, master, on_change=None, placeholder="<Filter>", **kw):
        self.var = tk.StringVar()
        super().__init__(master, textvariable=self.var, **kw)
        self.placeholder = placeholder
        self._ph = False
        self._show_ph()
        self.bind("<FocusIn>", self._focus_in)
        self.bind("<FocusOut>", lambda e: self._show_ph())
        if on_change:
            self.var.trace_add("write", lambda *a: None if self._ph else on_change(self.get_text()))

    def _show_ph(self):
        if not self.var.get():
            self._ph = True
            self.var.set(self.placeholder)
            self.configure(foreground="#9a9a9a")

    def _focus_in(self, _e=None):
        if self._ph:
            self._ph = False
            self.var.set("")
            self.configure(foreground="#000000")

    def get_text(self):
        return "" if self._ph else self.var.get()

    def set_text(self, text):
        self._ph = False
        self.configure(foreground="#000000")
        self.var.set(text)
        if not text:
            self._show_ph()


def dialog_header(parent, title, text, icon=None):
    """White wizard header (title bold, description, icon right) used by DaVinci dialogs."""
    f = tk.Frame(parent, background="#ffffff")
    f.pack(fill="x")
    tk.Label(f, text=title, background="#ffffff", font=("Segoe UI", 9, "bold"), anchor="w").pack(
        fill="x", padx=10, pady=(8, 2))
    tk.Label(f, text=text, background="#ffffff", font=FONT, anchor="w", justify="left", wraplength=640).pack(
        fill="x", padx=18, pady=(0, 10))
    if icon is not None:
        tk.Label(f, image=icon, background="#ffffff").place(relx=1.0, x=-14, y=10, anchor="ne")
    tk.Frame(parent, height=1, background="#c8c8c8").pack(fill="x")
    return f
