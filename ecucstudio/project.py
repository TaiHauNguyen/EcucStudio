"""DaVinci project (.dpa) reader and editable ECUC value model."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from lxml import etree

from . import arxml
from .arxml import XmlFile, q, text

MODULE_TAG = "ECUC-MODULE-CONFIGURATION-VALUES"
CONTAINER_TAG = "ECUC-CONTAINER-VALUE"
VALUE_TAGS = {"ECUC-NUMERICAL-PARAM-VALUE", "ECUC-TEXTUAL-PARAM-VALUE",
              "ECUC-REFERENCE-VALUE", "ECUC-INSTANCE-REFERENCE-VALUE",
              "ECUC-ADD-INFO-PARAM-VALUE"}
REF_VALUE_TAGS = {"ECUC-REFERENCE-VALUE", "ECUC-INSTANCE-REFERENCE-VALUE"}

# Element order inside an ECUC-CONTAINER-VALUE (AUTOSAR 4 schema)
CONTAINER_ORDER = ["SHORT-NAME", "SHORT-NAME-FRAGMENTS", "LONG-NAME", "DESC", "CATEGORY",
                   "ADMIN-DATA", "INTRODUCTION", "ANNOTATIONS", "INDEX", "DEFINITION-REF",
                   "PARAMETER-VALUES", "REFERENCE-VALUES", "SUB-CONTAINERS", "VARIATION-POINT"]
VALUE_ORDER = ["DEFINITION-REF", "ANNOTATIONS", "INDEX", "IS-AUTO-VALUE", "VALUE", "VALUE-REF",
               "VALUE-IREF", "VARIATION-POINT"]


# ============================================================================
# .dpa
# ============================================================================

@dataclass
class DpaProject:
    path: str
    name: str = ""
    author: str = ""
    derivative: str = ""
    compiler: str = ""
    sip_ids: list[str] = field(default_factory=list)
    target_type: str = ""
    version: str = ""
    sip_dir: str = ""
    gendata_dir: str = ""
    source_dir: str = ""
    ecuc_dir: str = ""
    log_dir: str = ""
    active_ecuc: str = ""
    initial_ecuc: str = ""
    active_root: str = "/ActiveEcuC/ActiveEcuC"
    splitter: list[tuple[str, list[str]]] = field(default_factory=list)
    add_bswmds: list[str] = field(default_factory=list)
    developer_exe: str = ""
    swcs: list[tuple[str, bool]] = field(default_factory=list)
    acknowledgements: list[dict] = field(default_factory=list)

    @property
    def dir(self) -> str:
        return os.path.dirname(self.path)

    def resolve(self, p: str | None) -> str:
        if not p:
            return ""
        p = p.replace("$(DpaProjectFolder)", self.dir)
        p = os.path.expandvars(p)
        if not os.path.isabs(p):
            p = os.path.join(self.dir, p)
        return os.path.normpath(p)

    def ecuc_files(self) -> list[str]:
        files = []
        if self.active_ecuc:
            files.append(self.active_ecuc)
        for f, _mods in self.splitter:
            if f not in files:
                files.append(f)
        if not self.splitter and self.ecuc_dir and os.path.isdir(self.ecuc_dir):
            for f in sorted(os.listdir(self.ecuc_dir)):
                full = os.path.join(self.ecuc_dir, f)
                if f.endswith(".arxml") and ".Initial." not in f and full not in files:
                    files.append(full)
        return [f for f in files if os.path.exists(f)]


def read_dpa(path: str) -> DpaProject:
    path = os.path.abspath(path)
    root = etree.parse(path, etree.XMLParser(huge_tree=True)).getroot()
    p = DpaProject(path=path)

    def t(xp, default=""):
        e = root.find(xp)
        return e.text.strip() if e is not None and e.text else default

    p.name = t("General/Name", os.path.splitext(os.path.basename(path))[0])
    p.author = t("General/Author")
    p.version = root.get("Version", "")
    p.derivative = t("Environment/Derivative")
    p.compiler = t("Environment/Compiler")
    p.target_type = t("Environment/TargetType")
    p.sip_ids = [e.text.strip() for e in root.findall("Environment/SipIds/SipId") if e.text]
    p.sip_dir = p.resolve(t("Folders/SIP"))
    p.gendata_dir = p.resolve(t("Folders/GenData"))
    p.source_dir = p.resolve(t("Folders/Source"))
    p.ecuc_dir = p.resolve(t("Folders/ECUC"))
    p.log_dir = p.resolve(t("Folders/Logs"))
    p.add_bswmds = [p.resolve(e.text.strip()) for e in root.findall("Folders/AddBswmds/AddBswmd") if e.text]
    p.developer_exe = t("Tools/DEV")
    act = root.find("ECUC/Active")
    if act is not None and act.text:
        p.active_ecuc = p.resolve(act.text.strip())
        p.active_root = act.get("RootPackageName", p.active_root)
    der = root.find("ECUC/Derived")
    if der is not None and der.text:
        p.initial_ecuc = p.resolve(der.text.strip())
    if not p.active_ecuc:
        c = t("EcucSplitter/Configuration")
        p.active_ecuc = p.resolve(c) if c else ""
    for sp in root.findall("EcucSplitter/Splitter"):
        f = p.resolve(sp.get("File"))
        mods = [m.get("Name") for m in sp.findall("Module")]
        p.splitter.append((f, mods))
    for c in root.findall("SwctGeneration/Component"):
        p.swcs.append((c.get("Name"), c.get("GenerationEnabled") == "true"))
    for s in root.iter("Settings"):
        if s.get("Name", "").startswith("ACK_"):
            d = s.find("Settings[@Name='DESCRIPTION']")
            if d is None:
                continue
            vals = {x.get("Name"): x.get("Value") for x in d.findall("Setting")}
            p.acknowledgements.append({
                "id": vals.get("a ResultId", ""), "severity": vals.get("b Severity", ""),
                "comment": vals.get("c AcknowledgementComment", ""),
                "description": vals.get("e ResultDescription", "")})
    return p


# ============================================================================
# ECUC model
# ============================================================================

def definition_ref(el) -> str:
    return text(el, "DEFINITION-REF", "") or ""


def def_name(def_path: str) -> str:
    return def_path.rsplit("/", 1)[-1]


def container_children(el):
    """Sub containers of a module configuration or container."""
    grp = el.find(q("CONTAINERS")) if arxml.local(el) == MODULE_TAG else el.find(q("SUB-CONTAINERS"))
    if grp is None:
        return []
    return [c for c in grp if arxml.local(c) == CONTAINER_TAG]


def value_elements(el):
    out = []
    for g in ("PARAMETER-VALUES", "REFERENCE-VALUES"):
        grp = el.find(q(g))
        if grp is not None:
            out.extend(c for c in grp if arxml.local(c) in VALUE_TAGS)
    return out


def raw_value(v) -> str | None:
    """Stored text of a parameter/reference value element."""
    t_ = arxml.local(v)
    if t_ == "ECUC-REFERENCE-VALUE":
        r = v.find(q("VALUE-REF"))
        return r.text.strip() if r is not None and r.text else None
    if t_ == "ECUC-INSTANCE-REFERENCE-VALUE":
        r = v.find(q("VALUE-IREF"))
        if r is None:
            return None
        t2 = r.find(q("TARGET-REF"))
        return t2.text.strip() if t2 is not None and t2.text else None
    val = v.find(q("VALUE"))
    if val is None:
        return None
    return "".join(val.itertext()).strip() if len(val) else (val.text or "")


def annotations(el) -> list[str]:
    a = el.find(q("ANNOTATIONS"))
    if a is None:
        return []
    return [x.text.strip() for x in a.iter(q("ANNOTATION-ORIGIN")) if x.text]


def is_user_defined(v) -> bool:
    return "DV:UserDefined" in annotations(v)


def is_auto_value(v) -> bool:
    return text(v, "IS-AUTO-VALUE") == "true"


class EcucModel:
    """All ECUC module configurations of a project, editable in place."""

    def __init__(self):
        self.files: dict[str, XmlFile] = {}
        self.modules: list = []                 # ECUC-MODULE-CONFIGURATION-VALUES elements
        self.module_file: dict = {}             # element -> XmlFile
        self.path_index: dict[str, object] = {} # AR path -> module/container element
        self.def_index: dict[str, list] = {}    # definition path -> container elements
        self.active_modules: set[str] = set()   # module paths listed in the value collection
        self.undo_stack: list = []
        self.redo_stack: list = []
        self._serial = 0          # id of the last recorded operation
        self._clean_serial = 0    # operation id at the last load/save
        self.listeners: list = []
        self._ref_index = None                  # target path -> [ref value elements]
        self.initial: dict[str, object] = {}    # initial ecuc: path -> element
        self.initial_values: dict[tuple, str] = {}
        self.initial_prefix: str | None = None
        self.initial_soft: set[tuple] = set()   # (rel, def) annotated DV:SoftDerived

    # ------------------------------------------------------------ loading
    def load(self, files, progress=None):
        for i, f in enumerate(files):
            xf = XmlFile(f)
            self.files[xf.path] = xf
            if progress:
                progress(i + 1, len(files), f)
        self.reindex()
        return self

    def reindex(self):
        self.modules = []
        self.module_file = {}
        self.path_index = {}
        self.def_index = {}
        self.active_modules = set()
        for xf in self.files.values():
            root = xf.root
            for path, el in arxml.iter_identifiables(root, {MODULE_TAG, CONTAINER_TAG, "ECUC-VALUE-COLLECTION"}):
                tag = arxml.local(el)
                if tag == "ECUC-VALUE-COLLECTION":
                    for r in el.iter(q("ECUC-MODULE-CONFIGURATION-VALUES-REF")):
                        if r.text:
                            self.active_modules.add(r.text.strip())
                    continue
                self.path_index[path] = el
                if tag == MODULE_TAG:
                    self.modules.append(el)
                    self.module_file[el] = xf
                self.def_index.setdefault(definition_ref(el), []).append(el)
        self.modules.sort(key=lambda m: (arxml.short_name(m) or "").lower())
        self._ref_index = None

    def load_initial(self, path: str):
        """Load the derived (InitialEcuC) configuration for 'derived' state and Cfg00020 checks."""
        if not path or not os.path.exists(path):
            return
        xf = XmlFile(path)
        base = None
        for p, el in arxml.iter_identifiables(xf.root, {MODULE_TAG, CONTAINER_TAG}):
            if base is None:
                base = p.rsplit("/", 1)[0]
                self.initial_prefix = base
            rel = p[len(base):]
            self.initial[rel] = el
            for v in value_elements(el):
                key = (rel, definition_ref(v))
                self.initial_values.setdefault(key, raw_value(v))
                if "DV:SoftDerived" in annotations(v):
                    self.initial_soft.add(key)

    # ------------------------------------------------------------ queries
    def file_of(self, el) -> XmlFile | None:
        root = el.getroottree().getroot()
        for xf in self.files.values():
            if xf.root is root:
                return xf
        return None

    def path_of(self, el) -> str:
        return arxml.ar_path(el)

    def module_of(self, el):
        while el is not None and arxml.local(el) != MODULE_TAG:
            el = el.getparent()
        return el

    def relative_path(self, el) -> str:
        """Path without the package prefix, e.g. /Com/ComConfig/X (used to match InitialEcuC)."""
        p = self.path_of(el)
        mod = self.module_of(el)
        mp = self.path_of(mod)
        return p[len(mp.rsplit("/", 1)[0]):]

    def resolve(self, path: str):
        return self.path_index.get(path)

    def is_active_module(self, path: str) -> bool:
        if not self.active_modules:
            return True
        mod = "/".join(path.split("/")[:3])
        return mod in self.active_modules

    def containers_of_def(self, def_path: str, defs=None):
        """All containers whose definition matches *def_path* (normalised via *defs*)."""
        res = list(self.def_index.get(def_path, []))
        if defs is not None:
            std = defs.standard_path(def_path)
            for dp, els in self.def_index.items():
                if dp != def_path and defs.standard_path(dp) == std:
                    res.extend(els)
        return res

    def ref_index(self):
        if self._ref_index is None:
            idx = {}
            for xf in self.files.values():
                for r in xf.root.iter(q("VALUE-REF")):
                    if r.text:
                        idx.setdefault(r.text.strip(), []).append(r.getparent())
            self._ref_index = idx
        return self._ref_index

    def references_to(self, path: str):
        return self.ref_index().get(path, [])

    def dirty_files(self):
        return [f for f in self.files.values() if f.dirty]

    def _top_serial(self):
        return self.undo_stack[-1][3] if self.undo_stack else 0

    def is_dirty(self) -> bool:
        """True if the model differs from the last load/save (undo back to it => clean)."""
        if not any(f.dirty for f in self.files.values()):
            return False
        return self._top_serial() != self._clean_serial or self._unrecorded

    _unrecorded = False

    # ------------------------------------------------------------ change notification
    def _touch(self, el, kind="changed"):
        xf = self.file_of(el)
        if xf is not None:
            xf.dirty = True
        for fn in list(self.listeners):
            try:
                fn(kind, el)
            except Exception:  # listeners must never break editing
                pass

    def _record(self, undo, redo, label):
        self._serial += 1
        self.undo_stack.append((undo, redo, label, self._serial))
        self.redo_stack.clear()
        if len(self.undo_stack) > 500:
            self.undo_stack.pop(0)

    def undo(self):
        if not self.undo_stack:
            return None
        op = self.undo_stack.pop()
        op[0]()
        self.redo_stack.append(op)
        return op[2]

    def redo(self):
        if not self.redo_stack:
            return None
        op = self.redo_stack.pop()
        op[1]()
        self.undo_stack.append(op)
        return op[2]

    # ------------------------------------------------------------ structural helpers
    @staticmethod
    def _ensure_group(el, group: str, order):
        g = el.find(q(group))
        if g is not None:
            return g
        g = arxml.make(group)
        # find position according to schema order
        pos = order.index(group)
        kids = [c for c in el if isinstance(c.tag, str)]
        idx = len(kids)
        for i, c in enumerate(kids):
            ln = arxml.local(c)
            if ln in order and order.index(ln) > pos:
                idx = i
                break
        arxml.insert_child(el, g, idx)
        return g

    def _insert_restorable(self, parent, el, index):
        arxml.insert_child(parent, el, index)

    def _detach(self, el):
        parent = el.getparent()
        kids = [c for c in parent if isinstance(c.tag, str)]
        idx = kids.index(el)
        arxml.remove_child(el)
        return parent, idx

    # ------------------------------------------------------------ editing: values
    def find_values(self, container, def_path: str, defs=None):
        res = []
        for v in value_elements(container):
            d = definition_ref(v)
            if d == def_path or (defs is not None and defs.same_definition(d, def_path)):
                res.append(v)
        return res

    def set_value(self, container, pdef, value: str, index: int = 0, dest: str | None = None,
                  record=True):
        """Create or update the *index*-th instance of parameter/reference *pdef* in *container*."""
        vals = self.find_values(container, pdef.path)
        if index < len(vals):
            v = vals[index]
            old = raw_value(v)
            self._write_value(v, pdef, value, dest)
            self._touch(v)
            if record:
                self._record(lambda: (self._write_value(v, pdef, old, dest), self._touch(v)),
                             lambda: (self._write_value(v, pdef, value, dest), self._touch(v)),
                             f"Set {pdef.name}")
            if pdef.is_ref:
                self._ref_index = None
            return v
        v = self._new_value(pdef, value, dest)
        grp_name = "REFERENCE-VALUES" if pdef.is_ref else "PARAMETER-VALUES"
        grp = self._ensure_group(container, grp_name, CONTAINER_ORDER)
        idx = self._value_position(grp, pdef)
        arxml.insert_child(grp, v, idx)
        self._touch(v, "added")
        self._ref_index = None
        if record:
            def undo():
                self._detach(v)
                self._touch(container, "removed")

            def redo():
                g = self._ensure_group(container, grp_name, CONTAINER_ORDER)
                arxml.insert_child(g, v, self._value_position(g, pdef))
                self._touch(v, "added")
            self._record(undo, redo, f"Create {pdef.name}")
        return v

    @staticmethod
    def _value_position(grp, pdef):
        """Insert position that follows the definition order of the container."""
        order = [c.path for c in pdef.parent.children] if pdef.parent else []
        my = order.index(pdef.path) if pdef.path in order else len(order)
        kids = [c for c in grp if isinstance(c.tag, str)]
        for i, c in enumerate(kids):
            d = definition_ref(c)
            if d in order and order.index(d) > my:
                return i
        return len(kids)

    def _new_value(self, pdef, value, dest=None):
        v = arxml.make(pdef.value_tag)
        arxml.sub(v, "DEFINITION-REF", pdef.path, {"DEST": pdef.tag})
        if pdef.is_ref:
            if pdef.kind == "instance":
                iref = arxml.sub(v, "VALUE-IREF")
                arxml.sub(iref, "TARGET-REF", value or "", {"DEST": dest or pdef.dest_type or ""})
            else:
                arxml.sub(v, "VALUE-REF", value or "", {"DEST": dest or self._ref_dest(pdef)})
        else:
            arxml.sub(v, "VALUE", value if value is not None else "")
        return v

    @staticmethod
    def _ref_dest(pdef) -> str:
        if pdef.kind == "foreign":
            return pdef.dest_type or ""
        return "ECUC-CONTAINER-VALUE"

    def _write_value(self, v, pdef, value, dest=None):
        tag = arxml.local(v)
        if tag == "ECUC-REFERENCE-VALUE":
            r = v.find(q("VALUE-REF"))
            if r is None:
                r = arxml.make("VALUE-REF")
                arxml.insert_child(v, r)
            r.text = value or ""
            if dest:
                r.set("DEST", dest)
            elif not r.get("DEST"):
                r.set("DEST", self._ref_dest(pdef))
        elif tag == "ECUC-INSTANCE-REFERENCE-VALUE":
            iref = v.find(q("VALUE-IREF"))
            if iref is None:
                iref = arxml.make("VALUE-IREF")
                arxml.insert_child(v, iref)
            t = iref.find(q("TARGET-REF"))
            if t is None:
                t = arxml.make("TARGET-REF")
                arxml.insert_child(iref, t)
            t.text = value or ""
        else:
            val = v.find(q("VALUE"))
            if val is None:
                val = arxml.make("VALUE")
                arxml.insert_child(v, val)
            for c in list(val):
                val.remove(c)
            val.text = "" if value is None else value
            auto = v.find(q("IS-AUTO-VALUE"))
            if auto is not None and auto.text == "true":
                # a manual edit ends the "auto calculated" state
                auto.text = "false"

    def delete_element(self, el, label=None):
        """Delete a value or container element (undoable)."""
        is_cont = arxml.local(el) == CONTAINER_TAG
        ppath = self.path_of(el.getparent())
        if is_cont:
            self._index_subtree(el, False, ppath)
        parent, idx = self._detach(el)
        self._ref_index = None
        self._touch(parent, "removed")

        def undo():
            arxml.insert_child(parent, el, idx)
            if is_cont:
                self._index_subtree(el, True, ppath)
            self._ref_index = None
            self._touch(el, "added")

        def redo():
            if is_cont:
                self._index_subtree(el, False, ppath)
            self._detach(el)
            self._ref_index = None
            self._touch(parent, "removed")
        self._record(undo, redo, label or f"Delete {arxml.short_name(el) or def_name(definition_ref(el))}")

    def set_user_defined(self, v, flag: bool):
        def apply(on):
            anns = v.find(q("ANNOTATIONS"))
            present = [a for a in (anns.findall(q("ANNOTATION")) if anns is not None else [])
                       if text(a, "ANNOTATION-ORIGIN") == "DV:UserDefined"]
            if on and not present:
                if anns is None:
                    anns = self._ensure_group(v, "ANNOTATIONS", VALUE_ORDER)
                a = arxml.make("ANNOTATION")
                arxml.sub(a, "ANNOTATION-ORIGIN", "DV:UserDefined")
                arxml.insert_child(anns, a)
            elif not on:
                for a in present:
                    arxml.remove_child(a)
                if anns is not None and not [c for c in anns if isinstance(c.tag, str)]:
                    arxml.remove_child(anns)
            self._touch(v)
        before = is_user_defined(v)
        apply(flag)
        self._record(lambda: apply(before), lambda: apply(flag),
                     "Set user-defined" if flag else "Remove user-defined")

    # ------------------------------------------------------------ editing: containers
    def unique_name(self, parent, base: str) -> str:
        names = {arxml.short_name(c) for c in container_children(parent)}
        # AUTOSAR requires unique short names within the whole module for referencing
        if base not in names:
            return base
        i = 1
        while f"{base}_{i:03d}" in names:
            i += 1
        return f"{base}_{i:03d}"

    def add_container(self, parent, cdef, name: str | None = None, with_defaults=True, record=True,
                      prepare=None):
        """Create a container instance of *cdef* below *parent* (module or container).

        *prepare(el)* may complete the new element before it is inserted (recommended /
        pre-configuration values), so the whole creation is a single undo step.
        """
        name = name or self.unique_name(parent, cdef.name)
        el = self._build_container(cdef, name, with_defaults)
        if prepare is not None:
            prepare(el)
        grp_name = "CONTAINERS" if arxml.local(parent) == MODULE_TAG else "SUB-CONTAINERS"

        def group():
            if grp_name == "CONTAINERS":
                g = parent.find(q("CONTAINERS"))
                if g is None:
                    g = arxml.make("CONTAINERS")
                    arxml.insert_child(parent, g)
                return g
            return self._ensure_group(parent, grp_name, CONTAINER_ORDER)

        ppath = self.path_of(parent)
        arxml.insert_child(group(), el)
        self._index_subtree(el, True, ppath)
        self._touch(el, "added")
        if record:
            def undo():
                self._index_subtree(el, False, ppath)
                self._detach(el)
                self._touch(parent, "removed")

            def redo():
                arxml.insert_child(group(), el)
                self._index_subtree(el, True, ppath)
                self._touch(el, "added")
            self._record(undo, redo, f"Add {cdef.name}")
        return el

    def _build_container(self, cdef, name, with_defaults):
        el = arxml.make(CONTAINER_TAG, attrib={"UUID": arxml.new_uuid()})
        arxml.sub(el, "SHORT-NAME", name)
        arxml.sub(el, "DEFINITION-REF", cdef.path, {"DEST": cdef.tag})
        if not with_defaults:
            return el
        params = []
        refs = []
        subs = []
        for c in cdef.children:
            if c.is_container:
                if c.lower >= 1 and cdef.kind != "choice":
                    for i in range(c.lower):
                        subs.append(self._build_container(c, c.name if i == 0 else f"{c.name}_{i:03d}", True))
            elif c.lower >= 1:
                if c.is_ref:
                    continue  # target is unknown; validation will ask for it
                if c.default is not None:
                    params.append(self._new_value(c, c.default))
        if cdef.kind == "choice":
            subs = []
        if params:
            g = arxml.sub(el, "PARAMETER-VALUES")
            for p in params:
                g.append(p)
        if refs:
            g = arxml.sub(el, "REFERENCE-VALUES")
            for r in refs:
                g.append(r)
        if subs:
            g = arxml.sub(el, "SUB-CONTAINERS")
            for s in subs:
                g.append(s)
        return el

    # ------------------------------------------------------------ editing: modules
    def value_collection(self):
        """(ECUC-VALUE-COLLECTION element or None, XmlFile, AR-PACKAGE element) that receives new modules."""
        for xf in self.files.values():
            for c in xf.root.iter(q("ECUC-VALUE-COLLECTION")):
                pkg = c.getparent().getparent()
                return c, xf, pkg
        if self.modules:
            m = self.modules[0]
            return None, self.module_file[m], m.getparent().getparent()
        return None, None, None

    def module_by_name(self, name):
        for m in self.modules:
            if arxml.short_name(m) == name:
                return m
        return None

    def add_module(self, mod_el, record=True):
        """Insert a new ECUC-MODULE-CONFIGURATION-VALUES and list it in the value collection."""
        coll, xf, pkg = self.value_collection()
        if pkg is None:
            raise ValueError("No ECUC file to add the module to")
        elements = pkg.find(q("ELEMENTS"))
        if elements is None:
            elements = arxml.make("ELEMENTS")
            arxml.insert_child(pkg, elements)
        pkg_path = arxml.ar_path(pkg)
        mpath = pkg_path + "/" + arxml.short_name(mod_el)
        ref_cond = None
        if coll is not None:
            vals = coll.find(q("ECUC-VALUES"))
            if vals is None:
                vals = arxml.make("ECUC-VALUES")
                arxml.insert_child(coll, vals)
            ref_cond = arxml.make("ECUC-MODULE-CONFIGURATION-VALUES-REF-CONDITIONAL")
            arxml.sub(ref_cond, "ECUC-MODULE-CONFIGURATION-VALUES-REF", mpath,
                      {"DEST": "ECUC-MODULE-CONFIGURATION-VALUES"})

        def attach():
            arxml.insert_child(elements, mod_el)
            if ref_cond is not None:
                arxml.insert_child(coll.find(q("ECUC-VALUES")), ref_cond)
                self.active_modules.add(mpath)
            self.modules.append(mod_el)
            self.modules.sort(key=lambda m: (arxml.short_name(m) or "").lower())
            self.module_file[mod_el] = xf
            self.path_index[mpath] = mod_el
            self.def_index.setdefault(definition_ref(mod_el), []).append(mod_el)
            self._index_subtree(mod_el, True, pkg_path)
            self._touch(mod_el, "added")

        def detach():
            self._index_subtree(mod_el, False, pkg_path)
            self.path_index.pop(mpath, None)
            lst = self.def_index.get(definition_ref(mod_el))
            if lst and mod_el in lst:
                lst.remove(mod_el)
            if mod_el in self.modules:
                self.modules.remove(mod_el)
            self.module_file.pop(mod_el, None)
            self._detach(mod_el)
            if ref_cond is not None and ref_cond.getparent() is not None:
                self._detach(ref_cond)
                self.active_modules.discard(mpath)
            xf.dirty = True
            self._touch(pkg, "removed")

        attach()
        if record:
            self._record(detach, attach, f"Add module {arxml.short_name(mod_el)}")
        return mod_el

    def remove_module(self, mod_el):
        coll, _xf, _pkg = self.value_collection()
        mpath = self.path_of(mod_el)
        pkg = mod_el.getparent().getparent()
        pkg_path = arxml.ar_path(pkg)
        xf = self.module_file.get(mod_el) or self.file_of(mod_el)
        ref_cond = None
        if coll is not None:
            for r in coll.iter(q("ECUC-MODULE-CONFIGURATION-VALUES-REF")):
                if r.text and r.text.strip() == mpath:
                    ref_cond = r.getparent()
                    break
        state = {}

        def detach():
            self._index_subtree(mod_el, False, pkg_path)
            self.path_index.pop(mpath, None)
            lst = self.def_index.get(definition_ref(mod_el))
            if lst and mod_el in lst:
                lst.remove(mod_el)
            if mod_el in self.modules:
                self.modules.remove(mod_el)
            self.module_file.pop(mod_el, None)
            state["mod"] = self._detach(mod_el)
            if ref_cond is not None:
                state["ref"] = self._detach(ref_cond)
            self.active_modules.discard(mpath)
            xf.dirty = True
            self._ref_index = None
            for fn in list(self.listeners):
                fn("removed", pkg)

        def attach():
            parent, idx = state["mod"]
            arxml.insert_child(parent, mod_el, idx)
            if "ref" in state:
                rp, ri = state["ref"]
                arxml.insert_child(rp, ref_cond, ri)
                self.active_modules.add(mpath)
            self.modules.append(mod_el)
            self.modules.sort(key=lambda m: (arxml.short_name(m) or "").lower())
            self.module_file[mod_el] = xf
            self.path_index[mpath] = mod_el
            self.def_index.setdefault(definition_ref(mod_el), []).append(mod_el)
            self._index_subtree(mod_el, True, pkg_path)
            self._touch(mod_el, "added")

        detach()
        self._record(attach, detach, f"Remove module {arxml.short_name(mod_el)}")

    def rename_container(self, el, new_name: str):
        """Rename a container and update every reference that points into it."""
        old_path = self.path_of(el)
        sn = el.find(q("SHORT-NAME"))
        old_name = sn.text
        parent_path = old_path.rsplit("/", 1)[0]
        new_path = parent_path + "/" + new_name

        def apply(frm, to, name):
            refs_snapshot = list(self.ref_index().items())
            self._index_subtree(el, False, parent_path)
            sn.text = name
            self._index_subtree(el, True, parent_path)
            for tgt, refs in refs_snapshot:
                if tgt == frm or tgt.startswith(frm + "/"):
                    for rv in refs:
                        r = rv.find(q("VALUE-REF"))
                        r.text = to + tgt[len(frm):]
                        self._touch(rv)
            self._ref_index = None
            self._touch(el)

        apply(old_path, new_path, new_name)
        self._record(lambda: apply(new_path, old_path, old_name),
                     lambda: apply(old_path, new_path, new_name), f"Rename {old_name}")

    def _index_subtree(self, el, add: bool, parent_path: str):
        """Incrementally add/remove *el* and its descendants in the path/definition indexes."""
        for path, e in arxml.iter_identifiables(el, {CONTAINER_TAG}, parent_path):
            d = definition_ref(e)
            if add:
                self.path_index[path] = e
                self.def_index.setdefault(d, []).append(e)
            else:
                self.path_index.pop(path, None)
                lst = self.def_index.get(d)
                if lst and e in lst:
                    lst.remove(e)
        self._ref_index = None

    # ------------------------------------------------------------ save
    def save(self, backup=True):
        saved = []
        for xf in self.files.values():
            if xf.dirty:
                xf.save(backup=backup)
                saved.append(xf.path)
        self._clean_serial = self._top_serial()
        self._unrecorded = False
        return saved


_CE = re.compile(r"^(?P<path>.*?)(?:\[(?P<idx>\d+):(?P<param>[^\]]+)\])?(?:\(value=.*\))?$")


def parse_object_ref(obj: str):
    """Split a DaVinci CE object string like ``/ActiveEcuC/Com/ComGeneral[0:ComX]``."""
    m = _CE.match(obj.strip()) if obj else None
    if not m:
        return obj, None, None
    idx = m.group("idx")
    return m.group("path"), m.group("param"), int(idx) if idx is not None else None
