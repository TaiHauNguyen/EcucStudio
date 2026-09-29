"""Console, Generation Result and Find views (DaVinci style)."""
import os
import tkinter as tk
from tkinter import ttk

from .. import arxml
from ..project import definition_ref, raw_value, value_elements
from .navigator import domain_of
from .theme import COLORS
from .widgets import FilterEntry, ToolButton, ToolSeparator


class ConsoleView(tk.Frame):
    def __init__(self, master, app):
        super().__init__(master, background=COLORS["view_bg"])
        self.app = app
        self.state = tk.Label(self, text="", background=COLORS["view_bg"], anchor="w")
        self.state.pack(fill="x", padx=4)
        frm = tk.Frame(self, background=COLORS["view_bg"])
        frm.pack(fill="both", expand=True)
        self.text = tk.Text(frm, wrap="none", font=("Consolas", 9), height=8, background="#ffffff",
                            foreground=COLORS["console_fg"], borderwidth=0)
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
        self.text.tag_configure("cmd", foreground="#000000", font=("Consolas", 9, "bold"))
        self.text.tag_configure("ok", foreground=COLORS["improvement"], font=("Consolas", 9, "bold"))
        self.running = False

    def build_view_tools(self, bar):
        ic = self.app.icons
        b = ToolButton(bar, ic.delete, self.app.cancel_davinci, "Cancel DaVinci run", bg=COLORS["tab_bar"])
        b.pack(side="left")
        b.set_enabled(self.running)
        ToolButton(bar, ic.copy, self.clear, "Clear Console", bg=COLORS["tab_bar"]).pack(side="left")
        ToolSeparator(bar).pack(side="left", fill="y", padx=3, pady=3)
        ToolButton(bar, ic.open, self.open_reports, "Open report folder", bg=COLORS["tab_bar"]).pack(side="left")

    def set_running(self, on):
        self.running = on

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


class GenerationView(tk.Frame):
    def __init__(self, master, app):
        super().__init__(master, background=COLORS["view_bg"])
        self.app = app
        self.summary = tk.Label(self, text="Result:", background=COLORS["view_bg"], anchor="w")
        self.summary.pack(fill="x", padx=4, pady=2)
        frm = tk.Frame(self, background=COLORS["view_bg"])
        frm.pack(fill="both", expand=True)
        self.tv = ttk.Treeview(frm, columns=("state", "phases", "output"))
        for c, t, w, st in (("#0", "Generator", 300, True), ("state", "State", 100, False),
                            ("phases", "Calculation / Validation / Generation", 300, False),
                            ("output", "Output", 360, True)):
            self.tv.heading(c, text=t, anchor="w")
            self.tv.column(c, width=w, stretch=st)
        ys = ttk.Scrollbar(frm, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=ys.set)
        self.tv.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        self.tv.tag_configure("FAILED", foreground=COLORS["error"])
        self.tv.tag_configure("domain", font=("Segoe UI", 9))
        self.tv.bind("<Double-1>", self._open)

    def show(self, report, rc, duration_s):
        ic = self.app.icons
        self.tv.delete(*self.tv.get_children(""))
        ok = sum(1 for g in report.generation if g.state == "SUCCESSFUL")
        failed = [g.name for g in report.generation if g.state == "FAILED"]
        files = sum(len(g.files) for g in report.generation)
        self.summary.config(text=f"Result: {report.process_result or '-'}   (exit code {rc}, {ok}/"
                                 f"{len(report.generation)} generators successful, {files} files, "
                                 f"{duration_s:.0f} s)" + (f"   Failed: {', '.join(failed)}" if failed else ""))
        domains = {}
        for g in sorted(report.generation, key=lambda g: g.name.lower()):
            d = domain_of(g.name.split("_")[0]) if g.gen_type == "GENERATOR" else "Generation Steps"
            if d not in domains:
                domains[d] = self.tv.insert("", "end", text=d, open=True, tags=("domain",))
            phases = " / ".join(f"{s.title()}" for _t, s in g.phases)
            icon = ic.error if g.state == "FAILED" else ic.generate
            gid = self.tv.insert(domains[d], "end", text=g.name, image=icon,
                                 values=(g.state, phases, g.error[:200] or (f"{g.generator} {g.version}"
                                                                         if g.generator else "")),
                                 tags=(g.state,), open=g.state == "FAILED")
            if g.files:
                fid = self.tv.insert(gid, "end", text="Generated Files", image=ic.open)
                for f in g.files:
                    self.tv.insert(fid, "end", text=os.path.basename(f.path), image=ic.properties,
                                   values=("", "", f.info), tags=(f.path,))
            if g.state == "FAILED" and g.console:
                cid = self.tv.insert(gid, "end", text="Console output", image=ic.console)
                for c in g.console:
                    for line in c.splitlines()[:200]:
                        self.tv.insert(cid, "end", text=line[:300])

    def _open(self, _e=None):
        iid = self.tv.focus()
        tags = self.tv.item(iid, "tags")
        path = tags[0] if tags else ""
        if path and os.path.isfile(path):
            os.startfile(path)


