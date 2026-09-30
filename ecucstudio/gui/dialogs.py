"""Dialogs in DaVinci wizard style: Generate, Settings, Add container, Open files."""
import os
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import arxml, davinci
from ..project import definition_ref
from .navigator import DOMAINS, domain_of
from .theme import COLORS
from .widgets import dialog_header

CHECK, UNCHECK, PARTIAL = "☑", "☐", "◩"


class _Dialog(tk.Toplevel):
    def __init__(self, master, title, header, text, icon=None):
        super().__init__(master)
        self.title(title)
        self.transient(master)
        self.configure(background=COLORS["bg"])
        self.result = None
        dialog_header(self, header, text, icon)
        self.body = ttk.Frame(self, padding=10)
        self.body.pack(fill="both", expand=True)
        self.bind("<Escape>", lambda e: self.destroy())

    def buttons(self, ok_text="Finish"):
        # packed "before" the body so the buttons stay visible when the window is small
        bar = ttk.Frame(self, padding=(10, 8))
        bar.pack(fill="x", side="bottom", before=self.body)
        tk.Frame(self, height=1, background="#c8c8c8").pack(fill="x", side="bottom", before=self.body)
        self.cancel_btn = ttk.Button(bar, text="Cancel", command=self.cancel, width=12)
        self.cancel_btn.pack(side="right")
        self.ok_btn = ttk.Button(bar, text=ok_text, command=self.ok, width=12)
        self.ok_btn.pack(side="right", padx=6)
        return bar

    def cancel(self):
        self.destroy()

    def run(self):
        self.grab_set()
        self.wait_window()
        return self.result

    def ok(self):
        self.destroy()


class DvCfgCmdPicker(ttk.Frame):
    """Combobox with detected DVCfgCmd installations + browse button."""

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        s = app.session
        sip = s.project.sip_dir if s.project else None
        extra = [app.cfg.get("dvcfgcmd")] if app.cfg.get("dvcfgcmd") else []
        self.installs = davinci.find_installations(sip, extra)
        self.var = tk.StringVar(value=app.cfg.get("dvcfgcmd") or (self.installs[0].exe if self.installs else ""))
        self.cb = ttk.Combobox(self, textvariable=self.var, values=[i.exe for i in self.installs], width=90)
        self.cb.pack(side="left", fill="x", expand=True)
        ttk.Button(self, text="…", width=3, command=self.browse).pack(side="left", padx=2)
        want = davinci.sip_tool_version(sip) if sip else None
        info = []
        for i in self.installs:
            mark = "  ✔ matches SIP" if want and (i.version, i.build) == want else ""
            info.append(f"{i.version or '?'} {i.build}{mark}  —  {i.exe}")
        self.info = ttk.Label(master, text="\n".join(info) or "No DVCfgCmd.exe found — browse for it.",
                              foreground="#555")

    def browse(self):
        f = filedialog.askopenfilename(title="DVCfgCmd.exe", filetypes=[("DVCfgCmd", "DVCfgCmd.exe"), ("exe", "*.exe")])
        if f:
            self.var.set(os.path.normpath(f))

    def get(self):
        return self.var.get().strip()


