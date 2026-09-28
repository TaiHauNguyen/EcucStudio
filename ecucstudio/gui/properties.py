"""Properties view: Description / Definition / Status tabs (like DaVinci's Properties view)."""
import math
import tkinter as tk
from tkinter import ttk

from .. import arxml, units
from ..bswmd import KIND_LABEL
from ..project import annotations, definition_ref, is_auto_value, raw_value


class PropertiesPanel(ttk.Notebook):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.texts = {}
        for name in ("Description", "Definition", "Status"):
            f = ttk.Frame(self)
            t = tk.Text(f, wrap="word", relief="flat", padx=6, pady=4, height=10, font=("Segoe UI", 9))
            ys = ttk.Scrollbar(f, orient="vertical", command=t.yview)
            t.configure(yscrollcommand=ys.set, state="disabled")
            t.pack(side="left", fill="both", expand=True)
            ys.pack(side="right", fill="y")
            t.tag_configure("k", font=("Segoe UI", 9, "bold"))
            t.tag_configure("h", font=("Segoe UI", 10, "bold"))
            self.add(f, text=name)
            self.texts[name] = t

    def _write(self, name, rows=None, text=None):
        t = self.texts[name]
        t.configure(state="normal")
        t.delete("1.0", "end")
        if text is not None:
            t.insert("end", text)
        for k, v in rows or []:
            if k is None:
                t.insert("end", f"{v}\n", "h")
                continue
            t.insert("end", f"{k}: ", "k")
            t.insert("end", f"{v}\n")
        t.configure(state="disabled")

    @staticmethod
    def _range(d):
        if d.min is None and d.max is None:
            return ""
        f = lambda x: "INF" if x == math.inf else "-INF" if x == -math.inf else ("?" if x is None else x)
        return f"[{f(d.min)} .. {f(d.max)}]"

    def _def_rows(self, d):
        rows = [(None, d.name), ("Type", KIND_LABEL.get(d.kind, d.kind)), ("Definition", d.path),
                ("Multiplicity", d.multiplicity_str())]
        if d.default is not None:
            rows.append(("Default", d.default))
        if d.is_param:
            r = self._range(d)
            if r:
                rows.append(("Range", r))
            if d.literals:
                rows.append(("Literals", ", ".join(d.literals)))
            if d.min_length is not None or d.max_length is not None:
                rows.append(("Length", f"{d.min_length or 0} .. {d.max_length or '*'}"))
            if d.unit or d.base_unit:
                rows.append(("Unit", f"shown in {units.label(d.unit) or '-'}, stored in "
                                     f"{units.label(d.base_unit) or '-'}"))
            if d.symbolic_name_value:
                rows.append(("Symbolic name value", "yes"))
        if d.is_ref:
            if d.dest:
                rows.append(("Destination", "\n    ".join(d.dest)))
            if d.dest_type:
                rows.append(("Destination type", d.dest_type))
        rows.append(("Origin", d.origin))
        if d.scope:
            rows.append(("Scope", d.scope))
        if d.config_classes:
            rows.append(("Configuration classes", "; ".join(f"{v}: {c}" for c, v in d.config_classes)))
        if d.post_build_changeable is not None:
            rows.append(("Post-build changeable", d.post_build_changeable))
        if d.module is not None:
            rows.append(("BSWMD file", d.module.file))
        return rows

    def show_def(self, d):
        self._write("Description", text=d.desc or "(no description)")
        self._write("Definition", self._def_rows(d))
        self._write("Status", [("Instances", "see editor")])

    def show_param(self, el, p, v):
        s = self.app.session
        self._write("Description", text=p.desc or "(no description)")
        self._write("Definition", self._def_rows(p))
        state, ro = s.param_state(el, p, v)
        rows = [(None, "Status"), ("Container", s.model.path_of(el))]
        if v is None:
            rows.append(("Instantiated", "no (definition exists, value not in ECUC)"))
        else:
            rows.append(("Stored value", raw_value(v)))
            rows.append(("State", state or "modified"))
            rows.append(("Changeable", "read-only: " + ro if ro else "yes"))
            if is_auto_value(v):
                rows.append(("Calculated", "IS-AUTO-VALUE = true"))
            anns = annotations(v)
            if anns:
                rows.append(("Annotations", ", ".join(anns)))
            rel = s.model.relative_path(el)
            iv = s.model.initial_values.get((rel, p.path))
            if iv is not None:
                rows.append(("Initial (derived) value", iv))
        xf = s.model.file_of(el)
        if xf:
            rows.append(("File", xf.path))
        live = [r for r in self.app.editor.live if (r.param or r.definition) == p.path]
        for r in live:
            rows.append((f"{r.severity.label} {r.rule_id}", r.message))
        self._write("Status", rows)
        self.select(2 if live else 0)

    def show_container(self, el, cdef):
        s = self.app.session
        self._write("Description", text=(cdef.desc if cdef else "") or "(no description)")
        self._write("Definition", self._def_rows(cdef) if cdef else [("Definition", "not found")])
        rows = [(None, arxml.short_name(el)), ("Path", s.model.path_of(el)), ("Definition-ref", definition_ref(el)),
                ("UUID", el.get("UUID") or "")]
        anns = annotations(el)
        if anns:
            rows.append(("Annotations", ", ".join(anns)))
        refs = s.model.references_to(s.model.path_of(el))
        rows.append(("Referenced by", f"{len(refs)} reference(s)"))
        for r in refs[:30]:
            owner = r.getparent().getparent()
            rows.append(("  ←", f"{s.model.path_of(owner)} [{definition_ref(r).rsplit('/', 1)[-1]}]"))
        xf = s.model.file_of(el)
        if xf:
            rows.append(("File", xf.path))
        self._write("Status", rows)
