"""Dialogs: Generate, Settings, Add container, Open files."""
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import arxml, davinci
from ..project import definition_ref


class _Dialog(tk.Toplevel):
    def __init__(self, master, title):
        super().__init__(master)
        self.title(title)
        self.transient(master)
        self.result = None
        self.body = ttk.Frame(self, padding=8)
        self.body.pack(fill="both", expand=True)
        self.bind("<Escape>", lambda e: self.destroy())

    def buttons(self, ok_text="OK"):
        bar = ttk.Frame(self, padding=(8, 0, 8, 8))
        bar.pack(fill="x")
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bar, text=ok_text, command=self.ok).pack(side="right", padx=4)

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
        super().__init__(master, "Generate code with DaVinci Configurator")
        self.app = app
        s = app.session
        b = self.body
        ttk.Label(b, text="DVCfgCmd.exe").grid(row=0, column=0, sticky="w")
        self.picker = DvCfgCmdPicker(b, app)
        self.picker.grid(row=0, column=1, sticky="ew")
        self.picker.info.grid(row=1, column=1, sticky="w", pady=(0, 6))

        ttk.Label(b, text="Modules").grid(row=2, column=0, sticky="nw")
        mf = ttk.Frame(b)
        mf.grid(row=2, column=1, sticky="nsew")
        self.lb = tk.Listbox(mf, selectmode="extended", height=18, exportselection=False)
        ys = ttk.Scrollbar(mf, orient="vertical", command=self.lb.yview)
        self.lb.configure(yscrollcommand=ys.set)
        self.lb.pack(side="left", fill="both", expand=True)
        ys.pack(side="left", fill="y")
        side = ttk.Frame(mf)
        side.pack(side="left", fill="y", padx=4)
        ttk.Button(side, text="All", command=lambda: self.lb.select_set(0, "end")).pack(fill="x")
        ttk.Button(side, text="None", command=lambda: self.lb.select_clear(0, "end")).pack(fill="x", pady=2)
        ttk.Button(side, text="MICROSAR only", command=self._microsar).pack(fill="x")
        ttk.Button(side, text="Dirty modules", command=self._dirty).pack(fill="x", pady=2)
        self.mods = []
        for m in s.model.modules:
            d = definition_ref(m)
            self.mods.append(d)
            self.lb.insert("end", f"{arxml.short_name(m):<18} {d}")
        prev = app.cfg.get("gen_modules", {}).get(os.path.normcase(s.project.path), [])
        for i, d in enumerate(self.mods):
            if d in prev:
                self.lb.select_set(i)

        opt = ttk.LabelFrame(b, text="Options", padding=6)
        opt.grid(row=3, column=1, sticky="ew", pady=6)
        self.all_modules = tk.BooleanVar(value=not prev)
        ttk.Checkbutton(opt, text="Generate all modules of the project (ignore selection)",
                        variable=self.all_modules).grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(opt, text="Target:").grid(row=1, column=0, sticky="w")
        self.gen_type = tk.StringVar(value="(project setting)")
        ttk.Combobox(opt, textvariable=self.gen_type, values=("(project setting)", "REAL", "VTT"), width=16,
                     state="readonly").grid(row=1, column=1, sticky="w")
        self.swcs = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text="Generate SWC templates (default set of the .dpa)",
                        variable=self.swcs).grid(row=2, column=0, columnspan=3, sticky="w")
        self.ext = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="Run external generation steps (default)", variable=self.ext).grid(
            row=3, column=0, columnspan=3, sticky="w")
        self.save_proj = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text="--saveProject (persist changes made by generator calculation)",
                        variable=self.save_proj).grid(row=4, column=0, columnspan=3, sticky="w")
        self.local_first = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="Stop if local validation reports errors in selected modules",
                        variable=self.local_first).grid(row=5, column=0, columnspan=3, sticky="w")

        ttk.Label(b, text="Command").grid(row=4, column=0, sticky="nw")
        self.cmd = tk.Text(b, height=3, wrap="word", font=("Consolas", 8))
        self.cmd.grid(row=4, column=1, sticky="ew")
        ttk.Label(b, text="Note: MCAL modules are generated through EB tresos and need a tresos license; "
                          "MemMap needs ..\\Board\\AsrCommon\\inc\\MemMap_User.h.",
                  foreground="#8a4b00", wraplength=700).grid(row=5, column=1, sticky="w", pady=4)
        b.columnconfigure(1, weight=1)
        b.rowconfigure(2, weight=1)
        for w in (self.lb,):
            w.bind("<<ListboxSelect>>", lambda e: self._preview())
        for v in (self.all_modules, self.swcs, self.ext, self.save_proj, self.gen_type):
            v.trace_add("write", lambda *a: self._preview())
        self.picker.var.trace_add("write", lambda *a: self._preview())
        self.buttons("Generate")
        self.geometry("900x720")
        self._preview()

    def _microsar(self):
        self.lb.select_clear(0, "end")
        for i, d in enumerate(self.mods):
            if d.startswith("/MICROSAR/") and "MemMap" not in d:
                self.lb.select_set(i)
        self.all_modules.set(False)

    def _dirty(self):
        s = self.app.session
        dirty = {id(f) for f in s.model.dirty_files()}
        self.lb.select_clear(0, "end")
        for i, m in enumerate(s.model.modules):
            if id(s.model.module_file.get(m)) in dirty:
                self.lb.select_set(i)
        self.all_modules.set(False)

    def options(self):
        o = davinci.GenerateOptions()
        if not self.all_modules.get():
            o.modules = [self.mods[i] for i in self.lb.curselection()]
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
        exe = self.picker.get()
        if not os.path.isfile(exe):
            messagebox.showerror("DVCfgCmd", "Select a valid DVCfgCmd.exe", parent=self)
            return
        o = self.options()
        if not self.all_modules.get() and not o.modules:
            messagebox.showerror("Generate", "Select at least one module or tick 'all modules'.", parent=self)
            return
        self.app.cfg["dvcfgcmd"] = exe
        self.app.cfg.setdefault("gen_modules", {})[os.path.normcase(self.app.session.project.path)] = o.modules
        self.app.cfg.save()
        self.result = (exe, o, self.local_first.get())
        self.destroy()