class GenerateDialog(_Dialog):
    def __init__(self, master, app):
        super().__init__(master, "Generate", "Generate Code",
                         "Start the validation and code generation process. Optionally the code generation run "
                         "can be configured.", app.icons.generate)
        self.app = app
        s = app.session
        b = self.body

        # --- generation steps tree -----------------------------------------
        tf = ttk.Frame(b)
        tf.pack(fill="both", expand=True)
        side = ttk.Frame(tf)
        side.pack(side="left", fill="y", padx=(0, 4))
        ttk.Button(side, text="All", width=6, command=lambda: self._set_all(True)).pack(pady=1)
        ttk.Button(side, text="None", width=6, command=lambda: self._set_all(False)).pack(pady=1)
        ttk.Button(side, text="Dirty", width=6, command=self._dirty).pack(pady=1)
        self.tv = ttk.Treeview(tf, columns=("calc", "val", "gen"), height=14, selectmode="browse")
        for c, t, w in (("#0", "Generation Step", 380), ("calc", "Calculation", 90), ("val", "Validation", 90),
                        ("gen", "Generation", 90)):
            self.tv.heading(c, text=t, anchor="w")
            self.tv.column(c, width=w, stretch=c == "#0")
        ys = ttk.Scrollbar(tf, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=ys.set)
        self.tv.pack(side="left", fill="both", expand=True)
        ys.pack(side="left", fill="y")
        self.tv.bind("<Button-1>", self._click)
        self.tv.bind("<space>", lambda e: self._toggle(self.tv.focus()))

        prev = set(app.cfg.get("gen_modules", {}).get(os.path.normcase(s.project.path), []))
        self.checked = {}
        self.items = {}      # iid -> module def path
        root = self.tv.insert("", "end", text="DaVinci Configurator Code Generation", open=True,
                              image=app.icons.generate)
        self.root_iid = root
        groups = {}
        order = [d for d, _ in DOMAINS] + ["Other"]
        mods = sorted(s.model.modules, key=lambda m: (order.index(domain_of(arxml.short_name(m))),
                                                      arxml.short_name(m).lower()))
        for m in mods:
            name = arxml.short_name(m)
            d = domain_of(name)
            if d not in groups:
                groups[d] = self.tv.insert(root, "end", text=d, open=False)
            dref = definition_ref(m)
            iid = self.tv.insert(groups[d], "end", text=f"{name}: {dref.rsplit('/', 1)[-1]}",
                                 image=app.icons.module)
            self.items[iid] = dref
            self.checked[iid] = (dref in prev) if prev else True
        self.groups = groups
        self._refresh_marks()

        # --- properties (collapsed like DaVinci's "Properties >>") ----------
        self.props_open = tk.BooleanVar(value=False)
        pbar = ttk.Frame(b)
        pbar.pack(fill="x", pady=(6, 0))
        self.props_btn = ttk.Button(pbar, text="Properties >>", command=self._toggle_props)
        self.props_btn.pack(side="left")
        self.status = ttk.Label(pbar, text="", foreground="#555")
        self.status.pack(side="left", padx=8)
        self.opt = ttk.Frame(b)

        o = self.opt
        ttk.Label(o, text="DVCfgCmd.exe:").grid(row=0, column=0, sticky="w")
        self.picker = DvCfgCmdPicker(o, app)
        self.picker.grid(row=0, column=1, sticky="ew")
        self.picker.info.grid(row=1, column=1, sticky="w", pady=(0, 6))
        ttk.Label(o, text="Target:").grid(row=2, column=0, sticky="w")
        self.gen_type = tk.StringVar(value="(project setting)")
        ttk.Combobox(o, textvariable=self.gen_type, values=("(project setting)", "REAL", "VTT"), width=16,
                     state="readonly").grid(row=2, column=1, sticky="w")
        self.swcs = tk.BooleanVar(value=False)
        ttk.Checkbutton(o, text="Generate SWC templates and contract headers (default set of the project)",
                        variable=self.swcs).grid(row=3, column=1, sticky="w")
        self.ext = tk.BooleanVar(value=True)
        ttk.Checkbutton(o, text="Run external generation steps", variable=self.ext).grid(row=4, column=1, sticky="w")
        self.save_proj = tk.BooleanVar(value=False)
        ttk.Checkbutton(o, text="Save project after generation (--saveProject)",
                        variable=self.save_proj).grid(row=5, column=1, sticky="w")
        self.local_first = tk.BooleanVar(value=True)
        ttk.Checkbutton(o, text="Stop if the local validation reports errors in the selected modules",
                        variable=self.local_first).grid(row=6, column=1, sticky="w")
        ttk.Label(o, text="Command:").grid(row=7, column=0, sticky="nw", pady=(6, 0))
        self.cmd = tk.Text(o, height=3, wrap="word", font=("Consolas", 8))
        self.cmd.grid(row=7, column=1, sticky="ew", pady=(6, 0))
        o.columnconfigure(1, weight=1)
        for v in (self.swcs, self.ext, self.save_proj, self.gen_type):
            v.trace_add("write", lambda *a: self._preview())
        self.picker.var.trace_add("write", lambda *a: self._preview())

        self.buttons("Generate")
        self.geometry("920x680")
        self.running = False
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self._preview()

    # ---------------------------------------------------------- tree checks
    def _refresh_marks(self):
        for iid, dref in self.items.items():
            base = self.tv.item(iid, "text").lstrip(CHECK + UNCHECK + " ")
            self.tv.item(iid, text=f"{CHECK if self.checked[iid] else UNCHECK} {base}")
        for d, gid in self.groups.items():
            kids = self.tv.get_children(gid)
            n = sum(1 for k in kids if self.checked[k])
            mark = CHECK if n == len(kids) else (UNCHECK if n == 0 else PARTIAL)
            self.tv.item(gid, text=f"{mark} {d}")
        total = sum(self.checked.values())
        mark = CHECK if total == len(self.checked) else (UNCHECK if total == 0 else PARTIAL)
        self.tv.item(self.root_iid, text=f"{mark} DaVinci Configurator Code Generation")
        if hasattr(self, "status"):
            self.status.configure(text=f"{total} of {len(self.checked)} generators selected")
        if hasattr(self, "cmd"):
            self._preview()

    def _click(self, e):
        if self.running:
            return
        iid = self.tv.identify_row(e.y)
        if iid and self.tv.identify_element(e.x, e.y) in ("text", "image", "padding") and \
                self.tv.identify_column(e.x) == "#0":
            self._toggle(iid)

    def _toggle(self, iid):
        if not iid:
            return
        if iid in self.items:
            self.checked[iid] = not self.checked[iid]
        else:
            kids = self._leaves(iid)
            on = not all(self.checked[k] for k in kids)
            for k in kids:
                self.checked[k] = on
        self._refresh_marks()

    def _leaves(self, iid):
        if iid in self.items:
            return [iid]
        out = []
        for c in self.tv.get_children(iid):
            out += self._leaves(c)
        return out

    def _set_all(self, on):
        for k in self.checked:
            self.checked[k] = on
        self._refresh_marks()

    def _dirty(self):
        s = self.app.session
        dirty = {id(f) for f in s.model.dirty_files()}
        path_by_iid = {iid: d for iid, d in self.items.items()}
        mods = {definition_ref(m): m for m in s.model.modules}
        for iid, d in path_by_iid.items():
            self.checked[iid] = id(s.model.module_file.get(mods.get(d))) in dirty
        self._refresh_marks()

    def _toggle_props(self):
        if self.props_open.get():
            self.opt.pack_forget()
            self.props_btn.configure(text="Properties >>")
        else:
            self.opt.pack(fill="x", pady=6)
            self.props_btn.configure(text="<< Properties")
        self.props_open.set(not self.props_open.get())

    # ------------------------------------------------------------- options
    def options(self):
        o = davinci.GenerateOptions()
        if not all(self.checked.values()):
            o.modules = [self.items[i] for i, on in self.checked.items() if on]
        gt = self.gen_type.get()
        o.gen_type = gt if gt in ("REAL", "VTT") else None
        o.swcs = "default" if self.swcs.get() else ""
        o.ext_gen_steps = None if self.ext.get() else ""
        o.save_project = self.save_proj.get()
        return o

    def _preview(self):
        s = self.app.session
        cmd = davinci.build_generate_cmd(self.picker.get() or "DVCfgCmd.exe", s.project.path,
                                         os.path.join(s.report_dir(), "GenerationReport.xml"), self.options())
        self.cmd.delete("1.0", "end")
        self.cmd.insert("1.0", davinci.format_cmd(cmd))

    def ok(self):
        if self.running:
            return
        exe = self.picker.get()
        if not os.path.isfile(exe):
            if not self.props_open.get():
                self._toggle_props()
            messagebox.showerror("DVCfgCmd", "Select a valid DVCfgCmd.exe", parent=self)
            return
        if not any(self.checked.values()):
            messagebox.showerror("Generate", "Select at least one generation step.", parent=self)
            return
        o = self.options()
        self.app.cfg["dvcfgcmd"] = exe
        self.app.cfg.setdefault("gen_modules", {})[os.path.normcase(self.app.session.project.path)] = o.modules
        self.app.cfg.save()
        self.result = (exe, o, self.local_first.get())
        # like DaVinci the dialog stays open and shows the progress of every generator
        self.app.start_generation(exe, o, self.local_first.get(), self)

    def cancel(self):
        if self.running:
            if messagebox.askyesno("Generate", "Cancel the running generation?", parent=self):
                self.app.cancel_davinci()
            return
        self.destroy()

    # ------------------------------------------------------------ progress
    def start_running(self):
        self.running = True
        self.ok_btn.state(["disabled"])
        self.cancel_btn.configure(text="Cancel")
        for iid in self.items:
            if self.checked[iid]:
                self.tv.item(iid, values=("", "", ""))
        for gid in self.groups.values():
            self.tv.item(gid, open=True)
        self.status.configure(text="Generation running…")

    def _match(self, gen_name):
        """Tree items of the modules produced by DaVinci generator *gen_name* (e.g. Eth_30_Tc3xx)."""
        out = []
        for iid, dref in self.items.items():
            mod = self.tv.item(iid, "text").split(" ", 1)[-1].split(":", 1)[0]
            if gen_name == mod or gen_name.startswith(mod + "_") or dref.rsplit("/", 1)[-1] == gen_name:
                out.append(iid)
        return out

    def on_progress(self, phase, event, text):
        col = {"Calculation": "calc", "Validation": "val", "Generation": "gen"}.get(phase)
        if col is None:
            return
        names = text.split(chr(9), 1)[0]   # "Generator: Det<TAB><TAB>MICROSAR Det Generator"
        names = names.split(":", 1)[-1] if ":" in names else names
        mark = {"MODULE_STARTED": "▶ running", "MODULE_FINISHED": "✔", "MODULE_SUSPENDED": "… suspended"}.get(
            event, event.lower())
        for n in [x.strip() for x in names.split(",") if x.strip()]:
            for iid in self._match(n):
                self.tv.set(iid, col, mark)
                self.tv.see(iid)
        self.status.configure(text=f"{phase}: {event.replace('_', ' ').lower()}  {names.strip()}")

    def finished(self, rc, report):
        self.running = False
        sym = {"SUCCESSFUL": "✔", "FAILED": "✖ failed", "NONE": "–", "CANCELED": "canceled"}
        cols = {"CALCULATION": "calc", "VALIDATION": "val", "GENERATION": "gen"}
        for g in report.generation:
            for iid in self._match(g.name):
                for t, st in g.phases:
                    if t in cols:
                        self.tv.set(iid, cols[t], sym.get(st, st))
        self.status.configure(text=f"Finished: {report.process_result or 'no report'} (exit code {rc}) — "
                                   f"details in the Generation Result view")
        self.ok_btn.state(["!disabled"])
        self.ok_btn.configure(text="Generate")
        self.cancel_btn.configure(text="Close")