class FindView(tk.Frame):
    """Find view: search containers by name and parameters by name or value."""

    def __init__(self, master, app):
        super().__init__(master, background=COLORS["view_bg"])
        self.app = app
        bar = tk.Frame(self, background=COLORS["view_bg"])
        bar.pack(fill="x", padx=4, pady=3)
        self.entry = FilterEntry(bar, placeholder="<Find: container name, parameter name or value>")
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda e: self.find())
        self.what = tk.StringVar(value="Containers")
        ttk.Combobox(bar, textvariable=self.what, width=14, state="readonly",
                     values=("Containers", "Parameter names", "Parameter values")).pack(side="left", padx=4)
        ttk.Button(bar, text="Find", command=self.find).pack(side="left")
        self.info = tk.Label(self, text="", background=COLORS["view_bg"], anchor="w")
        self.info.pack(fill="x", padx=4)
        frm = tk.Frame(self, background=COLORS["view_bg"])
        frm.pack(fill="both", expand=True)
        self.tv = ttk.Treeview(frm, columns=("value",))
        self.tv.heading("#0", text="Element", anchor="w")
        self.tv.heading("value", text="Value", anchor="w")
        self.tv.column("#0", width=640)
        self.tv.column("value", width=300)
        ys = ttk.Scrollbar(frm, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=ys.set)
        self.tv.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        self.tv.bind("<Double-1>", self._goto)
        self.hits = {}

    def find(self):
        text = self.entry.get_text().strip().lower()
        m = self.app.session.model
        self.tv.delete(*self.tv.get_children(""))
        self.hits.clear()
        if not text or m is None:
            return
        mode = self.what.get()
        n = 0
        for path, el in m.path_index.items():
            if mode == "Containers":
                if text in path.rsplit("/", 1)[-1].lower():
                    iid = self.tv.insert("", "end", text=path, image=self.app.icons.container)
                    self.hits[iid] = (el, None)
                    n += 1
            else:
                for v in value_elements(el):
                    pname = definition_ref(v).rsplit("/", 1)[-1]
                    val = raw_value(v) or ""
                    if (mode == "Parameter names" and text in pname.lower()) or \
                            (mode == "Parameter values" and text in val.lower()):
                        iid = self.tv.insert("", "end", text=f"{path}[{pname}]", values=(val,),
                                             image=self.app.icons.param)
                        self.hits[iid] = (el, definition_ref(v))
                        n += 1
            if n >= 2000:
                break
        self.info.configure(text=f"{n} match(es)" + (" (limited to 2000)" if n >= 2000 else ""))

    def _goto(self, _e=None):
        hit = self.hits.get(self.tv.focus())
        if hit:
            el, dref = hit
            ed = self.app.goto_element(el)
            if dref and ed is not None:
                ed.select_param(def_path=dref, index=None)
