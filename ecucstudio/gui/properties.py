"""Properties view: title line + vertical tabs Description / Status / Definition (DaVinci style)."""
import math
import tkinter as tk

from .. import arxml, units
from ..bswmd import KIND_LABEL
from ..project import annotations, definition_ref, is_auto_value, is_user_defined, raw_value
from .theme import COLORS

TABS = ("Description", "Status", "Definition")


class PropertiesPanel(tk.Frame):
    def __init__(self, master, app):
        super().__init__(master, background=COLORS["view_bg"])
        self.app = app
        self.title = tk.Label(self, text="", background=COLORS["view_bg"], foreground=COLORS["section"],
                              font=("Segoe UI", 9, "bold"), anchor="w")
        self.title.pack(fill="x", padx=6, pady=(4, 2))
        tk.Frame(self, height=1, background="#d7dde8").pack(fill="x")
        body = tk.Frame(self, background=COLORS["view_bg"])
        body.pack(fill="both", expand=True)
        self.side = tk.Frame(body, background="#eef1f6", width=92)
        self.side.pack(side="left", fill="y")
        self.side.pack_propagate(False)
        tk.Frame(body, width=1, background="#c9d1de").pack(side="left", fill="y")
        self.text = tk.Text(body, wrap="word", relief="flat", padx=8, pady=6, height=8, font=("Segoe UI", 9),
                            background=COLORS["view_bg"], borderwidth=0)
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("k", font=("Segoe UI", 9, "bold"))
        self.text.configure(tabs=("150",))
        self.text.tag_configure("dim", foreground="#777")
        self.text.configure(state="disabled")
        self.tab_btns = {}
        for t in TABS:
            b = tk.Label(self.side, text=t, anchor="w", padx=8, pady=3, background="#eef1f6", font=("Segoe UI", 9),
                         cursor="hand2")
            b.pack(fill="x")
            b.bind("<Button-1>", lambda e, t=t: self.select(t))
            self.tab_btns[t] = b
        self.content = {t: [] for t in TABS}
        self.current = "Description"
        self.select("Description")

    # ------------------------------------------------------------------ view
    def select(self, tab):
        self.current = tab
        for t, b in self.tab_btns.items():
            active = t == tab
            b.configure(background=COLORS["view_bg"] if active else "#eef1f6",
                        font=("Segoe UI", 9, "bold") if active else ("Segoe UI", 9))
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        for k, v in self.content.get(tab, []):
            if k is None:
                t.insert("end", f"{v}\n")
            else:
                t.insert("end", f"{k}\t", "k")
                t.insert("end", f"{v}\n")
        t.configure(state="disabled")

    def _set(self, title, desc, status, definition):
        self.title.configure(text=title)
        self.content = {"Description": [(None, desc or "(no description)")], "Status": status,
                        "Definition": definition}
        self.select(self.current)

    @staticmethod
    def _range(d):
        if d.min is None and d.max is None:
            return ""
        f = lambda x: "INF" if x == math.inf else "-INF" if x == -math.inf else ("?" if x is None else x)
        return f"[{f(d.min)} .. {f(d.max)}]"

    def _def_rows(self, d):
        rows = [("Short Name", d.name)]
        if d.long_name:
            rows.append(("Long Name", d.long_name))
        rows += [("Type", KIND_LABEL.get(d.kind, d.kind)), ("Definition", d.path),
                 ("Multiplicity", d.multiplicity_str())]
        if d.default is not None:
            rows.append(("Default Value", d.default))
        if d.is_param:
            r = self._range(d)
            if r:
                rows.append(("Range", r))
            if d.literals:
                rows.append(("Literals", ", ".join(d.literals)))
            if d.min_length is not None or d.max_length is not None:
                rows.append(("Length", f"{d.min_length or 0} .. {d.max_length or '*'}"))
            if d.unit or d.base_unit:
                rows.append(("Unit", f"{units.label(d.unit) or '-'} (stored as {units.label(d.base_unit) or '-'})"))
            if d.symbolic_name_value:
                rows.append(("Symbolic Name", "yes"))
        if d.is_ref:
            for i, x in enumerate(d.dest):
                rows.append(("Destination" if i == 0 else "", x))
            if d.dest_type:
                rows.append(("Destination Type", d.dest_type))
        rows.append(("Origin", d.origin or "-"))
        if d.scope:
            rows.append(("Scope", d.scope))
        for i, (c, v) in enumerate(d.config_classes):
            rows.append(("Config Classes" if i == 0 else "", f"{v}: {c}"))
        if d.post_build_changeable is not None:
            rows.append(("Post-build changeable", "yes" if d.post_build_changeable else "no"))
        if d.module is not None:
            rows.append(("BSWMD File", d.module.file))
        return rows

    # -------------------------------------------------------------- content
    def show_def(self, d):
        if d is None:
            return
        self._set(f"{d.label} ({d.path})", d.desc, [("Instances", "see editor")], self._def_rows(d))

    def show_param(self, el, p, v):
        s = self.app.session
        state, ro = s.param_state(el, p, v)
        rel = s.model.relative_path(el)
        iv = s.model.initial_values.get((rel, p.path))
        mod = s.model.module_of(el)
        pre = s.preconfig_map(mod).get(s.model.path_of(el)[len(s.model.path_of(mod)):], {}).get(p.path)
        status = [("Derived Value", iv if iv is not None else "-"),
                  ("Preconfigured", pre if pre is not None else "-"),
                  ("Recommended", "-"),
                  ("Default Value", p.default if p.default is not None else "-")]
        if v is None:
            status.append(("Instantiated", "no (defined in BSWMD, not in ECUC)"))
        else:
            status += [("Value", raw_value(v)), ("State", state or "modified"),
                       ("Changeable", f"no [{ro}]" if ro else "yes"),
                       ("User defined", "yes" if is_user_defined(v) else "no")]
            if is_auto_value(v):
                status.append(("Calculated", "yes (IS-AUTO-VALUE)"))
            anns = annotations(v)
            if anns:
                status.append(("Annotations", ", ".join(anns)))
        status.append(("Path", f"{s.model.path_of(el)}[0:{p.name}]"))
        xf = s.model.file_of(el)
        if xf:
            status.append(("File", xf.path))
        live = [r for r in getattr(self.app.current_editor(), "live", []) if (r.param or r.definition) == p.path]
        for r in live:
            status.append((f"{r.severity.label}", f"{r.rule_id}: {r.message}"))
        self._set(f"{p.label} ({p.path})", p.desc, status, self._def_rows(p))
        if live:
            self.select("Status")

    def show_container(self, el, cdef):
        s = self.app.session
        path = s.model.path_of(el)
        status = [("Path", path), ("Definition-Ref", definition_ref(el)), ("UUID", el.get("UUID") or "-")]
        anns = annotations(el)
        if anns:
            status.append(("Annotations", ", ".join(anns)))
        refs = s.model.references_to(path)
        status.append(("Referenced by", f"{len(refs)} reference(s)"))
        for r in refs[:40]:
            owner = r.getparent().getparent()
            status.append(("", f"{s.model.path_of(owner)} [{definition_ref(r).rsplit('/', 1)[-1]}]"))
        xf = s.model.file_of(el)
        if xf:
            status.append(("File", xf.path))
        name = arxml.short_name(el)
        self._set(f"{name} ({cdef.path if cdef else definition_ref(el)})", cdef.desc if cdef else "", status,
                  self._def_rows(cdef) if cdef else [("Definition", "not found")])
