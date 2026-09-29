"""Editor area content in DaVinci style.

A ``BasicEditor`` is one editor tab: address line (breadcrumb) + context tree with
<Filter> + form view (label | field | unit | ▾ | status) or grid view for container groups.
"""
from __future__ import annotations

import os

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from .. import arxml, units
from ..bswmd import KIND_LABEL
from ..project import MODULE_TAG, container_children, definition_ref, is_user_defined, raw_value
from .theme import COLORS, SEVERITY_TAG
from .tree import ProjectTree
from .widgets import FONT, LinkLabel, ScrollableFrame, ToolButton, Tooltip

# per definition path: display unit chosen via "Physical Units" (DaVinci keeps this in the UI only)
DISPLAY_UNIT: dict[str, str] = {}


def shown_unit(pdef):
    u = DISPLAY_UNIT.get(pdef.path) or pdef.unit
    return u if units.convertible(pdef.base_unit, u) else pdef.base_unit


def display_value(pdef, raw):
    if raw is None:
        return ""
    if pdef.kind in ("float", "integer"):
        return units.to_display(raw, pdef.base_unit, shown_unit(pdef))
    return raw


def stored_value(pdef, text):
    if pdef.kind in ("float", "integer"):
        return units.to_stored(text.strip(), pdef.base_unit, shown_unit(pdef))
    if pdef.kind in ("boolean", "enum"):
        return text.strip()
    return text


def unit_label(pdef):
    return units.label(shown_unit(pdef)) if (pdef.base_unit or pdef.unit) else ""


class InlineEditor:
    """Entry/Combobox placed over a Treeview cell (grid view)."""

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
        self.w.bind("<Return>", lambda e: self.commit())
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
            return
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


