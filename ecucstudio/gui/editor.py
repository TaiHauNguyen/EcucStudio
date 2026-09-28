"""Center editor: address line + form view (one container) / grid view (container group)."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from .. import arxml, units
from ..bswmd import KIND_LABEL
from ..project import MODULE_TAG, definition_ref, is_user_defined, raw_value
from .theme import COLORS, SEVERITY_TAG


def display_value(pdef, raw):
    if raw is None:
        return ""
    if pdef.kind == "float" or pdef.kind == "integer":
        return units.to_display(raw, pdef.base_unit, pdef.unit)
    return raw


def unit_label(pdef):
    if pdef.unit and units.convertible(pdef.base_unit, pdef.unit):
        return units.label(pdef.unit)
    return units.label(pdef.base_unit or pdef.unit)


class InlineEditor:
    """Places an Entry/Combobox over a Treeview cell and commits on Enter/selection/focus-out."""

    def __init__(self, tv, iid, column, pdef, current, candidates, on_commit):
        self.tv, self.on_commit, self.done = tv, on_commit, False
        bbox = tv.bbox(iid, column)
        if not bbox:
            return
        x, y, w, h = bbox
        k = pdef.kind
        self.var = tk.StringVar(value=current)
        if k in ("boolean", "enum"):
            vals = ["true", "false"] if k == "boolean" else list(pdef.literals)
            self.w = ttk.Combobox(tv, textvariable=self.var, values=vals, state="readonly")
            self.w.bind("<<ComboboxSelected>>", lambda e: self.commit())
        elif candidates is not None:
            self.w = ttk.Combobox(tv, textvariable=self.var, values=candidates)
            self.w.bind("<<ComboboxSelected>>", lambda e: self.commit())
        else:
            self.w = ttk.Entry(tv, textvariable=self.var)
        self.w.place(x=x, y=y, width=max(w, 160 if candidates is None else 520), height=h)
        self.w.focus_set()
        if isinstance(self.w, ttk.Entry) and not isinstance(self.w, ttk.Combobox):
            self.w.select_range(0, "end")
        self.w.bind("<Return>", lambda e: self.commit())
        self.w.bind("<KP_Enter>", lambda e: self.commit())
        self.w.bind("<Escape>", lambda e: self.cancel())
        self.w.bind("<FocusOut>", lambda e: self.tv.after(50, self._focus_out))

    def _focus_out(self):
        if self.done:
            return
        try:
            f = self.tv.focus_get()
        except (KeyError, tk.TclError):
            f = None
        if f is not None and str(f).startswith(str(self.w)):
            return  # focus moved into the combobox popdown
        self.commit()

    def commit(self):
        if self.done:
            return
        self.done = True
        val = self.var.get()
        self.w.destroy()
        self.on_commit(val)

    def cancel(self):
        self.done = True
        self.w.destroy()


class EditorPanel(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.node = None
        self.rows = {}
        self.live = []

        self.crumbs = ttk.Frame(self)
        self.crumbs.pack(fill="x", padx=4, pady=(4, 2))
        self.header = ttk.Label(self, text="", style="Header.TLabel")
        self.header.pack(fill="x", padx=6)
        self.subheader = ttk.Label(self, text="", foreground=COLORS["readonly"])
        self.subheader.pack(fill="x", padx=6, pady=(0, 4))

        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True)
        self.tv = ttk.Treeview(frm, columns=("value", "unit", "mult", "state"), selectmode="extended")
        self.tv.heading("#0", text="Parameter")
        self.tv.heading("value", text="Value")
        self.tv.heading("unit", text="Unit")
        self.tv.heading("mult", text="Mult.")
        self.tv.heading("state", text="State")
        self.tv.column("#0", width=280, stretch=False)
        self.tv.column("value", width=420, stretch=True)
        self.tv.column("unit", width=50, stretch=False, anchor="center")
        self.tv.column("mult", width=50, stretch=False, anchor="center")
        self.tv.column("state", width=150, stretch=False)
        ys = ttk.Scrollbar(frm, orient="vertical", command=self.tv.yview)
        xs = ttk.Scrollbar(frm, orient="horizontal", command=self.tv.xview)
        self.tv.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.tv.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        frm.rowconfigure(0, weight=1)
        frm.columnconfigure(0, weight=1)
        self.tv.tag_configure("notinst", foreground=COLORS["notinst"])
        self.tv.tag_configure("readonly", foreground=COLORS["readonly"])
        self.tv.tag_configure("user", foreground=COLORS["user"])
        for t in ("error", "warning", "info", "improvement"):
            self.tv.tag_configure(t, foreground=COLORS[t])
        self.tv.tag_configure("section", background=COLORS["header_bg"], font=("Segoe UI", 9, "bold"))
        self.tv.bind("<Double-1>", self._on_double)
        self.tv.bind("<Return>", lambda e: self._edit_focused())
        self.tv.bind("<F2>", lambda e: self._edit_focused())
        self.tv.bind("<Delete>", lambda e: self._delete_selected())
        self.tv.bind("<<TreeviewSelect>>", self._on_select)
        self.tv.bind("<Button-3>", self._on_menu)
        self.mode = "form"

    # ------------------------------------------------------------ address line
    def _set_crumbs(self, el, extra=None):
        for w in self.crumbs.winfo_children():
            w.destroy()
        chain = []
        e = el
        while e is not None:
            if arxml.local(e) in (MODULE_TAG, "ECUC-CONTAINER-VALUE"):
                chain.append(e)
            if arxml.local(e) == MODULE_TAG:
                break
            e = e.getparent()
        chain.reverse()
        for i, c in enumerate(chain):
            if i:
                ttk.Label(self.crumbs, text=" › ").pack(side="left")
            lbl = ttk.Label(self.crumbs, text=arxml.short_name(c), style="Crumb.TLabel", cursor="hand2")
            lbl.pack(side="left")
            lbl.bind("<Button-1>", lambda ev, c=c: self.app.tree.select_element(c))
        if extra:
            ttk.Label(self.crumbs, text=" › " + extra).pack(side="left")

    # ------------------------------------------------------------------- show
    def clear(self):
        self.tv.delete(*self.tv.get_children(""))
        self.rows.clear()

    def show(self, node):
        self.node = node
        self.clear()
        if node is None:
            self.header.config(text="")
            self.subheader.config(text="")
            return
        if node.kind == "group":
            self._show_grid(node)
        else:
            self._show_form(node)

    def refresh(self):
        if self.node is not None:
            sel = self.tv.selection()
            y = self.tv.yview()[0]
            self.show(self.node)
            self.tv.yview_moveto(y)
            for s in sel:
                if self.tv.exists(s):
                    self.tv.selection_add(s)

    def _configure_form_columns(self):
        if self.mode == "form":
            return
        self.mode = "form"
        self.tv.configure(columns=("value", "unit", "mult", "state"), displaycolumns=("value", "unit", "mult", "state"))
        self.tv.heading("#0", text="Parameter")
        for col, txt, w, st in (("value", "Value", 420, True), ("unit", "Unit", 50, False),
                                ("mult", "Mult.", 50, False), ("state", "State", 150, False)):
            self.tv.heading(col, text=txt)
            self.tv.column(col, width=w, stretch=st)
        self.tv.column("#0", width=280)

    def _show_form(self, node):
        self._configure_form_columns()
        s = self.app.session
        el, cdef = node.el, node.cdef
        self._set_crumbs(el)
        path = s.model.path_of(el)
        dref = definition_ref(el)
        if arxml.local(el) == MODULE_TAG:
            variant = arxml.text(el, "IMPLEMENTATION-CONFIG-VARIANT", "")
            impl = arxml.text(el, "MODULE-DESCRIPTION-REF", "")
            xf = s.model.file_of(el)
            self.header.config(text=f"Module {arxml.short_name(el)}")
            self.subheader.config(text=f"{dref}   |   {variant}   |   {impl}   |   {xf.path if xf else ''}")
            self._module_rows(node)
            return
        self.header.config(text=f"{arxml.short_name(el)}")
        kind = KIND_LABEL.get(cdef.kind, "") if cdef else "Unknown definition"
        self.subheader.config(text=f"{path}    ({kind}: {dref})")
        if cdef is None:
            self.tv.insert("", "end", text="Definition not found in SIP / BSWMD", tags=("error",))
            return
        self.live = s.validate_container(el)
        live_by_param = {}
        for r in self.live:
            key = r.param or r.definition
            if key and (key not in live_by_param or live_by_param[key].severity < r.severity):
                live_by_param[key] = r
        params = [p for p in cdef.params() if not p.is_ref]
        refs = [p for p in cdef.params() if p.is_ref]
        for title, lst in (("Parameters", params), ("References", refs)):
            if not lst:
                continue
            sid = self.tv.insert("", "end", text=f"{title} ({len(lst)})", open=True, tags=("section",))
            self.rows[sid] = None
            for p in lst:
                vals = s.model.find_values(el, p.path, s.defs)
                insts = vals or [None]
                for i, v in enumerate(insts):
                    name = p.name if len(insts) == 1 else f"{p.name} [{i}]"
                    self._param_row(sid, el, p, i, v, name, live_by_param.get(p.path))
        subs = [c for c in cdef.containers()]
        if subs:
            sid = self.tv.insert("", "end", text=f"Sub-containers ({len(subs)} definitions)", open=False,
                                 tags=("section",))
            self.rows[sid] = None
            from ..project import container_children
            counts = {}
            for c in container_children(el):
                counts[definition_ref(c)] = counts.get(definition_ref(c), 0) + 1
            for c in subs:
                n = counts.get(c.path, 0)
                tag = "notinst" if n == 0 else ""
                iid = self.tv.insert(sid, "end", text=c.name, image=self.app.icons.container,
                                     values=(f"{n} instance(s)", "", c.multiplicity_str(), KIND_LABEL[c.kind]),
                                     tags=(tag,) if tag else ())
                self.rows[iid] = ("subdef", el, c)

    def _module_rows(self, node):
        s = self.app.session
        mdef = node.cdef
        if mdef is None:
            self.tv.insert("", "end", text="Module definition not found", tags=("error",))
            return
        info = [("Definition", mdef.path), ("Refines", mdef.refined or ""),
                ("Supported variants", ", ".join(mdef.supported_variants)),
                ("BSW implementation", mdef.impl.path if mdef.impl else ""),
                ("SW version", mdef.impl.sw_version if mdef.impl else ""),
                ("BSWMD file", mdef.file)]
        sid = self.tv.insert("", "end", text="Module", open=True, tags=("section",))
        for k, v in info:
            self.tv.insert(sid, "end", text=k, values=(v, "", "", ""))
        sid = self.tv.insert("", "end", text="Containers", open=True, tags=("section",))
        from ..project import container_children
        counts = {}
        for c in container_children(node.el):
            counts[definition_ref(c)] = counts.get(definition_ref(c), 0) + 1
        for c in mdef.containers():
            n = counts.get(c.path, 0)
            iid = self.tv.insert(sid, "end", text=c.name, image=self.app.icons.container,
                                 values=(f"{n} instance(s)", "", c.multiplicity_str(), KIND_LABEL[c.kind]),
                                 tags=("notinst",) if n == 0 else ())
            self.rows[iid] = ("subdef", node.el, c)

    def _param_row(self, parent, el, p, i, v, name, live):
        s = self.app.session
        raw = raw_value(v) if v is not None else None
        state, ro = s.param_state(el, p, v)
        tags = []
        if v is None:
            tags.append("notinst")
        elif ro:
            tags.append("readonly")
        elif state == "user-defined":
            tags.append("user")
        if live is not None:
            tags = [SEVERITY_TAG[int(live.severity)]]
            state = (state + "  " if state else "") + f"⚠ {live.rule_id}"
        shown = display_value(p, raw) if v is not None else ("<not set>" if p.default is None
                                                              else f"<not set>  (default {display_value(p, p.default)})")
        img = self.app.icons.ref if p.is_ref else self.app.icons.param
        iid = self.tv.insert(parent, "end", text=name, image=img,
                             values=(shown, unit_label(p), p.multiplicity_str(), state), tags=tuple(tags))
        self.rows[iid] = ("param", el, p, i, v)

    def _show_grid(self, node):
        s = self.app.session
        cdef = node.cdef
        self._set_crumbs(node.el, cdef.name)
        self.header.config(text=f"{cdef.name}  ({len(node.el_list)} instances)")
        self.subheader.config(text=f"{cdef.path}   multiplicity {cdef.multiplicity_str()}   "
                                   f"— double-click a cell to edit, select a row to open it in the tree")
        params = cdef.params()[:60]
        cols = [p.path for p in params]
        self.mode = "grid"
        self.tv.configure(columns=cols, displaycolumns=cols)
        self.tv.heading("#0", text="Name")
        self.tv.column("#0", width=260)
        for p in params:
            u = unit_label(p)
            self.tv.heading(p.path, text=p.name + (f" [{u}]" if u else ""))
            self.tv.column(p.path, width=max(80, min(220, len(p.name) * 7)), stretch=False)
        self.grid_params = params
        for el in node.el_list:
            vals = []
            for p in params:
                vs = s.model.find_values(el, p.path)
                vals.append(display_value(p, raw_value(vs[0])) if vs else "")
            sev = self.app.tree.marks.get(el)
            tags = (SEVERITY_TAG[int(sev)],) if sev is not None else ()
            iid = self.tv.insert("", "end", text=arxml.short_name(el), values=vals, tags=tags,
                                 image=self.app.icons.container)
            self.rows[iid] = ("grid", el, cdef)

    # ------------------------------------------------------------------ edit
    def _on_double(self, e):
        iid = self.tv.identify_row(e.y)
        col = self.tv.identify_column(e.x)
        if not iid:
            return
        row = self.rows.get(iid)
        if row is None:
            return
        if row[0] == "subdef":
            self.app.add_container_dialog(row[1], row[2])
            return
        if row[0] == "grid":
            if col == "#0":
                self.app.tree.select_element(row[1])
                return
            idx = int(col[1:]) - 1
            p = self.grid_params[idx]
            vs = self.app.session.model.find_values(row[1], p.path)
            self.begin_edit(iid, col, row[1], p, 0, vs[0] if vs else None)
            return
        if row[0] == "param":
            _k, el, p, i, v = row
            self.begin_edit(iid, "#1", el, p, i, v)

    def _edit_focused(self):
        iid = self.tv.focus()
        row = self.rows.get(iid) if iid else None
        if row and row[0] == "param":
            _k, el, p, i, v = row
            self.begin_edit(iid, "#1", el, p, i, v)

    def begin_edit(self, iid, col, el, p, i, v):
        app = self.app
        if app.busy:
            app.status("Busy — wait until the background task finished")
            return
        s = app.session
        state, ro = s.param_state(el, p, v)
        if ro == "Pre-configured":
            messagebox.showinfo("Read-only", f"{p.name} is pre-configured by the SIP and cannot be changed.")
            return
        mark_user = False
        if ro:
            if not messagebox.askyesno(
                    "Derived parameter",
                    f"{p.name} is derived from the input files (read-only in DaVinci).\n\n"
                    f"Set it to User-Defined so that the value is kept during project update, and edit it?"):
                return
            mark_user = True
        current = display_value(p, raw_value(v)) if v is not None else (display_value(p, p.default) or "")
        if p.kind == "multiline":
            txt = simpledialog.askstring("Edit", p.name, initialvalue=current, parent=self)
            if txt is not None:
                self._commit(el, p, i, v, txt, mark_user)
            return
        cands = None
        if p.is_ref and p.kind not in ("foreign", "instance", "uri"):
            cands = self._ref_candidates(p)
        InlineEditor(self.tv, iid, col, p, current, cands,
                     lambda val: self._commit(el, p, i, v, val, mark_user))

    def _ref_candidates(self, p):
        s = self.app.session
        out = []
        for d in p.dest:
            for c in s.model.containers_of_def(d, s.defs):
                out.append(s.model.path_of(c))
        return sorted(set(out))

    def _commit(self, el, p, i, v, val, mark_user):
        app = self.app
        old = raw_value(v) if v is not None else None
        if p.kind in ("float", "integer"):
            val = units.to_stored(val.strip(), p.base_unit, p.unit)
        if p.kind in ("boolean", "integer", "float", "enum"):
            val = val.strip()
        if v is not None and old == val and not mark_user:
            return
        newv = app.session.model.set_value(el, p, val, index=i)
        if mark_user and not is_user_defined(newv):
            app.session.model.set_user_defined(newv, True)
        app.after_edit(el, f"{p.name} = {val}")

    def _delete_selected(self):
        for iid in self.tv.selection():
            row = self.rows.get(iid)
            if row and row[0] == "param" and row[4] is not None:
                self.app.session.model.delete_element(row[4], f"Delete {row[2].name}")
        if self.node is not None and self.node.el is not None:
            self.app.after_edit(self.node.el, "Delete parameter")

    # ------------------------------------------------------------ selection
    def _on_select(self, _e=None):
        sel = self.tv.selection()
        if not sel:
            return
        row = self.rows.get(sel[0])
        if row is None:
            return
        if row[0] == "param":
            _k, el, p, i, v = row
            self.app.props.show_param(el, p, v)
            self.app.status(f"{p.path}")
        elif row[0] == "subdef":
            self.app.props.show_def(row[2])
        elif row[0] == "grid":
            self.app.props.show_container(row[1], row[2])

    def select_param(self, def_path=None, name=None, index=0):
        for iid, row in self.rows.items():
            if row and row[0] == "param":
                p = row[2]
                if (def_path and p.path == def_path) or (name and p.name == name):
                    if row[3] == (index or 0) or index is None:
                        self.tv.see(iid)
                        self.tv.selection_set(iid)
                        self.tv.focus(iid)
                        return True
        return False

    # ------------------------------------------------------------------ menu
    def _on_menu(self, e):
        iid = self.tv.identify_row(e.y)
        if not iid:
            return
        if iid not in self.tv.selection():
            self.tv.selection_set(iid)
        row = self.rows.get(iid)
        if row is None:
            return
        m = tk.Menu(self, tearoff=False)
        s = self.app.session
        if row[0] == "param":
            _k, el, p, i, v = row
            m.add_command(label="Edit value…", command=lambda: self.begin_edit(iid, "#1", el, p, i, v))
            if p.default is not None and not p.is_ref:
                m.add_command(label=f"Set to default ({p.default})",
                              command=lambda: self._commit(el, p, i, v, display_value(p, p.default), False))
            n = len(s.model.find_values(el, p.path))
            if n < p.upper:
                m.add_command(label="Add instance", command=lambda: self._add_instance(el, p))
            if v is not None:
                if is_user_defined(v):
                    m.add_command(label="Remove User-Defined flag",
                                  command=lambda: (s.model.set_user_defined(v, False), self.app.after_edit(el, "")))
                else:
                    m.add_command(label="Set User-Defined",
                                  command=lambda: (s.model.set_user_defined(v, True), self.app.after_edit(el, "")))
                m.add_command(label="Delete parameter", command=self._delete_selected)
                if p.is_ref and raw_value(v):
                    m.add_command(label="Go to target", command=lambda: self.app.goto_path(raw_value(v)))
                m.add_separator()
                m.add_command(label="Copy value", command=lambda: self._copy(raw_value(v) or ""))
            m.add_command(label="Copy definition path", command=lambda: self._copy(p.path))
            refs = None
        elif row[0] == "subdef":
            m.add_command(label=f"Add {row[2].name}…", command=lambda: self.app.add_container_dialog(row[1], row[2]))
        elif row[0] == "grid":
            m.add_command(label="Open", command=lambda: self.app.tree.select_element(row[1]))
            m.add_command(label="Delete container", command=lambda: self.app.delete_container(row[1]))
        m.tk_popup(e.x_root, e.y_root)

    def _add_instance(self, el, p):
        n = len(self.app.session.model.find_values(el, p.path))
        self.app.session.model.set_value(el, p, p.default or "", index=n)
        self.app.after_edit(el, f"Add {p.name}")

    def _copy(self, txt):
        self.clipboard_clear()
        self.clipboard_append(txt)