class SettingsDialog(_Dialog):
    def __init__(self, master, app):
        super().__init__(master, "Settings", "Tool Settings",
                         "DaVinci Configurator command line, rule plug-ins and editor behaviour.", app.icons.settings)
        self.app = app
        b = self.body
        ttk.Label(b, text="DVCfgCmd.exe:").grid(row=0, column=0, sticky="w")
        self.picker = DvCfgCmdPicker(b, app)
        self.picker.grid(row=0, column=1, sticky="ew")
        self.picker.info.grid(row=1, column=1, sticky="w")
        ttk.Label(b, text="Rule folders:\n(one per line)").grid(row=2, column=0, sticky="nw", pady=6)
        self.rules = tk.Text(b, height=4, width=80)
        self.rules.grid(row=2, column=1, sticky="ew", pady=6)
        self.rules.insert("1.0", "\n".join(app.cfg.get("plugin_dirs", [])))
        self.backup = tk.BooleanVar(value=app.cfg.get("backup_on_save", True))
        ttk.Checkbutton(b, text="Write .bak backup when saving ARXML files", variable=self.backup).grid(
            row=3, column=1, sticky="w")
        self.val_load = tk.BooleanVar(value=app.cfg.get("validate_on_load", True))
        ttk.Checkbutton(b, text="Validate after loading a project", variable=self.val_load).grid(
            row=4, column=1, sticky="w")
        if app.session.project:
            p = app.session.project
            info = ttk.LabelFrame(b, text="Project (read-only)", padding=6)
            info.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(10, 0))
            for i, (k, v) in enumerate((("Project file", p.path), ("SIP", p.sip_dir),
                                        ("Derivative / Compiler", f"{p.derivative} / {p.compiler}"),
                                        ("Module files (GenData)", p.gendata_dir), ("ECUC", p.ecuc_dir))):
                ttk.Label(info, text=k + ":", foreground="#777").grid(row=i, column=0, sticky="w", padx=(0, 10))
                e = ttk.Entry(info, width=90)
                e.insert(0, v)
                e.configure(state="readonly")
                e.grid(row=i, column=1, sticky="ew", pady=1)
            info.columnconfigure(1, weight=1)
        # definition locations (a project copied to another machine may point to a missing SIP)
        dfr = ttk.LabelFrame(b, text="Definitions (BSWMD)", padding=6)
        dfr.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        s = app.session
        self.sip = tk.StringVar(value=(s.sip_override(s.project.path) or "") if s.project else "")
        self.extra_bswmd = tk.StringVar(value=";".join(app.cfg.get("extra_bswmd", [])))
        self.std_dir = tk.StringVar(value=app.cfg.get("std_def_dir", ""))
        rows = (("SIP folder for this project\n(empty = as in the .dpa):", self.sip),
                ("Extra BSWMD folders (;):", self.extra_bswmd),
                ("AUTOSAR StandardDefinition folder:", self.std_dir))
        for i, (lbl, var) in enumerate(rows):
            ttk.Label(dfr, text=lbl).grid(row=i, column=0, sticky="w", pady=2)
            ttk.Entry(dfr, textvariable=var, width=80).grid(row=i, column=1, sticky="ew", pady=2)
            ttk.Button(dfr, text="…", width=3, command=lambda v=var: self._pick_dir(v)).grid(row=i, column=2, padx=2)
        if s.defs is not None:
            ttk.Label(dfr, text=s.definition_status(), foreground="#555", wraplength=760).grid(
                row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        dfr.columnconfigure(1, weight=1)
        b.columnconfigure(1, weight=1)
        self.buttons("OK")

    def _pick_dir(self, var):
        d = filedialog.askdirectory(parent=self)
        if not d:
            return
        d = os.path.normpath(d)
        cur = var.get().strip()
        var.set(cur + ";" + d if (var is self.extra_bswmd and cur) else d)

    def ok(self):
        c = self.app.cfg
        c["dvcfgcmd"] = self.picker.get()
        c["plugin_dirs"] = [l.strip() for l in self.rules.get("1.0", "end").splitlines() if l.strip()]
        c["backup_on_save"] = self.backup.get()
        c["validate_on_load"] = self.val_load.get()
        old_defs = (c.get("extra_bswmd", []), c.get("std_def_dir", ""))
        c["extra_bswmd"] = [x.strip() for x in self.extra_bswmd.get().split(";") if x.strip()]
        c["std_def_dir"] = self.std_dir.get().strip()
        reload = old_defs != (c["extra_bswmd"], c["std_def_dir"])
        s = self.app.session
        if s.project:
            new = self.sip.get().strip()
            if new != (s.sip_override(s.project.path) or ""):
                c.setdefault("sip_override", {})[os.path.normcase(os.path.abspath(s.project.path))] = new
                reload = True
        c.save()
        self.result = "reload" if reload else True
        self.destroy()


class AddContainerDialog(_Dialog):
    def __init__(self, master, app, parent_el, cdefs, preset=None):
        super().__init__(master, "Add Container", "Create Container",
                         f"Create new container(s) below {app.session.model.path_of(parent_el)}.",
                         app.icons.add)
        self.app = app
        b = self.body
        ttk.Label(b, text="Definition:").grid(row=0, column=0, sticky="w", pady=4)
        self.cdefs = cdefs
        self.labels = [f"{c.label}   ({c.multiplicity_str()})" for c in cdefs]
        self.dvar = tk.StringVar()
        cb = ttk.Combobox(b, textvariable=self.dvar, values=self.labels, state="readonly", width=60)
        cb.grid(row=0, column=1, sticky="ew", pady=4)
        cb.current(cdefs.index(preset) if preset in cdefs else 0)
        cb.bind("<<ComboboxSelected>>", lambda e: self._suggest_name())
        ttk.Label(b, text="Short name:").grid(row=1, column=0, sticky="w")
        self.nvar = tk.StringVar()
        e = ttk.Entry(b, textvariable=self.nvar, width=60)
        e.grid(row=1, column=1, sticky="ew")
        self.count = tk.IntVar(value=1)
        ttk.Label(b, text="Count:").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Spinbox(b, from_=1, to=500, textvariable=self.count, width=6).grid(row=2, column=1, sticky="w", pady=4)
        self.defaults = tk.BooleanVar(value=True)
        ttk.Checkbutton(b, text="Create mandatory sub-containers and parameters with default values",
                        variable=self.defaults).grid(row=3, column=1, sticky="w")
        self.recommended = tk.BooleanVar(value=True)
        ttk.Checkbutton(b, text="Apply the recommended configuration of the SIP (like DaVinci)",
                        variable=self.recommended).grid(row=4, column=1, sticky="w")
        self.parent_el = parent_el
        self._suggest_name()
        e.focus_set()
        e.bind("<Return>", lambda ev: self.ok())
        b.columnconfigure(1, weight=1)
        self.buttons("Finish")

    def _cdef(self):
        return self.cdefs[self.labels.index(self.dvar.get())]

    def _suggest_name(self):
        self.nvar.set(self.app.session.model.unique_name(self.parent_el, self._cdef().name))

    def ok(self):
        name = self.nvar.get().strip()
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]{0,127}$", name):
            messagebox.showerror("Add container", "Invalid short name", parent=self)
            return
        self.result = (self._cdef(), name, max(1, self.count.get()), self.defaults.get(), self.recommended.get())
        self.destroy()