class BasicEditor(tk.Frame):
    """One editor tab. *modules* = None shows all modules (the Basic Editor)."""

    def __init__(self, master, app, modules=None, title="Basic Editor"):
        super().__init__(master, background=COLORS["view_bg"])
        self.app = app
        self.modules = modules
        self.title = title
        self.node = None
        self.live = []
        self.rows = {}
        self.fields = []   # (pdef, index, value element) of the form rows, in order

        # --- address line --------------------------------------------------
        top = tk.Frame(self, background=COLORS["view_bg"])
        top.pack(fill="x", padx=4, pady=(4, 2))
        ToolButton(top, app.icons.tree, self.toggle_tree, "Show/Hide Editor Structure").pack(side="left")
        tk.Label(top, image=app.icons.collapse, background=COLORS["view_bg"]).pack(side="left", padx=(2, 4))
        self.crumbs = tk.Frame(top, background=COLORS["view_bg"])
        self.crumbs.pack(side="left", fill="x", expand=True)
        self.status_icon = tk.Label(top, image=app.icons.ok, background=COLORS["view_bg"])
        self.status_icon.pack(side="right", padx=4)
        self.status_tip = Tooltip(self.status_icon, "No validation messages")
        tk.Frame(self, height=1, background="#d7dde8").pack(fill="x")

        # --- context tree | content ------------------------------------------
        self.pane = ttk.PanedWindow(self, orient="horizontal")
        self.pane.pack(fill="both", expand=True)
        self.tree = ProjectTree(self.pane, app, editor=self, modules=modules)
        self.pane.add(self.tree, weight=1)
        self.content = tk.Frame(self.pane, background=COLORS["view_bg"])
        self.pane.add(self.content, weight=3)
        self.tree_visible = True

        self.form = ScrollableFrame(self.content)
        self.grid_frame = tk.Frame(self.content, background=COLORS["view_bg"])
        self._build_grid()
        self.form.pack(fill="both", expand=True)
        self.mode = "form"

    # ------------------------------------------------------------ plumbing
    def can_close(self):
        return self.modules is not None   # the Basic Editor stays open

    def toggle_tree(self):
        if self.tree_visible:
            self.pane.forget(self.tree)
        else:
            self.pane.insert(0, self.tree, weight=1)
        self.tree_visible = not self.tree_visible

    def populate(self):
        self.after_idle(self._init_sash)
        self.tree.populate()
        self.tree.set_marks(self.app.all_results())

    def _init_sash(self, tries=20):
        """Context tree ~300 px like DaVinci, once the pane has its real size."""
        try:
            w = self.pane.winfo_width()
            if w < 600:
                if tries:
                    self.after(100, lambda: self._init_sash(tries - 1))
                return
            self.pane.sashpos(0, min(320, int(w * 0.3)))
        except tk.TclError:
            pass

    def contains(self, el):
        return self.tree.contains(el)

    # --------------------------------------------------------- address line
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
        icons = self.app.icons
        for i, c in enumerate(chain):
            if i:
                tk.Label(self.crumbs, text="▸", background=COLORS["view_bg"], foreground="#555").pack(side="left")
            icon = icons.module if arxml.local(c) == MODULE_TAG else icons.container
            last = i == len(chain) - 1 and not extra
            lbl = tk.Label(self.crumbs, text=arxml.short_name(c), image=icon, compound="left",
                           background=COLORS["view_bg"], padx=2,
                           font=("Segoe UI", 9, "bold") if last else ("Segoe UI", 9),
                           foreground="#1b1b1b" if last else COLORS["link"], cursor="hand2")
            lbl.pack(side="left")
            lbl.bind("<Button-1>", lambda ev, c=c: self.tree.select_element(c))
        if extra:
            tk.Label(self.crumbs, text="▸", background=COLORS["view_bg"], foreground="#555").pack(side="left")
            tk.Label(self.crumbs, text=extra, image=icons.group, compound="left", background=COLORS["view_bg"],
                     font=("Segoe UI", 9, "bold"), padx=2).pack(side="left")

    def _set_status(self, results):
        errs = [r for r in results if r.severity >= 3]
        warns = [r for r in results if r.severity == 2]
        if errs:
            self.status_icon.configure(image=self.app.icons.error)
        elif warns:
            self.status_icon.configure(image=self.app.icons.warning)
        elif results:
            self.status_icon.configure(image=self.app.icons.info)
        else:
            self.status_icon.configure(image=self.app.icons.ok)
        self.status_tip.text = ("\n".join(f"{r.rule_id}: {r.message.splitlines()[0][:120]}" for r in results[:12])
                                or "No validation messages")

    # ----------------------------------------------------------------- show
    def show_node(self, node):
        self.node = node
        if node is None:
            self.form.clear()
            return
        self.app.remember(self, node)
        if node.kind == "group":
            self._show_grid(node)
            self.app.props.show_def(node.cdef)
        else:
            self._show_form(node)
            if node.el is not None:
                self.app.props.show_container(node.el, node.cdef)

    def refresh(self):
        if self.node is None:
            return
        y = self.form.canvas.yview()[0]
        self.show_node(self.node)
        self.form.canvas.update_idletasks()
        self.form.canvas.yview_moveto(y)

    def _use(self, mode):
        if mode == self.mode:
            return
        (self.form if mode == "grid" else self.grid_frame).pack_forget()
        (self.grid_frame if mode == "grid" else self.form).pack(fill="both", expand=True)
        self.mode = mode

    # ------------------------------------------------------------ form view
    def _section(self, parent, title, row):
        f = tk.Frame(parent, background=COLORS["view_bg"])
        f.grid(row=row, column=0, columnspan=6, sticky="ew", pady=(12, 4))
        tk.Label(f, text=title, background=COLORS["view_bg"], foreground=COLORS["section"],
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Frame(f, height=1, background=COLORS["section_line"]).pack(fill="x", pady=(2, 0))
        return row + 1

    def _show_form(self, node):
        self._use("form")
        s = self.app.session
        el = node.el
        cdef, source = s.container_def(el)
        node.cdef = cdef
        self._set_crumbs(el)
        self.form.clear()
        self.fields = []
        g = self.form.inner
        g.columnconfigure(2, weight=1)
        row = 0
        hdr = tk.Frame(g, background=COLORS["view_bg"])
        hdr.grid(row=row, column=0, columnspan=6, sticky="ew", padx=8, pady=(6, 0))
        row += 1
        is_module = arxml.local(el) == MODULE_TAG
        kind = "Module" if is_module else (KIND_LABEL.get(cdef.kind, "") if cdef else "Container")
        tk.Label(hdr, text=arxml.short_name(el), background=COLORS["view_bg"], font=("Segoe UI", 11, "bold"),
                 image=self.app.icons.module if is_module else self.app.icons.container, compound="left",
                 padx=2).pack(anchor="w")
        tk.Label(hdr, text=f"{kind}   {definition_ref(el)}", background=COLORS["view_bg"], foreground="#777",
                 font=FONT).pack(anchor="w")
        if source:
            row = self._fallback_banner(g, row, el, source)
        if cdef is None:
            self._set_status([])
            return
        if is_module and not source:
            row = self._module_info(g, row, node)
        elif is_module:
            self._set_status([])
        else:
            self.live = s.validate_container(el)
            self._set_status(self.live)
            live = {}
            for r in self.live:
                key = r.param or r.definition
                if key and (key not in live or live[key].severity < r.severity):
                    live[key] = r
            params = [p for p in cdef.params() if not p.is_ref]
            refs = [p for p in cdef.params() if p.is_ref]
            for title, lst in (("Parameters", params), ("References", refs)):
                if not lst:
                    continue
                row = self._section_padded(g, title, row)
                for p in lst:
                    vals = s.model.find_values(el, p.path, s.defs)
                    for i, v in enumerate(vals or [None]):
                        row = self._field_row(g, row, el, p, i, v, len(vals) > 1, live.get(p.path))
        row = self._subcontainers(g, row, el, cdef)
        tk.Frame(g, height=20, background=COLORS["view_bg"]).grid(row=row, column=0)

    def _fallback_banner(self, g, row, el, source):
        """Yellow note: the SIP BSWMD definition is missing, a fallback definition is used."""
        s = self.app.session
        ex = s.defs.explain(definition_ref(el))
        box = tk.Frame(g, background="#fff8d6", highlightthickness=1, highlightbackground="#e6c65c")
        box.grid(row=row, column=0, columnspan=6, sticky="ew", padx=8, pady=(8, 2))
        tk.Label(box, image=self.app.icons.warning, background="#fff8d6").pack(side="left", anchor="n", padx=6,
                                                                           pady=4)
        txt = (f"The SIP BSWMD definition was not found — editing with the {source}. "
               f"Values are written with the definition path used in the ECUC file, so DaVinci reads them "
               f"normally. Range/enum checks are only available when a definition exists.")
        detail = ex.get("hint") or ""
        if ex.get("module"):
            detail = f"module {ex['module']} from {', '.join(os.path.basename(f) for f in ex['files'])}; " + detail
        lbl = tk.Label(box, text=txt + ("\n" + detail if detail else ""), background="#fff8d6", justify="left",
                       anchor="w", wraplength=700, font=FONT)
        lbl.pack(side="left", fill="x", expand=True, padx=(0, 6), pady=4)
        box.bind("<Configure>", lambda e: lbl.configure(wraplength=max(200, e.width - 50)))
        return row + 1

    def _section_padded(self, g, title, row):
        f = tk.Frame(g, background=COLORS["view_bg"])
        f.grid(row=row, column=0, columnspan=6, sticky="ew", padx=8, pady=(12, 4))
        tk.Label(f, text=title, background=COLORS["view_bg"], foreground=COLORS["section"],
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Frame(f, height=1, background=COLORS["section_line"]).pack(fill="x", pady=(2, 0))
        return row + 1

    def _module_info(self, g, row, node):
        s = self.app.session
        el, mdef = node.el, node.cdef
        row = self._section_padded(g, "General", row)
        xf = s.model.file_of(el)
        info = [("Definition", mdef.path), ("Refines", mdef.refined or "-"),
                ("Configuration Variant", arxml.text(el, "IMPLEMENTATION-CONFIG-VARIANT", "-")),
                ("Supported Variants", ", ".join(mdef.supported_variants) or "-"),
                ("BSW Implementation", arxml.text(el, "MODULE-DESCRIPTION-REF", "-")),
                ("SW Version", mdef.impl.sw_version if mdef.impl else "-"),
                ("ECUC File", xf.path if xf else "-"), ("BSWMD File", mdef.file)]
        for k, v in info:
            tk.Label(g, text=k + ":", background=COLORS["view_bg"], foreground=COLORS["label_ro"],
                     anchor="w").grid(row=row, column=1, sticky="w", padx=(10, 12), pady=2)
            e = ttk.Entry(g)
            e.insert(0, v if v not in (None, "") else "-")
            e.configure(state="readonly")
            e.grid(row=row, column=2, sticky="ew", pady=2)
            row += 1
        res = [r for r in self.app.all_results() if r.element is not None and s.model.module_of(r.element) is el]
        self._set_status(res)
        return row

    def _field_row(self, g, row, el, p, i, v, multi, live):
        s = self.app.session
        state, ro = s.param_state(el, p, v)
        bg = COLORS["view_bg"]
        # state icon
        icon = None
        if state == "user-defined":
            icon = self.app.icons.user
        elif ro == "Pre-configured":
            icon = self.app.icons.lock
        elif ro:
            icon = self.app.icons.derived
        tk.Label(g, image=icon or self.app.icons.blank, background=bg).grid(row=row, column=0, padx=(8, 0))
        # label
        text = p.label + (f" [{i}]" if multi else "")
        u = unit_label(p)
        if u:
            text += f" [{u}]"
        lab = tk.Label(g, text=text + ":", background=bg, anchor="w", font=FONT,
                       foreground=COLORS["label_ro"] if (v is None or ro) else COLORS["label"])
        lab.grid(row=row, column=1, sticky="w", padx=(2, 12), pady=2)
        Tooltip(lab, f"{p.name}   ({KIND_LABEL.get(p.kind, p.kind)}, {p.multiplicity_str()})")
        lab.bind("<Button-1>", lambda e: self.app.props.show_param(el, p, v))
        # field
        editable = not ro
        w = self._make_field(g, el, p, i, v, editable)
        w.grid(row=row, column=2, sticky="ew", pady=2)
        # hint (number format)
        hint = "dec" if p.kind in ("integer", "float") else ""
        tk.Label(g, text=hint, background=bg, foreground="#9a9a9a", font=("Segoe UI", 8)).grid(
            row=row, column=3, padx=2)
        # ▾ menu
        mb = tk.Label(g, text="▾", background=bg, foreground=COLORS["link"], cursor="hand2",
                      font=("Segoe UI", 10))
        mb.grid(row=row, column=4, padx=(0, 2))
        mb.bind("<Button-1>", lambda e: self._param_menu(e, el, p, i, v))
        lab.bind("<Button-3>", lambda e: self._param_menu(e, el, p, i, v))
        w.bind("<Button-3>", lambda e: self._param_menu(e, el, p, i, v), add="+")
        # validation
        if live is not None:
            vi = tk.Label(g, image=self.app.icons.severity(live.severity), background=bg)
            vi.grid(row=row, column=5, padx=(0, 8))
            Tooltip(vi, f"{live.rule_id}: {live.message}")
            lab.configure(foreground=COLORS[SEVERITY_TAG[int(live.severity)]])
        self.fields.append((p, i, v, w))
        return row + 1

    def _make_field(self, g, el, p, i, v, editable):
        raw = raw_value(v) if v is not None else None
        cur = display_value(p, raw) if v is not None else ""
        commit = lambda val: self._commit(el, p, i, v, val)
        if p.kind == "boolean":
            var = tk.BooleanVar(value=(raw or "").strip().lower() in ("true", "1"))
            f = tk.Frame(g, background=COLORS["view_bg"])
            cb = ttk.Checkbutton(f, variable=var, style="Form.TCheckbutton",
                                 command=lambda: commit("true" if var.get() else "false"))
            cb.pack(side="left")
            if v is None:
                tk.Label(f, text="(not set)", background=COLORS["view_bg"], foreground="#9a9a9a").pack(side="left")
            if not editable:
                cb.state(["disabled"])
            f._keep = var
            return f
        if p.kind == "enum":
            w = ttk.Combobox(g, values=list(p.literals), state="readonly" if editable else "disabled")
            w.set(cur)
            w.bind("<<ComboboxSelected>>", lambda e: commit(w.get()))
            return w
        if p.is_ref and p.kind not in ("foreign", "instance", "uri"):
            w = ttk.Combobox(g, state="normal" if editable else "disabled")
            w.set(cur)
            w.configure(postcommand=lambda: w.configure(values=self._ref_candidates(p, raw)))
            w.bind("<<ComboboxSelected>>", lambda e: commit(w.get()))
            w.bind("<Return>", lambda e: commit(w.get()))
            w.bind("<FocusOut>", lambda e: w.get() != cur and commit(w.get()))
            if raw:
                w.bind("<Control-Button-1>", lambda e: self.app.goto_path(raw))
            return w
        w = ttk.Entry(g)
        w.insert(0, cur)
        if not editable:
            w.configure(state="readonly")
        else:
            w.bind("<Return>", lambda e: commit(w.get()))
            w.bind("<FocusOut>", lambda e: w.get() != cur and commit(w.get()))
            if p.kind == "multiline":
                w.bind("<Double-1>", lambda e: self._multiline(el, p, i, v, raw or ""))
        if v is None and p.default is not None:
            Tooltip(w, f"not set — default {display_value(p, p.default)}")
        return w

    def _multiline(self, el, p, i, v, raw):
        txt = simpledialog.askstring("Edit", p.label, initialvalue=raw, parent=self)
        if txt is not None:
            self._commit(el, p, i, v, txt)

    def _ref_candidates(self, p, current=None):
        s = self.app.session
        out = []
        for d in p.dest:
            for c in s.model.containers_of_def(d, s.defs):
                out.append(s.model.path_of(c))
            if not out and d.startswith("/AUTOSAR/EcucDefs/"):
                # standard destination and no refined vendor module known: match by the path tail
                tail = "/" + d[len("/AUTOSAR/EcucDefs/"):]
                for dp, els in s.model.def_index.items():
                    if dp.endswith(tail):
                        out.extend(s.model.path_of(c) for c in els)
        if not p.dest and current:
            # definition inferred from the ECUC: offer containers of the same kind as the current target
            t = s.model.resolve(current)
            if t is not None:
                out.extend(s.model.path_of(c) for c in s.model.def_index.get(definition_ref(t), []))
        return sorted(set(out))

    def _subcontainers(self, g, row, el, cdef):
        subs = cdef.containers()
        if not subs:
            return row
        row = self._section_padded(g, "Sub-Containers", row)
        counts = {}
        insts = {}
        for c in container_children(el):
            d = definition_ref(c)
            counts[d] = counts.get(d, 0) + 1
            insts.setdefault(d, []).append(c)
        for cd in subs:
            n = counts.get(cd.path, 0)
            f = tk.Frame(g, background=COLORS["view_bg"])
            f.grid(row=row, column=1, columnspan=4, sticky="w", padx=(2, 0), pady=1)
            tk.Label(f, image=self.app.icons.group if cd.upper > 1 else self.app.icons.container,
                     background=COLORS["view_bg"]).pack(side="left")
            if n == 1 and cd.upper <= 1:
                target = insts[cd.path][0]
                LinkLabel(f, cd.label, lambda t=target: self.tree.select_element(t)).pack(side="left")
            else:
                tk.Label(f, text=cd.label, background=COLORS["view_bg"],
                         foreground=COLORS["label"] if n else COLORS["label_ro"]).pack(side="left")
            tk.Label(f, text=f"  {n} instance(s), multiplicity {cd.multiplicity_str()}",
                     background=COLORS["view_bg"], foreground="#888").pack(side="left")
            if n < cd.upper:
                LinkLabel(f, "Add", lambda cd=cd: self.app.add_container_dialog(el, cd),
                          image=self.app.icons.add).pack(side="left", padx=(10, 0))
            row += 1
        return row

    # ----------------------------------------------------------- editing
    def _commit(self, el, p, i, v, text, mark_user=False):
        app = self.app
        if app.busy:
            app.status("Busy — wait until the background task finished")
            return
        val = stored_value(p, text)
        old = raw_value(v) if v is not None else None
        if v is not None and old == val and not mark_user:
            return
        newv = app.session.model.set_value(el, p, val, index=i)
        if mark_user and not is_user_defined(newv):
            app.session.model.set_user_defined(newv, True)
        app.after_edit(el, f"{p.name} = {val}")

    def _param_menu(self, e, el, p, i, v):
        s = self.app.session
        state, ro = s.param_state(el, p, v)
        m = tk.Menu(self, tearoff=False)
        can_edit = ro != "Pre-configured"
        m.add_command(label="Set to default", state="normal" if (p.default is not None and not p.is_ref
                                                                  and can_edit) else "disabled",
                      command=lambda: self._commit(el, p, i, v, display_value(p, p.default)))
        m.add_separator()
        user = v is not None and is_user_defined(v)
        m.add_command(label="Set user defined", state="normal" if (v is not None and not user and can_edit)
                      else "disabled",
                      command=lambda: (s.model.set_user_defined(v, True), self.app.after_edit(el, "user defined")))
        m.add_command(label="Remove user defined", state="normal" if user else "disabled",
                      command=lambda: (s.model.set_user_defined(v, False), self.app.after_edit(el, "")))
        m.add_separator()
        n = len(s.model.find_values(el, p.path))
        m.add_command(label="Create parameter", image=self.app.icons.add, compound="left",
                      state="normal" if n < p.upper and can_edit else "disabled",
                      command=lambda: self._create(el, p, n))
        m.add_command(label="Delete parameter", image=self.app.icons.delete, compound="left",
                      state="normal" if v is not None and can_edit else "disabled",
                      command=lambda: (s.model.delete_element(v, f"Delete {p.name}"),
                                       self.app.after_edit(el, f"Delete {p.name}")))
        m.add_separator()
        path = f"{s.model.path_of(el)}[{i}:{p.name}]"
        m.add_command(label="Copy Path", image=self.app.icons.copy, compound="left",
                      command=lambda: self._copy(path))
        if p.base_unit and any(units.convertible(p.base_unit, u) for u in units.FACTORS):
            sub = tk.Menu(m, tearoff=False)
            fam = units.FACTORS.get(p.base_unit.upper(), (None,))[0]
            sub._var = uvar = tk.StringVar(value=(shown_unit(p) or "").upper())
            for u, (fa, _f) in units.FACTORS.items():
                if fa == fam:
                    base = " (base unit)" if u == p.base_unit.upper() else ""
                    sub.add_radiobutton(label=f"{units.label(u)}{base}", value=u, variable=uvar,
                                        command=lambda u=u: (DISPLAY_UNIT.__setitem__(p.path, u), self.refresh()))
            m.add_cascade(label="Physical Units", menu=sub)
        if p.is_ref and v is not None and raw_value(v):
            m.add_command(label="Show target", command=lambda: self.app.goto_path(raw_value(v)))
        m.add_command(label="Show properties", image=self.app.icons.properties, compound="left",
                      command=lambda: self.app.props.show_param(el, p, v))
        m.tk_popup(e.x_root, e.y_root)

    def _create(self, el, p, n):
        self.app.session.model.set_value(el, p, p.default or "", index=n)
        self.app.after_edit(el, f"Create {p.name}")

    def _copy(self, txt):
        self.clipboard_clear()
        self.clipboard_append(txt)

    def select_param(self, def_path=None, name=None, index=0):
        """Focus the field of a parameter in the form (used by validation navigation)."""
        for p, i, v, w in self.fields:
            if (def_path and p.path == def_path) or (name and p.name == name):
                if index is None or i == (index or 0):
                    try:
                        w.focus_set()
                        self.form.canvas.update_idletasks()
                        y = w.winfo_y() / max(1, self.form.inner.winfo_height())
                        self.form.canvas.yview_moveto(max(0, y - 0.1))
                    except tk.TclError:
                        pass
                    self.app.props.show_param(self.node.el, p, v)
                    return True
        return False

    # ------------------------------------------------------------ grid view
    def _build_grid(self):
        bar = tk.Frame(self.grid_frame, background=COLORS["view_bg"])
        bar.pack(fill="x", padx=6, pady=4)
        self.grid_title = tk.Label(bar, text="", background=COLORS["view_bg"], font=("Segoe UI", 10, "bold"))
        self.grid_title.pack(side="left")
        ToolButton(bar, self.app.icons.delete, self._grid_delete, "Delete selected containers").pack(side="right")
        ToolButton(bar, self.app.icons.add, lambda: self.node and self.app.add_container_dialog(
            self.node.el, self.node.cdef), "Add container").pack(side="right")
        frm = tk.Frame(self.grid_frame, background=COLORS["view_bg"])
        frm.pack(fill="both", expand=True)
        self.tv = ttk.Treeview(frm, selectmode="extended")
        ys = ttk.Scrollbar(frm, orient="vertical", command=self.tv.yview)
        xs = ttk.Scrollbar(frm, orient="horizontal", command=self.tv.xview)
        self.tv.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.tv.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        frm.rowconfigure(0, weight=1)
        frm.columnconfigure(0, weight=1)
        for t in ("error", "warning", "info", "improvement"):
            self.tv.tag_configure(t, foreground=COLORS[t])
        self.tv.tag_configure("odd", background="#f7f9fc")
        self.tv.bind("<Double-1>", self._grid_double)
        self.tv.bind("<<TreeviewSelect>>", self._grid_select)
        self.tv.bind("<Delete>", lambda e: self._grid_delete())

    def _show_grid(self, node):
        self._use("grid")
        s = self.app.session
        cdef = node.cdef
        self._set_crumbs(node.el, cdef.name)
        self.grid_title.configure(text=f"{cdef.label}  ({len(node.el_list)} of {cdef.multiplicity_str()})")
        cols_def = cdef
        if not cdef.params() and node.el_list:      # fallback definition: take columns from an instance
            cols_def = s.container_def(node.el_list[0])[0] or cdef
        params = cols_def.params()[:60]
        self.grid_params = params
        cols = [p.path for p in params]
        self.tv.delete(*self.tv.get_children(""))
        self.rows.clear()
        self.tv.configure(columns=cols, displaycolumns=cols)
        self.tv.heading("#0", text="Name")
        self.tv.column("#0", width=260, stretch=False)
        for p in params:
            u = unit_label(p)
            self.tv.heading(p.path, text=p.label + (f" [{u}]" if u else ""))
            self.tv.column(p.path, width=max(80, min(220, len(p.label) * 7)), stretch=False)
        res = []
        for n, el in enumerate(node.el_list):
            vals = []
            for p in params:
                vs = s.model.find_values(el, p.path)
                vals.append(display_value(p, raw_value(vs[0])) if vs else "")
            sev = self.tree.marks.get(el)
            tags = [SEVERITY_TAG[int(sev)]] if sev is not None else []
            if n % 2:
                tags.append("odd")
            iid = self.tv.insert("", "end", text=arxml.short_name(el), values=vals, tags=tuple(tags),
                                 image=self.app.icons.container)
            self.rows[iid] = el
        for r in self.app.all_results():
            if r.element is not None:
                c = r.element
                while c is not None and arxml.local(c) != "ECUC-CONTAINER-VALUE":
                    c = c.getparent()
                if c is not None and c in node.el_list:
                    res.append(r)
        self._set_status(res)

    def _grid_double(self, e):
        iid = self.tv.identify_row(e.y)
        col = self.tv.identify_column(e.x)
        el = self.rows.get(iid)
        if el is None:
            return
        if col == "#0":
            self.tree.select_element(el)
            return
        p = self.grid_params[int(col[1:]) - 1]
        s = self.app.session
        vs = s.model.find_values(el, p.path)
        v = vs[0] if vs else None
        state, ro = s.param_state(el, p, v)
        if ro:
            messagebox.showinfo("Read-only", f"{p.name} is {ro.lower()}. Open the container and use "
                                             f"'Set user defined' to change it.")
            return
        cur = display_value(p, raw_value(v)) if v is not None else ""
        cands = self._ref_candidates(p) if p.is_ref and p.kind not in ("foreign", "instance", "uri") else None
        InlineEditor(self.tv, iid, col, p, cur, cands, lambda val: self._commit(el, p, 0, v, val))

    def _grid_select(self, _e=None):
        sel = self.tv.selection()
        if sel and sel[0] in self.rows:
            el = self.rows[sel[0]]
            self.app.props.show_container(el, self.node.cdef if self.node else None)

    def _grid_delete(self):
        els = [self.rows[i] for i in self.tv.selection() if i in self.rows]
        if not els:
            return
        if not messagebox.askyesno("Delete", f"Delete {len(els)} container(s)?"):
            return
        for el in els:
            self.app.session.model.delete_element(el)
        self.app.after_edit(self.node.el, f"Deleted {len(els)} container(s)", structure=True)