class SettingsDialog(_Dialog):
    def __init__(self, master, app):
        super().__init__(master, "Settings")
        self.app = app
        b = self.body
        ttk.Label(b, text="DVCfgCmd.exe").grid(row=0, column=0, sticky="w")
        self.picker = DvCfgCmdPicker(b, app)
        self.picker.grid(row=0, column=1, sticky="ew")
        self.picker.info.grid(row=1, column=1, sticky="w")
        ttk.Label(b, text="Extra rule folders\n(one per line)").grid(row=2, column=0, sticky="nw", pady=6)
        self.rules = tk.Text(b, height=4, width=80)
        self.rules.grid(row=2, column=1, sticky="ew", pady=6)
        self.rules.insert("1.0", "\n".join(app.cfg.get("plugin_dirs", [])))
        self.backup = tk.BooleanVar(value=app.cfg.get("backup_on_save", True))
        ttk.Checkbutton(b, text="Write .bak backup when saving ARXML files", variable=self.backup).grid(
            row=3, column=1, sticky="w")
        self.val_load = tk.BooleanVar(value=app.cfg.get("validate_on_load", True))
        ttk.Checkbutton(b, text="Validate after loading a project", variable=self.val_load).grid(
            row=4, column=1, sticky="w")
        b.columnconfigure(1, weight=1)
        self.buttons()

    def ok(self):
        c = self.app.cfg
        c["dvcfgcmd"] = self.picker.get()
        c["plugin_dirs"] = [l.strip() for l in self.rules.get("1.0", "end").splitlines() if l.strip()]
        c["backup_on_save"] = self.backup.get()
        c["validate_on_load"] = self.val_load.get()
        c.save()
        self.result = True
        self.destroy()


