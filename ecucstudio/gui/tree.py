"""Project tree (DaVinci *Basic Editor* context tree): modules -> containers, lazily populated."""
from __future__ import annotations

import tkinter as tk
from collections import defaultdict
from dataclasses import dataclass
from tkinter import ttk

from .. import arxml
from ..project import MODULE_TAG, container_children, definition_ref
from .theme import COLORS, SEVERITY_TAG
from .widgets import FilterEntry


@dataclass
class Node:
    kind: str                 # module | container | group | search
    el: object = None         # lxml element (module/container) or parent element for groups
    cdef: object = None       # definition of the element / of the group's instances
    loaded: bool = False


class ProjectTree(tk.Frame):
    """Context tree of one editor; *modules* limits it to some module configurations."""

    def __init__(self, master, app, editor=None, modules=None):
        super().__init__(master, background=COLORS["view_bg"])
        self.app = app
        self.editor = editor
        self.modules = modules
        self.nodes: dict[str, Node] = {}
        self.by_el: dict = {}
        self.marks: dict = {}           # element -> max severity
        self._counter = 0
        self.search_mode = False

        bar = tk.Frame(self, background=COLORS["view_bg"])
        bar.pack(fill="x", padx=2, pady=2)
        ent = FilterEntry(bar)
        ent.pack(side="left", fill="x", expand=True)
        ent.bind("<Return>", lambda e: self.search())
        ent.bind("<Escape>", lambda e: self.clear_search())
        self.filter_entry = ent

        frm = tk.Frame(self, background=COLORS["view_bg"])
        frm.pack(fill="both", expand=True)
        self.tv = ttk.Treeview(frm, show="tree", selectmode="browse")
        ys = ttk.Scrollbar(frm, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=ys.set)
        self.tv.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        self.tv.column("#0", width=260, stretch=True)
        for tag in ("error", "warning", "info", "improvement"):
            self.tv.tag_configure(tag, foreground=COLORS[tag])
        self.tv.tag_configure("unknown", foreground=COLORS["notinst"])
        self.tv.tag_configure("dirty", font=("Segoe UI", 9, "italic"))
        self.tv.bind("<<TreeviewOpen>>", self._on_open)
        self.tv.bind("<<TreeviewSelect>>", self._on_select)
        self.tv.bind("<Button-3>", self._on_menu)
        self.tv.bind("<Delete>", lambda e: self.app.delete_selected_container())
        self.tv.bind("<F2>", lambda e: self.app.rename_selected_container())

    # --------------------------------------------------------------- building
    def _iid(self):
        self._counter += 1
        return f"n{self._counter}"

    def clear(self):
        self.tv.delete(*self.tv.get_children(""))
        self.nodes.clear()
        self.by_el.clear()

    def populate(self):
        self.clear()
        self.search_mode = False
        model, defs = self.app.session.model, self.app.session.defs
        for m in model.modules:
            if self.modules is not None and m not in self.modules:
                continue
            mdef = defs.module(definition_ref(m))
            self._add(parent="", node=Node("module", m, mdef),
                      text=arxml.short_name(m), image=self.app.icons.module if mdef else self.app.icons.unknown)

    def _add(self, parent, node, text, image=None, index="end"):
        iid = self._iid()
        self.nodes[iid] = node
        if node.kind in ("module", "container") and node.el is not None:
            self.by_el[node.el] = iid
        tags = self._tags(node)
        kw = {"text": text, "tags": tags}
        if image is not None:
            kw["image"] = image
        self.tv.insert(parent, index, iid=iid, **kw)
        if self._has_children(node):
            self.tv.insert(iid, "end", iid=iid + "_dummy", text="…")
        return iid

    def _has_children(self, node):
        if node.kind == "group":
            return True
        if node.kind in ("module", "container"):
            return bool(container_children(node.el))
        return False

    def _tags(self, node):
        tags = []
        if node.cdef is None and node.kind != "group":
            tags.append("unknown")
        sev = None
        if node.kind == "group":
            sev = self.marks.get(("group", node.el, node.cdef.path if node.cdef else None))
        elif node.el is not None:
            sev = self.marks.get(node.el)
        if sev is not None:
            tags.append(SEVERITY_TAG[int(sev)])
        return tuple(tags)

    def _children_spec(self, node):
        """(Node, text, image) tuples for the children of *node*."""
        defs = self.app.session.defs
        icons = self.app.icons
        out = []
        if node.kind == "group":
            for el in node.el_list:
                out.append((Node("container", el, node.cdef), arxml.short_name(el), self._icon(node.cdef)))
            return out
        el = node.el
        cdef = self.app.session.container_def(el)[0]   # SIP BSWMD or fallback definition
        node.cdef = cdef
        subs = container_children(el)
        if cdef is None:
            for s in subs:
                out.append((Node("container", s, None), arxml.short_name(s), icons.unknown))
            return out
        by_def = defaultdict(list)
        for s in subs:
            by_def[definition_ref(s)].append(s)
        used = set()
        for cd in cdef.containers():
            insts = list(by_def.get(cd.path, []))
            used.add(cd.path)
            for d, lst in by_def.items():
                if d not in used and d != cd.path and defs.same_definition(d, cd.path):
                    insts.extend(lst)
                    used.add(d)
            if cd.upper > 1 and (insts or cd.lower > 0):
                g = Node("group", el, cd)
                g.el_list = insts
                out.append((g, f"{cd.name}  [{len(insts)}]", icons.group))
            else:
                for s in insts:
                    out.append((Node("container", s, cd), arxml.short_name(s), self._icon(cd)))
        for d, lst in by_def.items():
            if d not in used:
                for s in lst:
                    out.append((Node("container", s, None), arxml.short_name(s), icons.unknown))
        return out

    def _icon(self, cdef):
        if cdef is None:
            return self.app.icons.unknown
        return self.app.icons.choice if cdef.kind == "choice" else self.app.icons.container

    def _load_children(self, iid):
        node = self.nodes.get(iid)
        if node is None or node.loaded:
            return
        node.loaded = True
        dummy = iid + "_dummy"
        if self.tv.exists(dummy):
            self.tv.delete(dummy)
        for child, text, img in self._children_spec(node):
            self._add(iid, child, text, img)

    def _on_open(self, _e=None):
        iid = self.tv.focus()
        if iid:
            self._load_children(iid)

    def _on_select(self, _e=None):
        sel = self.tv.selection()
        if not sel:
            return
        node = self.nodes.get(sel[0])
        if node is not None and self.editor is not None and self.editor.node is not node:
            self.editor.show_node(node)

    # ------------------------------------------------------------- navigation
    def select_element(self, el):
        """Expand the tree down to *el* (module or container) and select it."""
        if el is None:
            return False
        chain = []
        e = el
        while e is not None:
            if arxml.local(e) in (MODULE_TAG, "ECUC-CONTAINER-VALUE"):
                chain.append(e)
            if arxml.local(e) == MODULE_TAG:
                break
            e = e.getparent()
        chain.reverse()
        if self.search_mode:
            self.clear_search()
        parent_iid = None
        for target in chain:
            iid = self.by_el.get(target)
            if iid is None and parent_iid is not None:
                self._load_children(parent_iid)
                iid = self.by_el.get(target)
                if iid is None:
                    # the element may sit inside a group node
                    for gid in self.tv.get_children(parent_iid):
                        n = self.nodes.get(gid)
                        if n and n.kind == "group" and target in n.el_list:
                            self._load_children(gid)
                            self.tv.item(gid, open=True)
                            iid = self.by_el.get(target)
                            break
            if iid is None:
                return False
            if parent_iid is not None:
                self.tv.item(parent_iid, open=True)
            parent_iid = iid
        if parent_iid:
            self.tv.selection_set(parent_iid)
            self.tv.focus(parent_iid)
            self.tv.see(parent_iid)
            self._on_select()   # render synchronously so callers can select rows right away
            return True
        return False

    def contains(self, el):
        """True if *el* belongs to one of the modules shown in this tree."""
        if self.modules is None:
            return True
        m = el
        while m is not None and arxml.local(m) != MODULE_TAG:
            m = m.getparent()
        return m in self.modules

    def selected_node(self):
        sel = self.tv.selection()
        return self.nodes.get(sel[0]) if sel else None

    def refresh_element(self, el):
        """Rebuild the children of the tree item of *el* (after add/delete/rename)."""
        iid = self.by_el.get(el)
        if iid is None:
            return
        was_open = self.tv.item(iid, "open")
        for c in self.tv.get_children(iid):
            self._forget(c)
            self.tv.delete(c)
        node = self.nodes[iid]
        node.loaded = False
        if arxml.local(el) != MODULE_TAG:
            self.tv.item(iid, text=arxml.short_name(el))
        if self._has_children(node):
            self.tv.insert(iid, "end", iid=iid + "_dummy", text="…")
            if was_open:
                self._load_children(iid)
                self.tv.item(iid, open=True)

    def _forget(self, iid):
        for c in self.tv.get_children(iid):
            self._forget(c)
        n = self.nodes.pop(iid, None)
        if n is not None and n.el is not None and self.by_el.get(n.el) == iid:
            self.by_el.pop(n.el, None)

    # ---------------------------------------------------------------- marks
    def set_marks(self, results):
        marks = {}

        def bump(key, sev):
            if key not in marks or marks[key] < sev:
                marks[key] = sev
        for r in results:
            if r.acknowledged or r.element is None:
                continue
            e = r.element
            while e is not None:
                t = arxml.local(e)
                if t in (MODULE_TAG, "ECUC-CONTAINER-VALUE"):
                    bump(e, int(r.severity))
                    p = e.getparent()
                    if t == "ECUC-CONTAINER-VALUE" and p is not None:
                        owner = p.getparent()
                        bump(("group", owner, definition_ref(e)), int(r.severity))
                e = e.getparent()
        self.marks = marks
        for iid, node in self.nodes.items():
            if self.tv.exists(iid):
                self.tv.item(iid, tags=self._tags(node))

    # ---------------------------------------------------------------- search
    def search(self):
        text = self.filter_entry.get_text().strip().lower()
        if not text:
            self.clear_search()
            return
        model = self.app.session.model
        hits = [(p, el) for p, el in model.path_index.items() if text in p.rsplit("/", 1)[-1].lower()
                and self.contains(el)]
        hits.sort(key=lambda x: x[0])
        self.clear()
        self.search_mode = True
        if not hits:
            self.tv.insert("", "end", text=f"No container matches '{text}'")
            return
        for p, el in hits[:1000]:
            cdef = self.app.session.defs.find(definition_ref(el))
            iid = self._iid()
            self.nodes[iid] = Node("container" if arxml.local(el) != MODULE_TAG else "module", el, cdef, True)
            self.tv.insert("", "end", iid=iid, text=p, image=self._icon(cdef))
        if len(hits) > 1000:
            self.tv.insert("", "end", text=f"… {len(hits) - 1000} more, refine the filter")

    def clear_search(self):
        if self.search_mode:
            self.filter_entry.set_text("")
            self.populate()
            self.set_marks(self.app.all_results())

    # ------------------------------------------------------------------ menu
    def _on_menu(self, e):
        iid = self.tv.identify_row(e.y)
        if not iid or iid not in self.nodes:
            return
        self.tv.selection_set(iid)
        node = self.nodes[iid]
        self.app.container_menu(node, e.x_root, e.y_root)
