"""Validation view (DaVinci style): ID / Message tree grouped by result id, solving actions below."""
from __future__ import annotations

import tkinter as tk
from collections import defaultdict
from tkinter import simpledialog, ttk

from ..validation import Severity
from .theme import COLORS, SEVERITY_TAG
from .widgets import FilterEntry, ToolButton, ToolSeparator


class ValidationView(tk.Frame):
    def __init__(self, master, app):
        super().__init__(master, background=COLORS["view_bg"])
        self.app = app
        self.results = []
        self.rows = {}
        self.show = {k: tk.BooleanVar(value=(k != "ack")) for k in ("error", "warning", "info", "ack")}
        self.source = tk.StringVar(value="All")

        head = tk.Frame(self, background=COLORS["view_bg"])
        head.pack(fill="x", padx=4, pady=(3, 2))
        self.count = tk.Label(head, text="No validation executed", background=COLORS["view_bg"], anchor="w")
        self.count.pack(side="left")
        self.filter = FilterEntry(head, on_change=lambda t: self.after(250, self.render), width=30)
        self.filter.pack(side="right")
        cb = ttk.Combobox(head, textvariable=self.source, width=9, state="readonly",
                          values=("All", "Local", "DaVinci", "Plugins"))
        cb.pack(side="right", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self.render())
        tk.Label(head, text="Source:", background=COLORS["view_bg"]).pack(side="right")

        frm = tk.Frame(self, background=COLORS["view_bg"])
        frm.pack(fill="both", expand=True)
        self.tv = ttk.Treeview(frm, columns=("message", "source", "ack"), selectmode="extended")
        for c, t, w, st in (("#0", "ID", 170, False), ("message", "Message", 700, True),
                            ("source", "Source", 80, False), ("ack", "Acknowledgement", 160, False)):
            self.tv.heading(c, text=t, anchor="w")
            self.tv.column(c, width=w, stretch=st)
        ys = ttk.Scrollbar(frm, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=ys.set)
        self.tv.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        for t in ("error", "warning", "info", "improvement"):
            self.tv.tag_configure(t, foreground="#1b1b1b")
        self.tv.tag_configure("ack", foreground=COLORS["notinst"])
        self.tv.tag_configure("action", foreground=COLORS["link"])
        self.tv.tag_configure("ce", foreground="#555555")
        self.tv.bind("<Double-1>", self._on_double)
        self.tv.bind("<Return>", self._on_double)
        self.tv.bind("<Button-3>", self._on_menu)

    # tool items shown in the view stack's title bar (like Eclipse view toolbars)
    def build_view_tools(self, bar):
        ic = self.app.icons
        ToolButton(bar, ic.validate, self.app.validate, "Validate (F5)", bg=COLORS["tab_bar"]).pack(side="left")
        ToolButton(bar, ic.generate, self.app.davinci_validate, "On-demand validation with DaVinci (DVCfgCmd -v)",
                   bg=COLORS["tab_bar"]).pack(side="left")
        ToolButton(bar, ic.solve, self.solve_all, "Solve All", bg=COLORS["tab_bar"]).pack(side="left")
        ToolSeparator(bar).pack(side="left", fill="y", padx=3, pady=3)
        for key, icon, tip in (("error", ic.error, "Show/Hide Error Results"),
                               ("warning", ic.warning, "Show/Hide Warning Results"),
                               ("info", ic.info, "Show/Hide Info Results"),
                               ("ack", ic.ok, "Show/Hide Acknowledged Results")):
            b = ToolButton(bar, icon, lambda k=key: self._toggle(k), tip, bg=COLORS["tab_bar"])
            b.pack(side="left")
            if not self.show[key].get():
                b.configure(relief="flat", background="#d0d6e0")

    def _toggle(self, key):
        self.show[key].set(not self.show[key].get())
        self.render()
        stack = self.master
        while stack is not None and not hasattr(stack, "view_tools"):
            stack = stack.master
        if stack is not None:
            for c in stack.view_tools.winfo_children():
                c.destroy()
            self.build_view_tools(stack.view_tools)

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
        f = self.filter.get_text().strip().lower()
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
        n = sum(len(v) for v in groups.values())
        self.count.configure(text=f"{n} messages in {len(groups)} categories" if self.results or n
                             else "No validation messages")
        for rid, lst in order[:500]:
            sev = max(int(x.severity) for x in lst)
            gid = self.tv.insert("", "end", text=rid, image=self.app.icons.severity(sev),
                                 values=(f"{lst[0].title} ({len(lst)} message{'s' if len(lst) > 1 else ''})",
                                         lst[0].source, ""), tags=(SEVERITY_TAG[sev],), open=len(order) == 1)
            self.rows[gid] = ("group", lst)
            for r in lst[:2000]:
                tags = ("ack",) if r.acknowledged else (SEVERITY_TAG[int(r.severity)],)
                iid = self.tv.insert(gid, "end", text=r.severity.label, image=self.app.icons.severity(r.severity),
                                     values=(r.message.replace("\n", "  ")[:500], r.source, r.acknowledged or ""),
                                     tags=tags)
                self.rows[iid] = ("result", r)
                if r.obj:
                    cid = self.tv.insert(iid, "end", text="", values=(r.obj, "", ""), tags=("ce",),
                                         image=self.app.icons.param)
                    self.rows[cid] = ("result", r)
                for a in r.actions:
                    aid = self.tv.insert(iid, "end", text="", image=self.app.icons.bulb,
                                         values=(("(preferred) " if a.preferred else "") + a.description, "", ""),
                                         tags=("action",))
                    self.rows[aid] = ("action", r, a)
        self.app.update_counts()

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
        return list(dict.fromkeys(out))

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
        ic = self.app.icons
        if row[0] == "result":
            r = row[1]
            m.add_command(label="Show in", command=lambda: self.app.goto_result(r))
            for a in r.actions:
                m.add_command(label="Solve: " + a.description, image=ic.bulb, compound="left",
                              command=lambda a=a: self.app.apply_actions([(r, a)]))
        if row[0] in ("result", "group"):
            sel = self._selected_results()
            m.add_command(label="Solve All", image=ic.solve, compound="left", command=lambda: self.solve_all(sel))
            m.add_separator()
            m.add_command(label="Acknowledgment…", command=lambda: self._ack(sel))
            m.add_command(label="Revoke Acknowledgment", command=lambda: self._revoke(sel))
        m.add_separator()
        m.add_command(label="Copy", image=ic.copy, compound="left", command=self._copy)
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
                lines.append(self.tv.item(iid, "text") + "\t" + " ".join(map(str, self.tv.item(iid, "values"))))
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
