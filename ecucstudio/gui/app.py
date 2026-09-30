"""EcucStudio main window, laid out like DaVinci Configurator 5."""
from __future__ import annotations

import copy
import os
import queue
import re
import subprocess
import threading
import time
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, simpledialog, ttk

from .. import __version__, arxml, davinci
from ..project import MODULE_TAG, container_children, definition_ref, parse_object_ref
from ..session import Session
from ..settings import Settings
from ..validation import Severity
from .console import ConsoleView, FindView, GenerationView
from .dialogs import AddContainerDialog, GenerateDialog, ModulesDialog, OpenFilesDialog, SettingsDialog
from .editor import BasicEditor
from .navigator import NavigatorView
from .properties import PropertiesPanel
from .theme import COLORS, Icons, init_style
from .validation_view import ValidationView
from .widgets import ToolButton, ToolSeparator, ViewStack

APP_NAME = "EcucStudio"


class App(tk.Tk):
    def __init__(self, dpa=None):
        super().__init__()
        self.report_callback_exception = self._on_tk_error
        self.cfg = Settings()
        self.session = Session(self.cfg)
        self.busy = False
        self.dv_run = None
        self._q = queue.Queue()
        self.history, self.hist_pos, self._nav = [], -1, False
        self._max = None
        init_style(self)
        self.icons = Icons()
        self.title(APP_NAME)
        self.geometry(self.cfg.get("geometry", "1500x900"))
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build_menu()
        self._build_toolbar()
        self._build_status()
        self._build_body()
        self._bind_keys()
        self.after(100, self._poll)
        if dpa:
            self.after(200, lambda: self.open_project(dpa))

    # ================================================================== layout
    def _build_menu(self):
        mb = tk.Menu(self)
        ic = self.icons
        f = tk.Menu(mb, tearoff=False)
        f.add_command(label="Open Project…", accelerator="Ctrl+O", image=ic.open, compound="left",
                      command=self.open_project_dialog)
        f.add_command(label="Open ECUC Files…", command=self.open_files_dialog)
        self.recent_menu = tk.Menu(f, tearoff=False)
        f.add_cascade(label="Recent Projects", menu=self.recent_menu)
        f.add_separator()
        f.add_command(label="Save", accelerator="Ctrl+S", image=ic.save, compound="left", command=self.save)
        f.add_command(label="Reload Project", command=self.reload)
        f.add_separator()
        f.add_command(label="Exit", command=self.on_close)
        mb.add_cascade(label="File", menu=f)
        e = tk.Menu(mb, tearoff=False)
        e.add_command(label="Undo", accelerator="Ctrl+Z", image=ic.undo, compound="left", command=self.undo)
        e.add_command(label="Redo", accelerator="Ctrl+Y", image=ic.redo, compound="left", command=self.redo)
        e.add_separator()
        e.add_command(label="Find…", accelerator="Ctrl+F", image=ic.find, compound="left", command=self.show_find)
        mb.add_cascade(label="Edit", menu=e)
        n = tk.Menu(mb, tearoff=False)
        n.add_command(label="Last Editor", accelerator="Alt+Left", image=ic.back, compound="left",
                      command=self.nav_back)
        n.add_command(label="Next Editor", accelerator="Alt+Right", image=ic.forward, compound="left",
                      command=self.nav_forward)
        n.add_command(label="Go to Path…", accelerator="Ctrl+L", command=self.goto_path_dialog)
        mb.add_cascade(label="Navigate", menu=n)
        v = tk.Menu(mb, tearoff=False)
        v.add_command(label="Basic Editor", image=ic.basic, compound="left", command=lambda: self.open_editor(None))
        v.add_command(label="Configuration Editors", image=ic.editors, compound="left",
                      command=lambda: self.left.select("nav"))
        v.add_command(label="Properties", image=ic.properties, compound="left",
                      command=lambda: self.props_stack.select("props"))
        for key, label, icon in (("val", "Validation", ic.warning), ("find", "Find", ic.find),
                                 ("gen", "Generation Result", ic.genresult), ("console", "Console", ic.console)):
            v.add_command(label=label, image=icon, compound="left", command=lambda k=key: self.bottom.select(k))
        mb.add_cascade(label="View", menu=v)
        p = tk.Menu(mb, tearoff=False)
        p.add_command(label="Validate", accelerator="F5", image=ic.validate, compound="left", command=self.validate)
        p.add_command(label="On-demand Validation (DaVinci)", image=ic.generate, compound="left",
                      command=self.davinci_validate)
        p.add_command(label="Solve All", image=ic.solve, compound="left", command=lambda: self.val.solve_all())
        p.add_separator()
        p.add_command(label="Generate…", accelerator="Ctrl+G", image=ic.generate, compound="left",
                      command=self.generate)
        p.add_command(label="Open in DaVinci Configurator", command=self.open_in_davinci)
        p.add_separator()
        p.add_command(label="Modules…", image=ic.module, compound="left", command=self.modules_dialog)
        p.add_command(label="Project Settings…", image=ic.settings, compound="left", command=self.settings_dialog)
        mb.add_cascade(label="Project", menu=p)
        h = tk.Menu(mb, tearoff=False)
        h.add_command(label="About", command=lambda: messagebox.showinfo(
            APP_NAME, f"{APP_NAME} {__version__}\nECUC configurator with DaVinci compatible validation\n"
                      f"and DaVinci Configurator command line generation.\n\nLog: "
                      f"{os.path.join(os.environ.get('LOCALAPPDATA', ''), 'EcucStudio', 'ecucstudio.log')}"))
        mb.add_cascade(label="Help", menu=h)
        self.config(menu=mb)
        self._refresh_recent()

    def _refresh_recent(self):
        self.recent_menu.delete(0, "end")
        for pth in self.cfg.get("recent", []):
            self.recent_menu.add_command(label=pth, command=lambda p=pth: self.open_project(p))

    def _build_toolbar(self):
        tb = tk.Frame(self, background=COLORS["bg"])
        tb.pack(fill="x", padx=2, pady=(2, 0))
        ic = self.icons
        items = [(ic.open, self.open_project_dialog, "Open Project (Ctrl+O)"),
                 (ic.save, self.save, "Save (Ctrl+S)"), None,
                 (ic.undo, self.undo, "Undo (Ctrl+Z)"), (ic.redo, self.redo, "Redo (Ctrl+Y)"), None,
                 (ic.back, self.nav_back, "Last Editor (Alt+Left)"),
                 (ic.forward, self.nav_forward, "Next Editor (Alt+Right)"), None,
                 (ic.validate, self.validate, "Validate (F5)"),
                 (ic.solve, lambda: self.val.solve_all(), "Solve All"), None,
                 (ic.generate, self.generate, "Generate (Ctrl+G)"),
                 (ic.settings, self.settings_dialog, "Project Settings"), None,
                 (ic.find, self.show_find, "Find (Ctrl+F)")]
        for it in items:
            if it is None:
                ToolSeparator(tb).pack(side="left", fill="y", padx=4, pady=3)
            else:
                ToolButton(tb, it[0], it[1], it[2]).pack(side="left")

    def _build_body(self):
        self.vpane = ttk.PanedWindow(self, orient="vertical")
        self.vpane.pack(fill="both", expand=True, padx=3, pady=3)
        self.hpane = ttk.PanedWindow(self.vpane, orient="horizontal")
        self.vpane.add(self.hpane, weight=3)
        # left: Configuration Editors
        self.left = ViewStack(self.hpane, self)
        self.nav = NavigatorView(self.left.body, self)
        self.left.add("nav", "Configuration Editors", self.icons.editors, self.nav)
        self.hpane.add(self.left, weight=1)
        # editor area
        self.editors = ViewStack(self.hpane, self, closable=True)
        self.editors.on_change = lambda k: self._on_editor_change()
        self.hpane.add(self.editors, weight=4)
        self._welcome = tk.Label(self.editors.body, text="Open a DaVinci project (File › Open Project…)",
                                 background=COLORS["view_bg"], foreground="#888", font=("Segoe UI", 11))
        self._welcome.pack(expand=True)
        # bottom: Properties | Validation, Find, Generation Result, Console
        self.bpane = ttk.PanedWindow(self.vpane, orient="horizontal")
        self.vpane.add(self.bpane, weight=1)
        self.props_stack = ViewStack(self.bpane, self)
        self.props = PropertiesPanel(self.props_stack.body, self)
        self.props_stack.add("props", "Properties", self.icons.properties, self.props)
        self.bpane.add(self.props_stack, weight=2)
        self.bottom = ViewStack(self.bpane, self)
        self.val = ValidationView(self.bottom.body, self)
        self.find = FindView(self.bottom.body, self)
        self.genview = GenerationView(self.bottom.body, self)
        self.console = ConsoleView(self.bottom.body, self)
        self.bottom.add("val", "Validation", self.icons.warning, self.val, select=True)
        self.bottom.add("find", "Find", self.icons.find, self.find)
        self.bottom.add("gen", "Generation Result", self.icons.genresult, self.genview)
        self.bottom.add("console", "Console", self.icons.console, self.console)
        self.bottom.select("val")
        self.bpane.add(self.bottom, weight=3)
        self.nav.rebuild()
        self.after(50, self._initial_sashes)

    def _initial_sashes(self):
        try:
            self.update_idletasks()
            h = self.vpane.winfo_height()
            w = self.hpane.winfo_width()
            self.vpane.sashpos(0, int(h * 0.68))
            self.hpane.sashpos(0, int(min(260, w * 0.2)))
            self.bpane.sashpos(0, int(self.bpane.winfo_width() * 0.38))
        except tk.TclError:
            pass

    def _build_status(self):
        sb = tk.Frame(self, background=COLORS["status_bg"], borderwidth=1, relief="sunken")
        sb.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="Ready")
        tk.Label(sb, textvariable=self.status_var, background=COLORS["status_bg"], anchor="w").pack(
            side="left", fill="x", expand=True, padx=4)
        self.phase = tk.Label(sb, text="", background=COLORS["status_bg"], anchor="w", padx=4,
                              image=self.icons.phase, compound="left", borderwidth=1, relief="groove")
        self.phase.pack(side="right", padx=2)
        self.counts_lbl = tk.Label(sb, text="", background=COLORS["status_bg"], borderwidth=1, relief="groove",
                                   padx=6)
        self.counts_lbl.pack(side="right", padx=2)
        self.progress = ttk.Progressbar(sb, length=180, mode="determinate", maximum=1.0)
        self.progress.pack(side="right", padx=4, pady=1)

    def _bind_keys(self):
        self.bind_all("<Control-s>", lambda e: self.save())
        self.bind_all("<Control-o>", lambda e: self.open_project_dialog())
        self.bind_all("<Control-z>", lambda e: self._key(e, self.undo))
        self.bind_all("<Control-y>", lambda e: self._key(e, self.redo))
        self.bind_all("<F5>", lambda e: self.validate())
        self.bind_all("<Control-g>", lambda e: self.generate())
        self.bind_all("<Control-f>", lambda e: self.show_find())
        self.bind_all("<Control-l>", lambda e: self.goto_path_dialog())
        self.bind_all("<Alt-Left>", lambda e: self.nav_back())
        self.bind_all("<Alt-Right>", lambda e: self.nav_forward())

    def _key(self, e, fn):
        if isinstance(e.widget, (tk.Entry, ttk.Entry, tk.Text, ttk.Combobox)):
            return
        fn()

    # ============================================================= utilities
    def _on_tk_error(self, exc, val, tb):
        from ..__main__ import log_path, write_log
        text = "".join(traceback.format_exception(exc, val, tb))
        write_log("GUI error:\n" + text)
        try:
            self.console.write(text, "ERROR")
        except Exception:
            pass
        messagebox.showerror(APP_NAME, f"{val}\n\nDetails: {log_path()}")

    def status(self, msg, frac=None):
        self.status_var.set(msg)
        if frac is not None:
            self.progress["value"] = frac

    def run_bg(self, work, done=None, msg="Working…", block=True):
        if block:
            if self.busy:
                self.status("Busy — please wait")
                return
            self.busy = True
            self.config(cursor="watch")
        self.status(msg, 0)

        def progress(m, frac=None):
            self._q.put(("progress", m, frac))

        def runner():
            try:
                res = work(progress)
                self._q.put(("done", done, res, None, block))
            except Exception as ex:
                self._q.put(("done", done, None, (ex, traceback.format_exc()), block))
        threading.Thread(target=runner, daemon=True).start()

    def call_gui(self, fn, *args):
        self._q.put(("call", fn, args))

    def _poll(self):
        try:
            while True:
                item = self._q.get_nowait()
                if item[0] == "progress":
                    self.status(item[1], item[2])
                elif item[0] == "call":
                    item[1](*item[2])
                elif item[0] == "done":
                    _k, done, res, err, block = item
                    if block:
                        self.busy = False
                        self.config(cursor="")
                    self.progress["value"] = 0
                    if err is not None:
                        self.status(f"Error: {err[0]}")
                        self.console.write(err[1], "ERROR")
                        messagebox.showerror(APP_NAME, f"{err[0]}\n\nDetails in the Console view.")
                    elif done:
                        done(res)
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def update_title(self):
        s = self.session
        name = s.project.name if s.project else ("ECUC files" if s.model else "")
        dirty = s.model is not None and s.model.is_dirty()
        path = s.project.path if s.project else ""
        self.title(f"{'*' if dirty else ''}{APP_NAME} - {name}" + (f".dpa  [{path}]" if path else ""))
        self.phase.configure(text=" PreCompile" if s.model else "")

    def all_results(self):
        return list(self.session.results) + list(self.session.dv_results)

    def update_counts(self):
        allr = self.all_results()
        e = sum(1 for r in allr if r.severity >= Severity.ERROR and not r.acknowledged)
        w = sum(1 for r in allr if r.severity == Severity.WARNING and not r.acknowledged)
        i = sum(1 for r in allr if r.severity < Severity.WARNING and not r.acknowledged)
        self.counts_lbl.configure(text=f"{e} errors, {w} warnings, {i} infos" if allr else "")

    def tree_marks_for(self, module_el):
        sev = None
        model = self.session.model
        for r in self.all_results():
            if r.acknowledged or r.element is None:
                continue
            if model.module_of(r.element) is module_el and (sev is None or r.severity > sev):
                sev = int(r.severity)
        return sev

    def toggle_maximize(self, stack):
        """Eclipse 'maximize view': give the stack (almost) all the space, click again to restore."""
        try:
            if self._max is not None:
                for pane, idx, pos in self._max:
                    pane.sashpos(idx, pos)
                self._max = None
                return
            saved = [(self.vpane, 0, self.vpane.sashpos(0)), (self.hpane, 0, self.hpane.sashpos(0)),
                     (self.bpane, 0, self.bpane.sashpos(0))]
            H, W = self.vpane.winfo_height(), self.hpane.winfo_width()
            if stack is self.editors:
                self.vpane.sashpos(0, H - 4)
                self.hpane.sashpos(0, 0)
            elif stack is self.left:
                self.vpane.sashpos(0, H - 4)
                self.hpane.sashpos(0, W - 4)
            elif stack is self.bottom:
                self.vpane.sashpos(0, 0)
                self.bpane.sashpos(0, 0)
            elif stack is self.props_stack:
                self.vpane.sashpos(0, 0)
                self.bpane.sashpos(0, self.bpane.winfo_width() - 4)
            self._max = saved
        except tk.TclError:
            pass

    # =============================================================== editors
    def current_editor(self):
        w = self.editors.widget(self.editors.current) if self.editors.current else None
        return w if isinstance(w, BasicEditor) else None

    def open_editor(self, modules, title=None):
        if self.session.model is None:
            return None
        key = "basic" if modules is None else "ed:" + ",".join(sorted(arxml.short_name(m) for m in modules))
        if self.editors.has(key):
            self.editors.select(key)
            return self.editors.widget(key)
        title = title or ("Basic Editor" if modules is None else arxml.short_name(modules[0]))
        ed = BasicEditor(self.editors.body, self, modules=modules, title=title)
        icon = self.icons.basic if modules is None else self.icons.module
        self._welcome.pack_forget()
        self.editors.add(key, title, icon, ed, select=True)
        ed.populate()
        first = (modules or self.session.model.modules or [None])[0]
        if first is not None:
            ed.tree.select_element(first)
        return ed

    def _on_editor_change(self):
        ed = self.current_editor()
        if ed is not None and ed.node is not None and ed.node.el is not None and ed.node.kind != "group":
            self.props.show_container(ed.node.el, ed.node.cdef)

    def goto_element(self, el):
        """Show *el* (module/container/value) in the current editor or the Basic Editor."""
        if el is None:
            return None
        target = el
        while target is not None and arxml.local(target) not in (MODULE_TAG, "ECUC-CONTAINER-VALUE"):
            target = target.getparent()
        ed = self.current_editor()
        if ed is None or not ed.contains(target):
            ed = self.open_editor(None)
        else:
            self.editors.select(self.editors.current)
        if ed is not None:
            ed.tree.select_element(target)
        return ed

    def remember(self, editor, node):
        if self._nav:
            return
        key = self.editors.current
        entry = (key, node.el, node.kind, node.cdef.path if node.cdef else None)
        if self.hist_pos >= 0 and self.history[self.hist_pos][:2] == entry[:2]:
            return
        self.history = self.history[:self.hist_pos + 1] + [entry]
        self.history = self.history[-100:]
        self.hist_pos = len(self.history) - 1

    def _go_hist(self, pos):
        if not (0 <= pos < len(self.history)):
            return
        key, el, kind, _d = self.history[pos]
        self.hist_pos = pos
        self._nav = True
        try:
            if self.editors.has(key):
                self.editors.select(key)
                ed = self.editors.widget(key)
                if el is not None and self._attached(el):
                    ed.tree.select_element(el if kind != "group" else el)
        finally:
            self._nav = False

    def nav_back(self):
        self._go_hist(self.hist_pos - 1)

    def nav_forward(self):
        self._go_hist(self.hist_pos + 1)

    def show_find(self):
        self.bottom.select("find")
        self.find.entry.focus_set()

    # =============================================================== project
    def open_project_dialog(self):
        f = filedialog.askopenfilename(title="Open DaVinci project",
                                       filetypes=[("DaVinci project", "*.dpa"), ("All", "*.*")])
        if f:
            self.open_project(f)

    def open_files_dialog(self):
        if not self._confirm_discard():
            return
        r = OpenFilesDialog(self, self).run()
        if not r:
            return
        files, sip, extra = r
        self._load(lambda pr: self.session.open_files(files, sip, extra, progress=pr))

    def open_project(self, dpa):
        if not self._confirm_discard():
            return
        if not os.path.exists(dpa):
            messagebox.showerror(APP_NAME, f"Project not found:\n{dpa}")
            return
        self.cfg.add_recent(os.path.abspath(dpa))
        self.cfg.save()
        self._refresh_recent()
        self._load(lambda pr: self.session.open_dpa(dpa, progress=pr))

    def _close_editors(self):
        for v in list(self.editors.views):
            v[3].destroy()
            v[4].destroy()
        self.editors.views.clear()
        self.editors.current = None

    def _load(self, work):
        self.session = Session(self.cfg)
        self._close_editors()
        self.val.set_results([])
        self.history, self.hist_pos = [], -1

        def done(_sess):
            s = self.session
            self.update_title()
            self.nav.rebuild()
            self.open_editor(None)
            self.status(f"Loaded {len(s.model.modules)} modules, {len(s.model.path_index)} containers, "
                        f"{len(s.defs.module_index)} module definitions in {s.load_time:.1f} s")
            if s.project is not None and not s.defs.files:
                self._ask_sip_folder()
                return
            if self.cfg.get("validate_on_load", True):
                self.validate()
        self.run_bg(lambda pr: work(pr), done, "Loading project…")

    def _ask_sip_folder(self):
        s = self.session
        if not messagebox.askyesno(
                APP_NAME, f"No module definitions (BSWMD) were found for this project.\n\nSIP folder from the "
                          f".dpa: {s.project.sip_dir}\n\nWithout the SIP you can only edit existing values. "
                          f"Select the SIP folder (the folder that contains 'Components' and "
                          f"'DaVinciConfigurator') now?"):
            return
        d = filedialog.askdirectory(title="SIP folder (contains Components\\<Module>\\BSWMD)")
        if d:
            s.set_sip_override(s.project.path, os.path.normpath(d))
            self.open_project(s.project.path)

    def reload(self):
        if self.session.project:
            p = self.session.project.path
            if self.session.model and self.session.model.is_dirty():
                if not messagebox.askyesno(APP_NAME, "Discard unsaved changes and reload?"):
                    return
                for f in self.session.model.files.values():
                    f.dirty = False
            self.open_project(p)

    def _confirm_discard(self):
        m = self.session.model
        if m is None or not m.is_dirty():
            return True
        r = messagebox.askyesnocancel(APP_NAME, "Save changes before continuing?")
        if r is None:
            return False
        if r:
            return self.save()
        return True

    def save(self):
        m = self.session.model
        if m is None or self.busy:
            return False
        changed = [f.path for f in m.files.values() if os.path.exists(f.path)
                   and abs(os.path.getmtime(f.path) - f.mtime) > 1e-6 and f.dirty]
        if changed and not messagebox.askyesno(
                APP_NAME, "These files were modified on disk by another program (DaVinci?):\n\n" +
                          "\n".join(changed) + "\n\nOverwrite them?"):
            return False
        try:
            saved = m.save(backup=self.cfg.get("backup_on_save", True))
        except OSError as ex:
            messagebox.showerror(APP_NAME, f"Save failed: {ex}")
            return False
        self.update_title()
        self.status(f"Saved {len(saved)} file(s)" + (": " + ", ".join(os.path.basename(s) for s in saved)
                                                    if saved else ""))
        return True

    def on_close(self):
        if not self._confirm_discard():
            return
        if self.dv_run is not None and self.dv_run.returncode is None:
            if not messagebox.askyesno(APP_NAME, "DaVinci is still running. Cancel it and exit?"):
                return
            self.dv_run.cancel()
        self.cfg["geometry"] = self.geometry()
        self.cfg.save()
        self.destroy()

    # ============================================================ navigation
    def goto_path(self, path):
        el = self.session.model.resolve(path)
        if el is None:
            self.status(f"Not found in ECUC: {path}")
            return None
        return self.goto_element(el)

    def goto_path_dialog(self):
        if self.session.model is None:
            return
        p = simpledialog.askstring(APP_NAME, "AUTOSAR path (container, or DaVinci object like "
                                             "/ActiveEcuC/X/Y[0:Param]):")
        if p:
            path, param, idx = parse_object_ref(p)
            ed = self.goto_path(path)
            if ed is not None and param:
                ed.select_param(name=param, index=idx)

    def goto_result(self, r):
        model = self.session.model
        el = r.element
        if el is not None and self._attached(el):
            ed = self.goto_element(el)
            if ed is not None:
                if arxml.local(el) not in (MODULE_TAG, "ECUC-CONTAINER-VALUE"):
                    ed.select_param(def_path=definition_ref(el), index=None)
                elif r.param:
                    ed.select_param(def_path=r.param, index=None)
            return
        if r.obj:
            path, param, idx = parse_object_ref(r.obj)
            ed = self.goto_path(path)
            if ed is not None:
                if param:
                    ed.select_param(name=param, index=idx)
                return
        if r.definition:
            els = model.containers_of_def(r.definition.rsplit("/", 1)[0], self.session.defs)
            if els:
                ed = self.goto_element(els[0])
                if ed is not None:
                    ed.select_param(def_path=r.definition, index=None)
                return
        self.status("Cannot locate the object of this result in the loaded ECUC")

    def _attached(self, el):
        root = el.getroottree().getroot()
        return any(f.root is root for f in self.session.model.files.values()) and \
            (el.getparent() is not None or arxml.local(el) == "AUTOSAR")

    # =============================================================== editing
    def _editors(self):
        return [v[3] for v in self.editors.views if isinstance(v[3], BasicEditor)]

    def after_edit(self, el, label, structure=False):
        self.update_title()
        fb = getattr(self.session, "fallback", None)
        if fb is not None:
            fb.invalidate()
        cur = self.current_editor()
        for ed in self._editors():
            if structure:
                grp = ed.node.cdef.path if ed.node is not None and ed.node.kind == "group" else None
                ed.tree.refresh_element(el)
                if grp and ed is cur:
                    self._reselect_group(ed, el, grp)
                    continue
            if ed is cur:
                ed.refresh()
        if label:
            self.status(f"Changed: {label}")

    def _reselect_group(self, ed, parent_el, def_path):
        iid = ed.tree.by_el.get(parent_el)
        if iid is None:
            return
        ed.tree._load_children(iid)
        for gid in ed.tree.tv.get_children(iid):
            n = ed.tree.nodes.get(gid)
            if n is not None and n.kind == "group" and n.cdef.path == def_path:
                ed.tree.tv.selection_set(gid)
                ed.show_node(n)
                return
        ed.tree.select_element(parent_el)

    def undo(self):
        m = self.session.model
        if m is None or self.busy:
            return
        self._after_undo(m.undo(), "Undo")

    def redo(self):
        m = self.session.model
        if m is None or self.busy:
            return
        self._after_undo(m.redo(), "Redo")

    def _after_undo(self, lbl, what):
        if lbl is None:
            self.status(f"Nothing to {what.lower()}")
            return
        for ed in self._editors():
            sel = ed.tree.selected_node()
            ed.populate()
            if sel is not None and sel.el is not None and self._attached(sel.el):
                ed.tree.select_element(sel.el)
        self.update_title()
        self.status(f"{what}: {lbl}")

    def container_menu(self, node, x, y):
        m = tk.Menu(self, tearoff=False)
        s = self.session
        ic = self.icons
        if node.kind in ("module", "container") and node.cdef is not None:
            addable = [c for c in node.cdef.containers()
                       if len([e for e in container_children(node.el) if definition_ref(e) == c.path]) < c.upper]
            if addable:
                sub = tk.Menu(m, tearoff=False)
                for c in addable:
                    sub.add_command(label=f"{c.label}  ({c.multiplicity_str()})",
                                    command=lambda c=c: self.add_container_dialog(node.el, c))
                m.add_cascade(label="Create Sub-Container", image=ic.add, compound="left", menu=sub)
        if node.kind == "group":
            m.add_command(label=f"Create {node.cdef.label}…", image=ic.add, compound="left",
                          command=lambda: self.add_container_dialog(node.el, node.cdef))
        if node.kind in ("module", "container") and node.cdef is not None:
            m.add_command(label="Create Missing Mandatory Elements", image=ic.add, compound="left",
                          command=lambda: self.complete_element(node.el))
        if node.kind == "module":
            m.add_separator()
            m.add_command(label="Remove Module…", image=ic.delete, compound="left",
                          command=lambda: self.remove_module(node.el))
        if node.kind == "container":
            m.add_command(label="Rename…  (F2)", command=lambda: self.rename_container(node.el))
            m.add_command(label="Duplicate", image=ic.copy, compound="left",
                          command=lambda: self.duplicate_container(node.el))
            m.add_command(label="Remove  (Del)", image=ic.delete, compound="left",
                          command=lambda: self.delete_container(node.el))
            m.add_separator()
            m.add_command(label="Element Usage", command=lambda: self.props.show_container(node.el, node.cdef))
        if node.el is not None and node.kind != "group":
            m.add_command(label="Validate", image=ic.validate, compound="left",
                          command=lambda: self.validate_element(node.el))
            m.add_command(label="Copy Path", command=lambda: (self.clipboard_clear(),
                                                               self.clipboard_append(s.model.path_of(node.el))))
        m.tk_popup(x, y)

    def complete_element(self, el):
        from ..configure import complete_container
        cdef = self.session.container_def(el)[0]
        if cdef is None:
            return
        n = complete_container(self.session, el, cdef)
        self.after_edit(el, f"Created {n} mandatory element(s)" if n else "Nothing missing", structure=bool(n))

    def remove_module(self, el):
        s = self.session
        name = arxml.short_name(el)
        mp = s.model.path_of(el)
        refs = sum(len(v) for k, v in s.model.ref_index().items()
                   if (k == mp or k.startswith(mp + "/")) and s.model.module_of(v[0]) is not el)
        msg = f"Remove the module configuration {name}?"
        if refs:
            msg += f"\n\n{refs} reference(s) from other modules point into it and will become dangling."
        if not messagebox.askyesno(APP_NAME, msg):
            return
        s.model.remove_module(el)
        self.modules_changed(f"Removed module {name} (Ctrl+Z to undo)")

    def add_container_dialog(self, parent_el, cdef=None):
        if self.busy:
            return
        s = self.session
        pdef = s.container_def(parent_el)[0]
        if pdef is None:
            return
        cdefs = pdef.containers()
        if not cdefs:
            self.status("This element has no sub-container definitions")
            return
        r = AddContainerDialog(self, self, parent_el, cdefs, cdef).run()
        if not r:
            return
        cd, name, count, defaults, recommended = r
        existing = len([e for e in container_children(parent_el) if definition_ref(e) == cd.path])
        if existing + count > cd.upper:
            if not messagebox.askyesno(APP_NAME, f"{cd.name} allows at most {cd.multiplicity_str()} instances. "
                                                 f"Add anyway (validation will report AR-ECUC02008)?"):
                return
        from ..configure import create_container
        created = create_container(s, parent_el, cd, name, count, with_defaults=defaults,
                                   apply_recommended=recommended)
        last = created[-1] if created else None
        self.after_edit(parent_el, f"Added {count} × {cd.name}", structure=True)
        if last is not None:
            ed = self.current_editor()
            if ed is not None:
                ed.tree.select_element(last)

    def delete_container(self, el):
        if self.busy:
            return
        s = self.session
        path = s.model.path_of(el)
        n = sum(len(v) for k, v in s.model.ref_index().items() if k == path or k.startswith(path + "/"))
        msg = f"Remove {path}?"
        if n:
            msg += f"\n\n{n} reference(s) point into this container and will become dangling (Cfg00024)."
        if not messagebox.askyesno(APP_NAME, msg):
            return
        parent = el.getparent().getparent()
        s.model.delete_element(el)
        self.after_edit(parent, f"Removed {path}", structure=True)
        ed = self.current_editor()
        if ed is not None:
            ed.tree.select_element(parent)

    def rename_container(self, el):
        if self.busy:
            return
        s = self.session
        old = arxml.short_name(el)
        new = simpledialog.askstring(APP_NAME, "New short name (references are updated):", initialvalue=old)
        if not new or new == old:
            return
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]{0,127}$", new):
            messagebox.showerror(APP_NAME, "Invalid short name")
            return
        parent = el.getparent().getparent()
        if any(arxml.short_name(c) == new for c in container_children(parent)):
            messagebox.showerror(APP_NAME, f"'{new}' already exists here")
            return
        s.model.rename_container(el, new)
        self.after_edit(parent, f"Renamed {old} → {new}", structure=True)
        ed = self.current_editor()
        if ed is not None:
            ed.tree.select_element(el)

    def duplicate_container(self, el):
        s = self.session
        parent = el.getparent().getparent()
        cdef = s.defs.find(definition_ref(el))
        if cdef is None:
            return
        name = s.model.unique_name(parent, arxml.short_name(el))
        new = s.model.add_container(parent, cdef, name, with_defaults=False)
        for child in el:
            if arxml.local(child) in ("PARAMETER-VALUES", "REFERENCE-VALUES", "SUB-CONTAINERS"):
                c = copy.deepcopy(child)
                for sub in c.iter(arxml.q("ECUC-CONTAINER-VALUE")):
                    sub.set("UUID", arxml.new_uuid())
                arxml.insert_child(new, c)
        s.model._index_subtree(new, False, s.model.path_of(parent))
        s.model._index_subtree(new, True, s.model.path_of(parent))
        self.after_edit(parent, f"Duplicated {arxml.short_name(el)} → {name}", structure=True)
        ed = self.current_editor()
        if ed is not None:
            ed.tree.select_element(new)

    def apply_actions(self, pairs):
        if self.busy:
            return
        ok = fail = 0
        for r, a in pairs:
            try:
                a.apply()
                ok += 1
            except Exception as ex:
                fail += 1
                self.console.write(f"Solving action failed ({r.rule_id}): {ex}", "ERROR")
        for ed in self._editors():
            ed.populate()
        self.update_title()
        self.status(f"Executed {ok} solving action(s)" + (f", {fail} failed" if fail else ""))
        self.validate()

    # ============================================================ validation
    def validate(self, **options):
        s = self.session
        if s.model is None:
            return
        t0 = time.time()

        def work(pr):
            return s.validate(progress=lambda i, n, name: pr(f"Validating: {name}", (i + 1) / max(n, 1)), **options)
        self.run_bg(work, lambda res: self._show_results(f"Validation finished in {time.time() - t0:.1f} s"),
                    "Validating…")

    def validate_element(self, el):
        s = self.session
        res = s.validate_container(el)
        self.val.set_results(res + list(s.dv_results))
        self.bottom.select("val")
        self.status(f"{len(res)} result(s) for {s.model.path_of(el)}")

    def _show_results(self, msg):
        allr = self.all_results()
        self.val.set_results(allr)
        for ed in self._editors():
            ed.tree.set_marks(allr)
        ed = self.current_editor()
        if ed is not None:
            ed.refresh()
        self.nav.rebuild()
        self.update_counts()
        if self.session.validator and self.session.validator.errors:
            for err in self.session.validator.errors:
                self.console.write("Rule error: " + err, "ERROR")
        self.status(msg)

    # ============================================================== DaVinci
    def _dvcfgcmd(self):
        exe = self.cfg.get("dvcfgcmd")
        if exe and os.path.isfile(exe):
            return exe
        s = self.session
        inst = davinci.find_installations(s.project.sip_dir if s.project else None)
        if inst:
            self.cfg["dvcfgcmd"] = inst[0].exe
            self.cfg.save()
            return inst[0].exe
        messagebox.showerror(APP_NAME, "DVCfgCmd.exe not found. Configure it in Project › Project Settings.")
        return None

    def _ensure_saved(self):
        m = self.session.model
        if m is not None and m.is_dirty():
            if self.cfg.get("auto_save_before_generate", True) or messagebox.askyesno(
                    APP_NAME, "DaVinci works on the files on disk. Save changes now?"):
                return self.save()
            return False
        return True

    def davinci_validate(self):
        s = self.session
        if s.project is None:
            messagebox.showinfo(APP_NAME, "DaVinci validation needs a .dpa project.")
            return
        exe = self._dvcfgcmd()
        if not exe or not self._ensure_saved():
            return
        report = os.path.join(s.report_dir(), "ValidationReport.xml")
        log = os.path.join(s.report_dir(), "DVCfgCmd_validate.log")
        self._run_davinci(davinci.build_validate_cmd(exe, s.project.path, report, log), report, "validate")

    def generate(self):
        s = self.session
        if s.project is None:
            messagebox.showinfo(APP_NAME, "Code generation needs a .dpa project.")
            return
        if self.busy or (self.dv_run is not None and self.dv_run.returncode is None):
            self.status("A task is already running")
            return
        GenerateDialog(self, self).run()

    def start_generation(self, exe, opts, local_first, dialog=None):
        """Called by the Generate dialog; the dialog stays open and shows the progress."""
        s = self.session
        parent = dialog or self
        if local_first:
            names = None
            if opts.modules:
                names = {arxml.short_name(m) for m in s.model.modules if definition_ref(m) in opts.modules}
            res = s.validate(modules=list(names) if names else None)
            errs = [x for x in res if x.severity >= Severity.ERROR and not x.acknowledged
                    and (names is None or not x.obj or any(f"/{n}/" in x.obj + "/" for n in names))]
            self._show_results("Local validation before generation")
            if errs and not messagebox.askyesno(
                    APP_NAME, f"Local validation found {len(errs)} error(s) in the selected modules.\n"
                              f"DaVinci will most likely refuse to generate them.\n\nGenerate anyway?",
                    parent=parent):
                return
        if not self._ensure_saved():
            return
        report = os.path.join(s.report_dir(), "GenerationReport.xml")
        log = os.path.join(s.report_dir(), "DVCfgCmd_generate.log")
        self.gen_dialog = dialog
        if dialog is not None:
            dialog.start_running()
        self._run_davinci(davinci.build_generate_cmd(exe, s.project.path, report, opts, log), report, "generate")

    def _run_davinci(self, cmd, report, what):
        if self.dv_run is not None and self.dv_run.returncode is None:
            self.status("DaVinci is already running")
            return
        if os.path.exists(report):
            try:
                os.remove(report)
            except OSError:
                pass
        self.console.set_running(True)
        self.bottom.select("console")
        self.console.write("")
        self.console.write("> " + davinci.format_cmd(cmd), "cmd")
        self.console.state.config(text=f"DaVinci {what} running…")
        t0 = time.time()
        self.progress.configure(mode="indeterminate")
        self.progress.start(15)
        mtimes = {f.path: os.path.getmtime(f.path) for f in self.session.model.files.values()
                  if os.path.exists(f.path)}
        self.dv_run = davinci.DvRun(cmd, lambda line: self.call_gui(self._dv_line, line),
                                    lambda rc: self.call_gui(self._dv_done, rc, report, what, time.time() - t0,
                                                             mtimes)).start()

    def _dv_line(self, line):
        self.console.write(line)
        p = davinci.parse_progress(line)
        dlg = getattr(self, "gen_dialog", None)
        if p and dlg is not None and dlg.winfo_exists():
            dlg.on_progress(*p)
        if p:
            self.status(f"DaVinci: {p[0]} {p[1]} {p[2].split(chr(9))[0]}")
        elif "Action '" in line and "started" in line:
            self.status("DaVinci: " + line.split(" - ", 1)[-1][:120])

    def _dv_done(self, rc, report, what, secs, mtimes):
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.console.set_running(False)
        self.bottom.select(self.bottom.current)
        rep = davinci.parse_report(report)
        s = self.session
        for r in rep.validation:
            if r.obj:
                path, _param, _idx = parse_object_ref(r.obj)
                r.element = s.model.resolve(path)
        s.dv_results = rep.validation
        self.console.write(f"DVCfgCmd finished: exit code {rc} {davinci.EXIT_CODES.get(rc, '')} after {secs:.0f} s — "
                           f"{rep.process_result or 'no report'}", "ok" if rc == 0 else "ERROR")
        self.console.state.config(text=f"Last run: {what}, exit code {rc}")
        if what == "generate":
            self.genview.show(rep, rc, secs)
            self.bottom.select("gen")
            dlg = getattr(self, "gen_dialog", None)
            if dlg is not None and dlg.winfo_exists():
                dlg.finished(rc, rep)
            self.gen_dialog = None
        else:
            self.bottom.select("val")
        self._show_results(f"DaVinci {what} finished (exit code {rc}) in {secs:.0f} s — "
                           f"{len(rep.validation)} DaVinci result(s)")
        changed = [p for p, t in mtimes.items() if os.path.exists(p) and os.path.getmtime(p) != t]
        if changed and messagebox.askyesno(APP_NAME, "DaVinci modified project files (--saveProject):\n" +
                                           "\n".join(os.path.basename(c) for c in changed) + "\n\nReload now?"):
            self.reload()

    def cancel_davinci(self):
        if self.dv_run is not None:
            self.dv_run.cancel()
            self.console.write("Cancel requested…", "WARN")

    def open_in_davinci(self):
        s = self.session
        exe = self._dvcfgcmd()
        if not exe or s.project is None or not self._ensure_saved():
            return
        gui = os.path.join(os.path.dirname(exe), "DaVinciCFG.exe")
        subprocess.Popen([gui, "--project", s.project.path], cwd=os.path.dirname(gui))
        self.status("DaVinci Configurator started — reload here after saving in DaVinci")

    def settings_dialog(self):
        r = SettingsDialog(self, self).run()
        if r == "reload" and self.session.project is not None:
            self.status("Definition folders changed — reloading the project")
            self.reload()
        elif r:
            self.status("Settings saved")

    def modules_dialog(self):
        if self.session.model is None or self.busy:
            return
        ModulesDialog(self, self).run()

    def modules_changed(self, msg):
        for ed in self._editors():
            ed.populate()
        self.nav.rebuild()
        self.update_title()
        self.status(msg)


def main(dpa=None):
    app = App(dpa)
    app.mainloop()