class AddContainerDialog(_Dialog):
    def __init__(self, master, app, parent_el, cdefs, preset=None):
        super().__init__(master, "Add container")
        self.app = app
        b = self.body
        ttk.Label(b, text="Parent:").grid(row=0, column=0, sticky="w")
        ttk.Label(b, text=app.session.model.path_of(parent_el)).grid(row=0, column=1, sticky="w")
        ttk.Label(b, text="Definition:").grid(row=1, column=0, sticky="w", pady=4)
        self.cdefs = cdefs
        self.dvar = tk.StringVar()
        names = [f"{c.name}   ({c.multiplicity_str()})" for c in cdefs]
        cb = ttk.Combobox(b, textvariable=self.dvar, values=names, state="readonly", width=60)
        cb.grid(row=1, column=1, sticky="ew", pady=4)
        idx = cdefs.index(preset) if preset in cdefs else 0
        cb.current(idx)
        cb.bind("<<ComboboxSelected>>", lambda e: self._suggest_name())
        ttk.Label(b, text="Short name:").grid(row=2, column=0, sticky="w")
        self.nvar = tk.StringVar()
        e = ttk.Entry(b, textvariable=self.nvar, width=60)
        e.grid(row=2, column=1, sticky="ew")
        self.count = tk.IntVar(value=1)
        ttk.Label(b, text="Count:").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Spinbox(b, from_=1, to=500, textvariable=self.count, width=6).grid(row=3, column=1, sticky="w", pady=4)
        self.defaults = tk.BooleanVar(value=True)
        ttk.Checkbutton(b, text="Create mandatory sub-containers and parameters with default values",
                        variable=self.defaults).grid(row=4, column=1, sticky="w")
        self.parent_el = parent_el
        self._suggest_name()
        e.focus_set()
        e.bind("<Return>", lambda ev: self.ok())
        self.buttons("Add")

    def _cdef(self):
        return self.cdefs[[f"{c.name}   ({c.multiplicity_str()})" for c in self.cdefs].index(self.dvar.get())]

    def _suggest_name(self):
        self.nvar.set(self.app.session.model.unique_name(self.parent_el, self._cdef().name))

    def ok(self):
        name = self.nvar.get().strip()
        import re
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]{0,127}$", name):
            messagebox.showerror("Add container", "Invalid short name", parent=self)
            return
        self.result = (self._cdef(), name, max(1, self.count.get()), self.defaults.get())
        self.destroy()


class OpenFilesDialog(_Dialog):
    """Open loose ECUC arxml files (no .dpa) with BSWMD definitions from a SIP folder."""

    def __init__(self, master, app):
        super().__init__(master, "Open ECUC ARXML files")
        self.app = app
        b = self.body
        ttk.Label(b, text="ECUC files").grid(row=0, column=0, sticky="nw")
        self.files = tk.Listbox(b, height=8, width=90)
        self.files.grid(row=0, column=1, sticky="ew")
        ttk.Button(b, text="Add…", command=self._add).grid(row=0, column=2, sticky="n")
        ttk.Label(b, text="SIP folder").grid(row=1, column=0, sticky="w", pady=4)
        self.sip = tk.StringVar(value=app.cfg.get("last_sip", ""))
        ttk.Entry(b, textvariable=self.sip, width=90).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Button(b, text="…", command=lambda: self.sip.set(filedialog.askdirectory() or self.sip.get())).grid(
            row=1, column=2)
        ttk.Label(b, text="Extra BSWMD folder").grid(row=2, column=0, sticky="w")
        self.extra = tk.StringVar()
        ttk.Entry(b, textvariable=self.extra, width=90).grid(row=2, column=1, sticky="ew")
        ttk.Button(b, text="…", command=lambda: self.extra.set(filedialog.askdirectory() or self.extra.get())).grid(
            row=2, column=2)
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
