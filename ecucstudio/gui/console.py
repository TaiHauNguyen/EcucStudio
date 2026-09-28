"""Console view (DVCfgCmd output) and Generation Result view."""
import os
import tkinter as tk
from tkinter import ttk

from .theme import COLORS


class ConsoleView(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=2, pady=2)
        self.cancel_btn = ttk.Button(bar, text="Cancel", command=app.cancel_davinci, state="disabled")
        self.cancel_btn.pack(side="left")
        ttk.Button(bar, text="Clear", command=self.clear).pack(side="left", padx=2)
        ttk.Button(bar, text="Open report folder", command=self.open_reports).pack(side="left", padx=2)
        self.state = ttk.Label(bar, text="")
        self.state.pack(side="right")
        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True)
        self.text = tk.Text(frm, wrap="none", font=("Consolas", 9), height=10, background="#fbfbfb")
        ys = ttk.Scrollbar(frm, orient="vertical", command=self.text.yview)
        xs = ttk.Scrollbar(frm, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        frm.rowconfigure(0, weight=1)
        frm.columnconfigure(0, weight=1)
        self.text.tag_configure("ERROR", foreground=COLORS["error"])
        self.text.tag_configure("WARN", foreground=COLORS["warning"])
        self.text.tag_configure("cmd", foreground=COLORS["link"], font=("Consolas", 9, "bold"))
        self.text.tag_configure("ok", foreground=COLORS["improvement"], font=("Consolas", 9, "bold"))

    def clear(self):
        self.text.delete("1.0", "end")

    def write(self, line, tag=None):
        if tag is None:
            if " ERROR " in line or line.startswith("ERROR") or "[Error]" in line:
                tag = "ERROR"
            elif " WARN " in line or "[Warning]" in line:
                tag = "WARN"
        at_end = self.text.yview()[1] > 0.98
        self.text.insert("end", line + "\n", tag or ())
        if int(self.text.index("end-1c").split(".")[0]) > 30000:
            self.text.delete("1.0", "5000.0")
        if at_end:
            self.text.see("end")

    def open_reports(self):
        d = self.app.session.report_dir() if self.app.session.model else None
        if d and os.path.isdir(d):
            os.startfile(d)


class GenerationView(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.summary = ttk.Label(self, text="No generation executed yet.")
        self.summary.pack(fill="x", padx=4, pady=2)
        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True)
        self.tv = ttk.Treeview(frm, columns=("state", "phases", "info"))
        for c, t, w, st in (("#0", "Generator / File", 420, True), ("state", "State", 100, False),
                            ("phases", "Phases", 360, False), ("info", "Info", 300, True)):
            self.tv.heading(c, text=t)
            self.tv.column(c, width=w, stretch=st)
        ys = ttk.Scrollbar(frm, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=ys.set)
        self.tv.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        self.tv.tag_configure("FAILED", foreground=COLORS["error"])
        self.tv.tag_configure("SUCCESSFUL", foreground=COLORS["improvement"])
        self.tv.bind("<Double-1>", self._open)

    def show(self, report, rc, duration_s):
        self.tv.delete(*self.tv.get_children(""))
        ok = sum(1 for g in report.generation if g.state == "SUCCESSFUL")
        failed = [g.name for g in report.generation if g.state == "FAILED"]
        files = sum(len(g.files) for g in report.generation)
        self.summary.config(text=f"Result: {report.process_result or '-'}   exit code {rc}   "
                                 f"{ok}/{len(report.generation)} generators successful   {files} files   "
                                 f"{duration_s:.0f} s" + (f"   FAILED: {', '.join(failed)}" if failed else ""))
        for g in sorted(report.generation, key=lambda g: (g.state != "FAILED", g.name)):
            phases = ", ".join(f"{t}:{s}" for t, s in g.phases)
            info = g.error or (f"{g.generator} {g.version}" if g.generator else g.gen_type)
            gid = self.tv.insert("", "end", text=g.name, values=(g.state, phases, info), tags=(g.state,))
            for f in g.files:
                self.tv.insert(gid, "end", text=f.path, values=("", "", f.info))
            if g.state == "FAILED" and g.console:
                cid = self.tv.insert(gid, "end", text="Console output", open=False)
                for c in g.console:
                    for line in c.splitlines()[:200]:
                        self.tv.insert(cid, "end", text=line)

    def _open(self, _e=None):
        iid = self.tv.focus()
        path = self.tv.item(iid, "text")
        if path and os.path.isfile(path):
            os.startfile(path)