class OpenFilesDialog(_Dialog):
    """Open loose ECUC arxml files (no .dpa) with BSWMD definitions from a SIP folder."""

    def __init__(self, master, app):
        super().__init__(master, "Open ECUC Files", "Open ECUC Files",
                         "Open ECUC ARXML files without a DaVinci project. The module definitions (BSWMD) "
                         "are read from the SIP folder.", app.icons.open)
        self.app = app
        b = self.body
        ttk.Label(b, text="ECUC files:").grid(row=0, column=0, sticky="nw")
        self.files = tk.Listbox(b, height=8, width=90)
        self.files.grid(row=0, column=1, sticky="ew")
        ttk.Button(b, text="Add…", command=self._add).grid(row=0, column=2, sticky="n", padx=4)
        ttk.Label(b, text="SIP folder:").grid(row=1, column=0, sticky="w", pady=4)
        self.sip = tk.StringVar(value=app.cfg.get("last_sip", ""))
        ttk.Entry(b, textvariable=self.sip, width=90).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Button(b, text="…", command=lambda: self.sip.set(filedialog.askdirectory() or self.sip.get())).grid(
            row=1, column=2, padx=4)
        ttk.Label(b, text="Extra BSWMD folder:").grid(row=2, column=0, sticky="w")
        self.extra = tk.StringVar()
        ttk.Entry(b, textvariable=self.extra, width=90).grid(row=2, column=1, sticky="ew")
        ttk.Button(b, text="…", command=lambda: self.extra.set(filedialog.askdirectory() or self.extra.get())).grid(
            row=2, column=2, padx=4)
        b.columnconfigure(1, weight=1)
        self.buttons("Open")

    def _add(self):
        for f in filedialog.askopenfilenames(filetypes=[("ARXML", "*.arxml")]):
            self.files.insert("end", os.path.normpath(f))

    def ok(self):
        files = list(self.files.get(0, "end"))
        if not files or not os.path.isdir(self.sip.get()):
            messagebox.showerror("Open", "Select at least one file and a valid SIP folder", parent=self)
            return
        self.app.cfg["last_sip"] = self.sip.get()
        self.result = (files, self.sip.get(), [self.extra.get()] if self.extra.get() else [])
        self.destroy()


