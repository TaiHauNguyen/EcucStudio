"""Validation view: results grouped by ID, related objects and solving actions."""
from __future__ import annotations

import tkinter as tk
from collections import defaultdict
from tkinter import simpledialog, ttk

from ..validation import Severity
from .theme import COLORS, SEVERITY_TAG


class ValidationView(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.results = []
        self.rows = {}

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=2, pady=2)
        ttk.Button(bar, text="Validate (F5)", command=app.validate).pack(side="left")
        ttk.Button(bar, text="Validate with DaVinci", command=app.davinci_validate).pack(side="left", padx=2)
        ttk.Button(bar, text="Solve All", command=self.solve_all).pack(side="left", padx=(8, 2))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        self.show = {}
        for key, label in (("error", "Errors"), ("warning", "Warnings"), ("info", "Infos"), ("ack", "Acknowledged")):
            v = tk.BooleanVar(value=key != "ack")
            self.show[key] = v
            ttk.Checkbutton(bar, text=label, variable=v, command=self.render).pack(side="left")
        ttk.Label(bar, text="  Source:").pack(side="left")
        self.source = tk.StringVar(value="All")
        cb = ttk.Combobox(bar, textvariable=self.source, width=10, state="readonly",
                          values=("All", "Local", "DaVinci", "Plugins"))
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self.render())
        ttk.Label(bar, text="  Filter:").pack(side="left")
        self.filter = tk.StringVar()
        ent = ttk.Entry(bar, textvariable=self.filter, width=28)
        ent.pack(side="left")
        ent.bind("<KeyRelease>", lambda e: self.after(250, self.render))
        self.count = ttk.Label(bar, text="")
        self.count.pack(side="right")

        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True)
        self.tv = ttk.Treeview(frm, columns=("sev", "source", "object", "ack"), selectmode="extended")
        for c, t, w, st in (("#0", "ID / Message", 640, True), ("sev", "Severity", 80, False),
                            ("source", "Source", 90, False), ("object", "Object", 380, True),
                            ("ack", "Acknowledgement", 160, False)):
            self.tv.heading(c, text=t)
            self.tv.column(c, width=w, stretch=st)
        ys = ttk.Scrollbar(frm, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=ys.set)
        self.tv.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        for t in ("error", "warning", "info", "improvement"):
            self.tv.tag_configure(t, foreground=COLORS[t])
        self.tv.tag_configure("ack", foreground=COLORS["notinst"])
        self.tv.tag_configure("action", foreground=COLORS["link"])
        self.tv.bind("<Double-1>", self._on_double)
        self.tv.bind("<Return>", self._on_double)
        self.tv.bind("<Button-3>", self._on_menu)

    # ----------------------------------------------------------------- data
    def set_results(self, results):
        self.results = results
        self.render()

    def _visible(self, r):
        if r.acknowledged and not self.show["ack"].get():
            return False
        sev = int(r.severity)
        if sev >= 3 and not self.show["error"].get():
            return False
        if sev == 2 and not self.show["warning"].get():
            return False
        if sev <= 1 and not self.show["info"].get():
            return False
        src = self.source.get()
        if src == "Local" and r.source != "local":
            return False
        if src == "DaVinci" and r.source != "DaVinci":
            return False
        if src == "Plugins" and not r.source.startswith("plugin"):
            return False
        f = self.filter.get().strip().lower()
        if f and f not in (r.rule_id + " " + r.message + " " + (r.obj or "")).lower():
            return False
        return True

    def render(self):
        self.tv.delete(*self.tv.get_children(""))
        self.rows.clear()
        groups = defaultdict(list)
        for r in self.results:
            if self._visible(r):
                groups[r.rule_id].append(r)
        order = sorted(groups.items(), key=lambda kv: (-max(int(x.severity) for x in kv[1]), kv[0]))
        counts = defaultdict(int)
        for r in self.results:
            if not r.acknowledged:
                counts[min(int(r.severity), 3)] += 1
        self.count.config(text=f"{counts[3]} errors, {counts[2]} warnings, {counts[0] + counts[1]} infos"
                               f"   ({len(self.results)} total)")
        for rid, lst in order[:400]:
            sev = max(int(x.severity) for x in lst)
            gid = self.tv.insert("", "end", text=f"{rid}  {lst[0].title}  ({len(lst)})",
                                 image=self.app.icons.severity(sev),
                                 values=(Severity(sev).label, lst[0].source, "", ""),
                                 tags=(SEVERITY_TAG[sev],), open=len(order) == 1)
            self.rows[gid] = ("group", lst)
            for r in lst[:2000]:
                msg = r.message.replace("\n", "  ")
                tags = ("ack",) if r.acknowledged else (SEVERITY_TAG[int(r.severity)],)
                iid = self.tv.insert(gid, "end", text=msg[:400], image=self.app.icons.severity(r.severity),
                                     values=(r.severity.label, r.source, r.obj or "", r.acknowledged or ""),
                                     tags=tags)
                self.rows[iid] = ("result", r)
                for a in r.actions:
                    aid = self.tv.insert(iid, "end", text=("★ " if a.preferred else "") + a.description,
                                         image=self.app.icons.bulb, tags=("action",))
                    self.rows[aid] = ("action", r, a)

    # --------------------------------------------------------------- actions
    def _on_double(self, _e=None):
        iid = self.tv.focus()
        row = self.rows.get(iid)
        if not row:
            return
        if row[0] == "result":
            self.app.goto_result(row[1])
        elif row[0] == "action":
            self.app.apply_actions([(row[1], row[2])])

    def solve_all(self, results=None):
        pairs = []
        for r in results or self.results:
            if r.acknowledged or r.source == "DaVinci":
                continue
            a = r.preferred_action
            if a is not None:
                pairs.append((r, a))
        if not pairs:
            self.app.status("No preferred solving actions available")
            return
        self.app.apply_actions(pairs)

    def _selected_results(self):
        out = []
        for iid in self.tv.selection():
            row = self.rows.get(iid)
            if row and row[0] == "result":
                out.append(row[1])
            elif row and row[0] == "group":
                out.extend(row[1])
        return out

    def _on_menu(self, e):
        iid = self.tv.identify_row(e.y)
        if not iid:
            return
        if iid not in self.tv.selection():
            self.tv.selection_set(iid)
        row = self.rows.get(iid)
        if not row:
            return
        m = tk.Menu(self, tearoff=False)
        if row[0] == "result":
            r = row[1]
            m.add_command(label="Show in editor", command=lambda: self.app.goto_result(r))
            for a in r.actions:
                m.add_command(label="Solve: " + a.description, command=lambda a=a: self.app.apply_actions([(r, a)]))
        if row[0] in ("result", "group"):
            sel = self._selected_results()
            m.add_command(label="Solve all selected", command=lambda: self.solve_all(sel))
            m.add_separator()
            m.add_command(label="Acknowledge…", command=lambda: self._ack(sel))
            m.add_command(label="Revoke acknowledgement", command=lambda: self._revoke(sel))
        m.add_separator()
        m.add_command(label="Copy", command=self._copy)
        m.tk_popup(e.x_root, e.y_root)

    def _ack(self, results):
        results = [r for r in results if r.severity < Severity.ERROR]
        if not results:
            self.app.status("Only warnings and infos can be acknowledged (like DaVinci)")
            return
        c = simpledialog.askstring("Acknowledge", f"Comment for {len(results)} result(s):", parent=self)
        if not c:
            return
        for r in results:
            self.app.session.acknowledge(r, c)
        self.render()

    def _revoke(self, results):
        for r in results:
            self.app.session.revoke(r)
        self.render()

    def _copy(self):
        lines = []
        for iid in self.tv.selection():
            row = self.rows.get(iid)
            if row and row[0] == "result":
                r = row[1]
                lines.append(f"{r.rule_id}\t{r.severity.label}\t{r.obj or ''}\t{r.message}")
            elif row:
                lines.append(self.tv.item(iid, "text"))
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
