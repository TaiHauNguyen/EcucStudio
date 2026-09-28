"""EcucStudio main window."""
from __future__ import annotations

import os
import queue
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
from .console import ConsoleView, GenerationView
from .dialogs import AddContainerDialog, GenerateDialog, OpenFilesDialog, SettingsDialog
from .editor import EditorPanel
from .properties import PropertiesPanel
from .theme import Icons, init_style
from .tree import ProjectTree
from .validation_view import ValidationView

APP_NAME = "EcucStudio"


class App(tk.Tk):
    def __init__(self, dpa=None):
        super().__init__()
        self.cfg = Settings()
        self.session = Session(self.cfg)
        self.busy = False
        self.dv_run = None
        self._q = queue.Queue()
        init_style(self)
        self.icons = Icons()
        self.title(APP_NAME)
        self.geometry(self.cfg.get("geometry", "1500x900"))
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build_menu()
        self._build_toolbar()
        self._build_body()
        self._build_status()
        self._bind_keys()
        self.after(100, self._poll)
        self._welcome()
        if dpa:
            self.after(200, lambda: self.open_project(dpa))

    # ================================================================== layout
    def _build_menu(self):
        mb = tk.Menu(self)
        f = tk.Menu(mb, tearoff=False)
        f.add_command(label="Open Project (.dpa)…", accelerator="Ctrl+O", command=self.open_project_dialog)
        f.add_command(label="Open ECUC ARXML files…", command=self.open_files_dialog)
        self.recent_menu = tk.Menu(f, tearoff=False)
        f.add_cascade(label="Recent projects", menu=self.recent_menu)
        f.add_separator()
        f.add_command(label="Save", accelerator="Ctrl+S", command=self.save)
        f.add_command(label="Reload from disk", command=self.reload)
        f.add_separator()
        f.add_command(label="Exit", command=self.on_close)
        mb.add_cascade(label="File", menu=f)
        e = tk.Menu(mb, tearoff=False)
        e.add_command(label="Undo", accelerator="Ctrl+Z", command=self.undo)
        e.add_command(label="Redo", accelerator="Ctrl+Y", command=self.redo)
        e.add_separator()
        e.add_command(label="Find container…", accelerator="Ctrl+F", command=lambda: self.tree.filter_entry.focus_set())
        e.add_command(label="Go to path…", accelerator="Ctrl+L", command=self.goto_path_dialog)
        mb.add_cascade(label="Edit", menu=e)
        p = tk.Menu(mb, tearoff=False)
        p.add_command(label="Validate", accelerator="F5", command=self.validate)
        p.add_command(label="Validate with DaVinci (DVCfgCmd -v)", command=self.davinci_validate)
        p.add_command(label="Solve All", command=lambda: self.val.solve_all())
        p.add_separator()
        p.add_command(label="Generate with DaVinci…", accelerator="Ctrl+G", command=self.generate)
        p.add_command(label="Open project in DaVinci Configurator GUI", command=self.open_in_davinci)
        p.add_separator()
        p.add_command(label="Settings…", command=self.settings_dialog)
        mb.add_cascade(label="Project", menu=p)
        h = tk.Menu(mb, tearoff=False)
        h.add_command(label="About", command=lambda: messagebox.showinfo(
            APP_NAME, f"{APP_NAME} {__version__}\nECUC configurator with DaVinci compatible validation\n"
                      f"and DaVinci Configurator command line generation."))
        mb.add_cascade(label="Help", menu=h)
        self.config(menu=mb)
        self._refresh_recent()

    def _refresh_recent(self):
        self.recent_menu.delete(0, "end")
        for pth in self.cfg.get("recent", []):
            self.recent_menu.add_command(label=pth, command=lambda p=pth: self.open_project(p))

    def _build_toolbar(self):
        tb = ttk.Frame(self, padding=(4, 2))
        tb.pack(fill="x")
        for text, cmd in (("Open", self.open_project_dialog), ("Save", self.save), (None, None),
                          ("Undo", self.undo), ("Redo", self.redo), (None, None),
                          ("Validate", self.validate), ("DaVinci Validate", self.davinci_validate),
                          ("Solve All", lambda: self.val.solve_all()), (None, None),
                          ("Generate…", self.generate)):
            if text is None:
                ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=4)
            else:
                ttk.Button(tb, text=text, command=cmd).pack(side="left", padx=1)
        self.proj_label = ttk.Label(tb, text="", foreground="#555")
        self.proj_label.pack(side="right")

    def _build_body(self):
        vp = ttk.PanedWindow(self, orient="vertical")
        vp.pack(fill="both", expand=True)
        hp = ttk.PanedWindow(vp, orient="horizontal")
        vp.add(hp, weight=4)
        self.tree = ProjectTree(hp, self)
        hp.add(self.tree, weight=1)
        self.editor = EditorPanel(hp, self)
        hp.add(self.editor, weight=3)
        self.props = PropertiesPanel(hp, self)
        hp.add(self.props, weight=1)
        self.bottom = ttk.Notebook(vp)
        vp.add(self.bottom, weight=2)
        self.val = ValidationView(self.bottom, self)
        self.console = ConsoleView(self.bottom, self)
        self.genview = GenerationView(self.bottom, self)
        self.bottom.add(self.val, text="Validation")
        self.bottom.add(self.console, text="Console")
        self.bottom.add(self.genview, text="Generation Result")

    def _build_status(self):
        sb = ttk.Frame(self)
        sb.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(sb, textvariable=self.status_var, style="Status.TLabel").pack(side="left", fill="x", expand=True)
        self.progress = ttk.Progressbar(sb, length=220, mode="determinate", maximum=1.0)
        self.progress.pack(side="right", padx=4, pady=1)
        self.counts_var = tk.StringVar(value="")
        ttk.Label(sb, textvariable=self.counts_var, style="Status.TLabel").pack(side="right")

    def _bind_keys(self):
        self.bind_all("<Control-s>", lambda e: self.save())
        self.bind_all("<Control-o>", lambda e: self.open_project_dialog())
        self.bind_all("<Control-z>", lambda e: self._key_undo(e))
        self.bind_all("<Control-y>", lambda e: self._key_redo(e))
        self.bind_all("<F5>", lambda e: self.validate())
        self.bind_all("<Control-g>", lambda e: self.generate())
        self.bind_all("<Control-f>", lambda e: self.tree.filter_entry.focus_set())
        self.bind_all("<Control-l>", lambda e: self.goto_path_dialog())

    def _key_undo(self, e):
        if isinstance(e.widget, (tk.Entry, ttk.Entry, tk.Text)):
            return
        self.undo()

    def _key_redo(self, e):
        if isinstance(e.widget, (tk.Entry, ttk.Entry, tk.Text)):
            return
        self.redo()

    def _welcome(self):
        self.editor.header.config(text=f"{APP_NAME} — open a DaVinci project (.dpa) to start")
        self.editor.subheader.config(text="File › Open Project…  (recent projects in the File menu)")

    # ============================================================ utilities
    def status(self, msg, frac=None):
        self.status_var.set(msg)
        if frac is not None:
            self.progress["value"] = frac

    def run_bg(self, work, done=None, msg="Working…", block=True):
        """Run *work(progress)* in a thread; *done(result, error)* runs in the GUI thread."""
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
            except Exception as ex:  # report background failures in the GUI
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
                        messagebox.showerror(APP_NAME, f"{err[0]}\n\nDetails in the Console tab.")
                    elif done:
                        done(res)
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def update_title(self):
        s = self.session
        name = s.project.name if s.project else ("ECUC files" if s.model else "")
        dirty = s.model is not None and s.model.is_dirty()
        self.title(f"{APP_NAME} — {name}{' *' if dirty else ''}")
        if s.project:
            p = s.project
            self.proj_label.config(text=f"{p.derivative} | {p.compiler} | {', '.join(p.sip_ids)} | {p.path}")

    def all_results(self):
        return list(self.session.results) + list(self.session.dv_results)

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

    def _load(self, work):
        self.session = Session(self.cfg)
        self.tree.clear()
        self.editor.show(None)
        self.val.set_results([])

        def done(sess):
            s = self.session
            s.model.listeners.append(self._on_model_change)
            self.tree.populate()
            self.update_title()
            nmods = len(s.model.modules)
            self.status(f"Loaded {nmods} modules, {len(s.model.path_index)} containers, "
                        f"{len(s.defs.module_index)} module definitions in {s.load_time:.1f} s")
            if s.model.modules:
                self.tree.select_element(s.model.modules[0])
            if self.cfg.get("validate_on_load", True):
                self.validate()
        self.run_bg(lambda pr: work(pr), done, "Loading project…")

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
    def show_node(self, node):
        self.editor.show(node)
        if node.kind == "group":
            self.props.show_def(node.cdef)
        elif node.el is not None:
            self.props.show_container(node.el, node.cdef)

    def goto_path(self, path):
        el = self.session.model.resolve(path)
        if el is None:
            self.status(f"Not found in ECUC: {path}")
            return False
        return self.tree.select_element(el)

    def goto_path_dialog(self):
        if self.session.model is None:
            return
        p = simpledialog.askstring(APP_NAME, "AUTOSAR path (container or DaVinci object like /ActiveEcuC/X[0:Param]):")
        if p:
            path, param, idx = parse_object_ref(p)
            if self.goto_path(path) and param:
                self.editor.select_param(name=param, index=idx)

    def goto_result(self, r):
        model = self.session.model
        el = r.element
        if el is not None and el.getroottree().getroot() is not None and self._attached(el):
            target = el
            if arxml.local(el) not in (MODULE_TAG, "ECUC-CONTAINER-VALUE"):
                target = el.getparent().getparent()
            self.tree.select_element(target)
            if target is not el or r.param:
                self.editor.select_param(def_path=r.param or definition_ref(el), index=None)
            return
        if r.obj:
            path, param, idx = parse_object_ref(r.obj)
            if self.goto_path(path):
                if param:
                    self.editor.select_param(name=param, index=idx)
                return
        if r.definition:
            parent_def = r.definition.rsplit("/", 1)[0]
            els = model.containers_of_def(parent_def, self.session.defs)
            if els:
                self.tree.select_element(els[0])
                self.editor.select_param(def_path=r.definition, index=None)
                return
        self.status("Cannot locate the object of this result in the loaded ECUC")

    def _attached(self, el):
        root = el.getroottree().getroot()
        return any(f.root is root for f in self.session.model.files.values()) and \
            (el.getparent() is not None or arxml.local(el) == "AUTOSAR")

    # =============================================================== editing
    def _on_model_change(self, kind, el):
        pass

    def after_edit(self, el, label, structure=False):
        self.update_title()
        if structure:
            self.tree.refresh_element(el)
        self.editor.refresh()
        if label:
            self.status(f"Changed: {label}")

    def undo(self):
        m = self.session.model
        if m is None or self.busy:
            return
        lbl = m.undo()
        self._after_undo(lbl, "Undo")

    def redo(self):
        m = self.session.model
        if m is None or self.busy:
            return
        lbl = m.redo()
        self._after_undo(lbl, "Redo")

    def _after_undo(self, lbl, what):
        if lbl is None:
            self.status(f"Nothing to {what.lower()}")
            return
        sel = self.tree.selected_node()
        self.tree.populate()
        self.tree.set_marks(self.all_results())
        if sel is not None and sel.el is not None and self._attached(sel.el):
            self.tree.select_element(sel.el)
        self.update_title()
        self.status(f"{what}: {lbl}")

    def container_menu(self, node, x, y):
        m = tk.Menu(self, tearoff=False)
        s = self.session
        if node.kind in ("module", "container") and node.cdef is not None:
            addable = [c for c in node.cdef.containers()
                       if len([e for e in container_children(node.el) if definition_ref(e) == c.path]) < c.upper]
            if addable:
                sub = tk.Menu(m, tearoff=False)
                for c in addable:
                    sub.add_command(label=f"{c.name}  ({c.multiplicity_str()})",
                                    command=lambda c=c: self.add_container_dialog(node.el, c))
                m.add_cascade(label="Add sub-container", menu=sub)
        if node.kind == "group":
            m.add_command(label=f"Add {node.cdef.name}…", command=lambda: self.add_container_dialog(node.el, node.cdef))
        if node.kind == "container":
            m.add_command(label="Rename…  (F2)", command=lambda: self.rename_container(node.el))
            m.add_command(label="Duplicate", command=lambda: self.duplicate_container(node.el))
            m.add_command(label="Delete  (Del)", command=lambda: self.delete_container(node.el))
            m.add_separator()
            refs = s.model.references_to(s.model.path_of(node.el))
            m.add_command(label=f"Show {len(refs)} referencing object(s)",
                          command=lambda: self.props.show_container(node.el, node.cdef))
        if node.el is not None and node.kind != "group":
            m.add_command(label="Validate this element", command=lambda: self.validate_element(node.el))
            m.add_command(label="Copy path", command=lambda: (self.clipboard_clear(),
                                                               self.clipboard_append(s.model.path_of(node.el))))
        m.tk_popup(x, y)

    def add_container_dialog(self, parent_el, cdef=None):
        if self.busy:
            return
        s = self.session
        pdef = s.defs.find(definition_ref(parent_el))
        if pdef is None:
            return
        cdefs = pdef.containers()
        if not cdefs:
            self.status("This element has no sub-container definitions")
            return
        r = AddContainerDialog(self, self, parent_el, cdefs, cdef).run()
        if not r:
            return
        cd, name, count, defaults = r
        existing = len([e for e in container_children(parent_el) if definition_ref(e) == cd.path])
        if existing + count > cd.upper:
            if not messagebox.askyesno(APP_NAME, f"{cd.name} allows at most {cd.multiplicity_str()} instances. "
                                                 f"Add anyway (validation will report AR-ECUC02008)?"):
                return
        last = None
        for i in range(count):
            nm = name if i == 0 else s.model.unique_name(parent_el, name)
            last = s.model.add_container(parent_el, cd, nm, with_defaults=defaults)
        self.after_edit(parent_el, f"Added {count} × {cd.name}", structure=True)
        if last is not None:
            self.tree.select_element(last)

    def selected_container(self):
        n = self.tree.selected_node()
        return n.el if n is not None and n.kind == "container" else None

    def delete_selected_container(self):
        el = self.selected_container()
        if el is not None:
            self.delete_container(el)

    def delete_container(self, el):
        if self.busy:
            return
        s = self.session
        path = s.model.path_of(el)
        refs = [r for r in s.model.ref_index().items() if r[0] == path or r[0].startswith(path + "/")]
        n = sum(len(v) for _k, v in refs)
        msg = f"Delete {path}?"
        if n:
            msg += f"\n\n{n} reference(s) point into this container and will become dangling (Cfg00024)."
        if not messagebox.askyesno(APP_NAME, msg):
            return
        parent = el.getparent().getparent()
        s.model.delete_element(el)
        self.after_edit(parent, f"Deleted {path}", structure=True)
        self.tree.select_element(parent)

    def rename_selected_container(self):
        el = self.selected_container()
        if el is not None:
            self.rename_container(el)

    def rename_container(self, el):
        if self.busy:
            return
        import re
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
        self.tree.select_element(el)

    def duplicate_container(self, el):
        import copy
        s = self.session
        parent = el.getparent().getparent()
        cdef = s.defs.find(definition_ref(el))
        if cdef is None:
            return
        name = s.model.unique_name(parent, arxml.short_name(el))
        new = s.model.add_container(parent, cdef, name, with_defaults=False)
        # copy content (values & sub containers), new UUIDs
        for child in el:
            tag = arxml.local(child)
            if tag in ("PARAMETER-VALUES", "REFERENCE-VALUES", "SUB-CONTAINERS"):
                c = copy.deepcopy(child)
                for sub in c.iter(arxml.q("ECUC-CONTAINER-VALUE")):
                    sub.set("UUID", arxml.new_uuid())
                arxml.insert_child(new, c)
        s.model._index_subtree(new, False, s.model.path_of(parent))
        s.model._index_subtree(new, True, s.model.path_of(parent))
        self.after_edit(parent, f"Duplicated {arxml.short_name(el)} → {name}", structure=True)
        self.tree.select_element(new)

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
        self.tree.populate()
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

        def done(results):
            self._show_results(f"Validation finished in {time.time() - t0:.1f} s")
        self.run_bg(work, done, "Validating…")

    def validate_element(self, el):
        s = self.session
        res = s.validate_container(el)
        self.val.set_results(res + [r for r in s.dv_results])
        self.bottom.select(self.val)
        self.status(f"{len(res)} result(s) for {s.model.path_of(el)}")

    def _show_results(self, msg):
        s = self.session
        allr = self.all_results()
        self.val.set_results(allr)
        self.tree.set_marks(allr)
        self.editor.refresh()
        e = sum(1 for r in allr if r.severity >= Severity.ERROR and not r.acknowledged)
        w = sum(1 for r in allr if r.severity == Severity.WARNING and not r.acknowledged)
        self.counts_var.set(f"Errors {e}   Warnings {w}   ")
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
        messagebox.showerror(APP_NAME, "DVCfgCmd.exe not found. Configure it in Project › Settings.")
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
        cmd = davinci.build_validate_cmd(exe, s.project.path, report, log)
        self._run_davinci(cmd, report, "validate")

    def generate(self):
        s = self.session
        if s.project is None:
            messagebox.showinfo(APP_NAME, "Code generation needs a .dpa project.")
            return
        if self.busy or (self.dv_run is not None and self.dv_run.returncode is None):
            self.status("A task is already running")
            return
        r = GenerateDialog(self, self).run()
        if not r:
            return
        exe, opts, local_first = r
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
                              f"DaVinci will most likely refuse to generate them.\n\nGenerate anyway?"):
                return
        if not self._ensure_saved():
            return
        report = os.path.join(s.report_dir(), "GenerationReport.xml")
        log = os.path.join(s.report_dir(), "DVCfgCmd_generate.log")
        cmd = davinci.build_generate_cmd(exe, s.project.path, report, opts, log)
        self._run_davinci(cmd, report, "generate")

    def _run_davinci(self, cmd, report, what):
        if self.dv_run is not None and self.dv_run.returncode is None:
            self.status("DaVinci is already running")
            return
        if os.path.exists(report):
            try:
                os.remove(report)
            except OSError:
                pass
        self.bottom.select(self.console)
        self.console.write("")
        self.console.write("> " + davinci.format_cmd(cmd), "cmd")
        self.console.cancel_btn.config(state="normal")
        self.console.state.config(text=f"DaVinci {what} running…")
        t0 = time.time()
        self.progress.configure(mode="indeterminate")
        self.progress.start(15)
        mtimes = {f.path: os.path.getmtime(f.path) for f in self.session.model.files.values()
                  if os.path.exists(f.path)}

        def on_line(line):
            self.call_gui(self._dv_line, line)

        def on_done(rc):
            self.call_gui(self._dv_done, rc, report, what, time.time() - t0, mtimes)
        self.dv_run = davinci.DvRun(cmd, on_line, on_done).start()

    def _dv_line(self, line):
        self.console.write(line)
        p = davinci.parse_progress(line)
        if p:
            self.status(f"DaVinci: {p[0]} {p[1]} {p[2].split(chr(9))[0]}")
        elif "Action '" in line and "started" in line:
            self.status("DaVinci: " + line.split(" - ", 1)[-1][:120])

    def _dv_done(self, rc, report, what, secs, mtimes):
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.console.cancel_btn.config(state="disabled")
        rep = davinci.parse_report(report)
        s = self.session
        # map DaVinci results to loaded elements for navigation and tree marks
        for r in rep.validation:
            if r.obj:
                path, _param, _idx = parse_object_ref(r.obj)
                r.element = s.model.resolve(path)
        s.dv_results = rep.validation
        explain = davinci.EXIT_CODES.get(rc, "")
        self.console.write(f"DVCfgCmd finished: exit code {rc} {explain} after {secs:.0f} s — "
                           f"{rep.process_result or 'no report'}", "ok" if rc == 0 else "ERROR")
        self.console.state.config(text=f"Last run: {what}, exit {rc}")
        if what == "generate":
            self.genview.show(rep, rc, secs)
            self.bottom.select(self.genview)
        else:
            self.bottom.select(self.val)
        self._show_results(f"DaVinci {what} finished (exit code {rc}) in {secs:.0f} s — "
                           f"{len(rep.validation)} DaVinci result(s)")
        changed = [p for p, t in mtimes.items() if os.path.exists(p) and os.path.getmtime(p) != t]
        if changed:
            if messagebox.askyesno(APP_NAME, "DaVinci modified project files (--saveProject):\n" +
                                            "\n".join(os.path.basename(c) for c in changed) + "\n\nReload now?"):
                self.reload()

    def cancel_davinci(self):
        if self.dv_run is not None:
            self.dv_run.cancel()
            self.console.write("Cancel requested…", "WARN")

    def open_in_davinci(self):
        s = self.session
        exe = self._dvcfgcmd()
        if not exe or s.project is None:
            return
        gui = os.path.join(os.path.dirname(exe), "DaVinciCFG.exe")
        if not self._ensure_saved():
            return
        import subprocess
        subprocess.Popen([gui, "--project", s.project.path], cwd=os.path.dirname(gui))
        self.status("DaVinci Configurator started — reload here after saving in DaVinci")

    def settings_dialog(self):
        if SettingsDialog(self, self).run():
            self.status("Settings saved")


def main(dpa=None):
    app = App(dpa)
    app.mainloop()