class ModulesDialog(_Dialog):
    """Project Settings › Modules: add module configurations from the SIP's BSWMD or remove them."""

    def __init__(self, master, app):
        super().__init__(master, "Modules", "Modules",
                         "Activate BSW modules for this project. A new module configuration is created from its "
                         "BSWMD with the mandatory containers, default values and the SIP's recommended and "
                         "pre-configuration — like DaVinci's Project Settings › Modules.", app.icons.module)
        self.app = app
        b = self.body
        cols = ttk.Frame(b)
        cols.pack(fill="both", expand=True)
        left = ttk.LabelFrame(cols, text="Configured modules", padding=4)
        left.pack(side="left", fill="both", expand=True)
        mid = ttk.Frame(cols, padding=6)
        mid.pack(side="left", fill="y")
        right = ttk.LabelFrame(cols, text="Available module definitions (SIP / BSWMD)", padding=4)
        right.pack(side="left", fill="both", expand=True)

        self.cur = ttk.Treeview(left, columns=("def", "variant"), height=18, selectmode="extended")
        for c, t, w in (("#0", "Module", 140), ("def", "Definition", 230), ("variant", "Variant", 150)):
            self.cur.heading(c, text=t, anchor="w")
            self.cur.column(c, width=w)
        self.cur.pack(fill="both", expand=True)
        self.avail = ttk.Treeview(right, columns=("impl",), height=18, selectmode="extended")
        for c, t, w in (("#0", "Definition", 280), ("impl", "BSW implementation", 220)):
            self.avail.heading(c, text=t, anchor="w")
            self.avail.column(c, width=w)
        self.avail.pack(fill="both", expand=True)
        self.avail.bind("<Double-1>", lambda e: self.add())
        ttk.Button(mid, text="◀  Add", width=12, command=self.add).pack(pady=(80, 4))
        ttk.Button(mid, text="Remove  ▶", width=12, command=self.remove).pack(pady=4)

        opt = ttk.Frame(b)
        opt.pack(fill="x", pady=(8, 0))
        ttk.Label(opt, text="Short name:").pack(side="left")
        self.name = tk.StringVar()
        ttk.Entry(opt, textvariable=self.name, width=24).pack(side="left", padx=4)
        ttk.Label(opt, text="Configuration variant:").pack(side="left", padx=(12, 0))
        self.variant = tk.StringVar()
        self.var_cb = ttk.Combobox(opt, textvariable=self.variant, width=30, state="readonly")
        self.var_cb.pack(side="left", padx=4)
        self.templates = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="Apply recommended / pre-configuration", variable=self.templates).pack(
            side="left", padx=12)
        self.avail.bind("<<TreeviewSelect>>", lambda e: self._on_avail())
        self.info = ttk.Label(b, text="", foreground="#555")
        self.info.pack(fill="x", pady=(4, 0))
        self.buttons("Close")
        self.ok_btn.pack_forget()
        self.cancel_btn.configure(text="Close")
        self.geometry("1100x640")
        self.refresh()

    def refresh(self):
        s = self.app.session
        self.cur.delete(*self.cur.get_children(""))
        self.avail.delete(*self.avail.get_children(""))
        used = {}
        for m in s.model.modules:
            d = definition_ref(m)
            used[d] = used.get(d, 0) + 1
            self.cur.insert("", "end", iid=f"m{id(m)}", text=arxml.short_name(m), image=self.app.icons.module,
                            values=(d, arxml.text(m, "IMPLEMENTATION-CONFIG-VARIANT", "")))
        self._mods = {f"m{id(m)}": m for m in s.model.modules}
        groups = {}
        order = [d for d, _ in DOMAINS] + ["Other"]
        paths = sorted(s.defs.module_paths(), key=lambda p: (order.index(domain_of(p.rsplit("/", 1)[-1])),
                                                             p.lower()))
        self._defs = {}
        n = 0
        for p in paths:
            mdef_upper = None
            try:
                mdef = s.defs.module(p)
                mdef_upper = mdef.upper if mdef is not None else 1
            except Exception:
                mdef = None
            if mdef is None or (used.get(p, 0) >= (mdef_upper or 1)):
                continue
            dom = domain_of(p.rsplit("/", 1)[-1])
            if dom not in groups:
                groups[dom] = self.avail.insert("", "end", text=dom, open=True)
            iid = self.avail.insert(groups[dom], "end", text=p, image=self.app.icons.module,
                                    values=(mdef.impl.path if mdef.impl else "(no BSW implementation)",))
            self._defs[iid] = mdef
            n += 1
        self.info.configure(text=f"{len(s.model.modules)} configured, {n} more module definitions available in "
                                 f"{s.defs.sip_dir or 'the loaded BSWMD folders'}")

    def _on_avail(self):
        sel = [i for i in self.avail.selection() if i in self._defs]
        if not sel:
            return
        mdef = self._defs[sel[0]]
        self.name.set(mdef.name)
        from ..configure import default_variant
        vals = mdef.supported_variants or ["VARIANT-PRE-COMPILE"]
        self.var_cb.configure(values=vals)
        self.variant.set(default_variant(mdef))

    def add(self):
        from ..configure import build_module
        s = self.app.session
        sel = [i for i in self.avail.selection() if i in self._defs]
        if not sel:
            return
        added = []
        for iid in sel:
            mdef = self._defs[iid]
            name = self.name.get().strip() if len(sel) == 1 and self.name.get().strip() else mdef.name
            if s.model.module_by_name(name) is not None:
                messagebox.showerror("Modules", f"A module named '{name}' already exists.", parent=self)
                continue
            if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]{0,127}$", name):
                messagebox.showerror("Modules", "Invalid short name", parent=self)
                continue
            variant = self.variant.get() if len(sel) == 1 and self.variant.get() else None
            el = build_module(s.model, s.defs, mdef, name, variant, apply_templates=self.templates.get())
            s.model.add_module(el)
            added.append(name)
        if added:
            self.app.modules_changed(f"Added module(s): {', '.join(added)}")
            self.refresh()

    def remove(self):
        s = self.app.session
        mods = [self._mods[i] for i in self.cur.selection() if i in self._mods]
        if not mods:
            return
        names = ", ".join(arxml.short_name(m) for m in mods)
        refs = 0
        for m in mods:
            mp = s.model.path_of(m)
            refs += sum(len(v) for k, v in s.model.ref_index().items()
                        if (k == mp or k.startswith(mp + "/")) and s.model.module_of(v[0]) is not m)
        msg = f"Remove the module configuration(s) {names}?"
        if refs:
            msg += f"\n\n{refs} reference(s) from other modules point into them and will become dangling."
        if not messagebox.askyesno("Modules", msg, parent=self):
            return
        for m in mods:
            s.model.remove_module(m)
        self.app.modules_changed(f"Removed module(s): {names}")
        self.refresh()
