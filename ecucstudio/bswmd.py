"""BSWMD (module definition) repository.

Loads ECUC-MODULE-DEF trees from the SIP (``Core/Components/*/BSWMD``) and any
additional BSWMD folders of the project. Files are indexed once (cached on disk)
and module definitions are parsed lazily on first use.
"""
from __future__ import annotations

import json
import math
import os
import threading
from dataclasses import dataclass, field

from lxml import etree

from . import arxml
from .arxml import NS, q, text

PARAM_KINDS = {
    "ECUC-BOOLEAN-PARAM-DEF": "boolean",
    "ECUC-INTEGER-PARAM-DEF": "integer",
    "ECUC-FLOAT-PARAM-DEF": "float",
    "ECUC-ENUMERATION-PARAM-DEF": "enum",
    "ECUC-STRING-PARAM-DEF": "string",
    "ECUC-MULTILINE-STRING-PARAM-DEF": "multiline",
    "ECUC-FUNCTION-NAME-DEF": "function",
    "ECUC-LINKER-SYMBOL-DEF": "linker",
    "ECUC-ADD-INFO-PARAM-DEF": "addinfo",
}
REF_KINDS = {
    "ECUC-REFERENCE-DEF": "reference",
    "ECUC-SYMBOLIC-NAME-REFERENCE-DEF": "symbolic",
    "ECUC-CHOICE-REFERENCE-DEF": "choice-reference",
    "ECUC-FOREIGN-REFERENCE-DEF": "foreign",
    "ECUC-INSTANCE-REFERENCE-DEF": "instance",
    "ECUC-URI-REFERENCE-DEF": "uri",
}
CONTAINER_KINDS = {
    "ECUC-PARAM-CONF-CONTAINER-DEF": "container",
    "ECUC-CHOICE-CONTAINER-DEF": "choice",
}
NUMERICAL_KINDS = {"boolean", "integer", "float"}
TEXTUAL_KINDS = {"enum", "string", "multiline", "function", "linker", "addinfo"}

# Value element that stores an instance of each definition kind
VALUE_TAG = {k: "ECUC-NUMERICAL-PARAM-VALUE" for k in NUMERICAL_KINDS}
VALUE_TAG.update({k: "ECUC-TEXTUAL-PARAM-VALUE" for k in TEXTUAL_KINDS})
VALUE_TAG.update({"reference": "ECUC-REFERENCE-VALUE", "symbolic": "ECUC-REFERENCE-VALUE",
                  "choice-reference": "ECUC-REFERENCE-VALUE", "foreign": "ECUC-REFERENCE-VALUE",
                  "uri": "ECUC-REFERENCE-VALUE", "instance": "ECUC-INSTANCE-REFERENCE-VALUE"})

KIND_LABEL = {
    "boolean": "Boolean", "integer": "Integer", "float": "Float", "enum": "Enumeration",
    "string": "String", "multiline": "Multiline String", "function": "Function Name",
    "linker": "Linker Symbol", "addinfo": "Add Info", "reference": "Reference",
    "symbolic": "Symbolic Name Reference", "choice-reference": "Choice Reference",
    "foreign": "Foreign Reference", "instance": "Instance Reference", "uri": "URI Reference",
    "container": "Container", "choice": "Choice Container", "module": "Module",
}

INDEX_VERSION = 4


def _num(s: str | None):
    if s is None:
        return None
    s = s.strip()
    low = s.lower()
    if low in ("inf", "+inf", "infinity"):
        return math.inf
    if low in ("-inf", "-infinity"):
        return -math.inf
    try:
        if low.startswith(("0x", "-0x")):
            return int(s, 16)
        if low.startswith("0b"):
            return int(s[2:], 2)
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return None


@dataclass
class Def:
    """One node of a module definition tree."""
    path: str
    name: str
    tag: str
    kind: str
    lower: int = 0
    upper: float = 1  # math.inf for UPPER-MULTIPLICITY-INFINITE
    desc: str = ""
    origin: str = ""
    default: str | None = None
    min: float | int | None = None
    max: float | int | None = None
    min_length: int | None = None
    max_length: int | None = None
    literals: list[str] = field(default_factory=list)
    dest: list[str] = field(default_factory=list)       # container def paths (normalised)
    dest_type: str | None = None                         # foreign reference DESTINATION-TYPE
    symbolic_name_value: bool = False
    requires_index: bool = False
    post_build_changeable: bool | None = None
    config_classes: list[tuple[str, str]] = field(default_factory=list)
    unit: str | None = None
    base_unit: str | None = None
    number_format: str | None = None
    scope: str | None = None
    children: list["Def"] = field(default_factory=list)   # containers: params, refs, sub-containers
    parent: "Def | None" = None
    module: "ModuleDef | None" = None
    uuid: str | None = None

    # -- classification helpers -------------------------------------------
    @property
    def is_container(self) -> bool:
        return self.kind in ("container", "choice")

    @property
    def is_param(self) -> bool:
        return self.kind in PARAM_KINDS.values()

    @property
    def is_ref(self) -> bool:
        return self.kind in REF_KINDS.values()

    @property
    def value_tag(self) -> str | None:
        return VALUE_TAG.get(self.kind)

    @property
    def mandatory(self) -> bool:
        return self.lower >= 1

    @property
    def multi(self) -> bool:
        return self.upper > 1

    def multiplicity_str(self) -> str:
        up = "*" if self.upper == math.inf else str(int(self.upper))
        return f"{self.lower}..{up}"

    def params(self):
        return [c for c in self.children if not c.is_container]

    def containers(self):
        return [c for c in self.children if c.is_container]

    def child(self, name: str):
        for c in self.children:
            if c.name == name:
                return c
        return None

    @property
    def relative(self) -> str:
        """Path relative to the module definition (without module prefix)."""
        mod = self.module
        if mod is None or self is mod:
            return ""
        return self.path[len(mod.path):]


@dataclass
class ModuleDef(Def):
    refined: str | None = None
    supported_variants: list[str] = field(default_factory=list)
    post_build_variant_support: bool | None = None
    file: str = ""
    impl: "BswImpl | None" = None
    index: dict = field(default_factory=dict)   # def path -> Def

    def find(self, path: str):
        return self.index.get(path)


@dataclass
class BswImpl:
    path: str
    module_def: str | None
    file: str
    sw_version: str | None = None
    vendor_id: str | None = None
    ar_release: str | None = None
    preconfig: list[str] = field(default_factory=list)
    recommended: list[str] = field(default_factory=list)


def _sdg_values(el) -> dict:
    out = {}
    admin = el.find(q("ADMIN-DATA"))
    if admin is None:
        return out
    for sd in admin.iter(q("SD")):
        gid = sd.get("GID")
        if gid and sd.text:
            out[gid] = sd.text.strip()
    return out


def _desc(el) -> str:
    d = el.find(q("DESC"))
    if d is None:
        return ""
    parts = []
    for l2 in d.iter(q("L-2")):
        parts.append("".join(l2.itertext()).strip())
    return "\n".join(p for p in parts if p)


class DefinitionRepository:
    """Index of all BSWMD files and lazily parsed module definitions."""

    def __init__(self, sip_dir: str | None, extra_dirs=(), cache_dir: str | None = None):
        self.sip_dir = os.path.abspath(sip_dir) if sip_dir else None
        self.extra_dirs = [os.path.abspath(d) for d in extra_dirs if d]
        self.cache_dir = cache_dir
        self.files: list[str] = []
        # module def path -> {"file":..., "refined":...}
        self.module_index: dict[str, dict] = {}
        # BSW implementation path -> BswImpl
        self.impls: dict[str, BswImpl] = {}
        # ECUC-MODULE-CONFIGURATION-VALUES path (pre/rec configs) -> files defining it
        self.config_values_index: dict[str, list[str]] = {}
        self._modules: dict[str, ModuleDef] = {}
        self._trees: dict[str, etree._ElementTree] = {}
        self._lock = threading.RLock()
        self.refined_to_vendor: dict[str, list[str]] = {}
        self.warnings: list[str] = []

    # ------------------------------------------------------------------ scan
    def bswmd_files(self) -> list[str]:
        files = []
        roots = []
        if self.sip_dir:
            comp = os.path.join(self.sip_dir, "Components")
            if os.path.isdir(comp):
                for name in sorted(os.listdir(comp)):
                    b = os.path.join(comp, name, "BSWMD")
                    if os.path.isdir(b):
                        roots.append(b)
        roots.extend(self.extra_dirs)
        for r in roots:
            if os.path.isfile(r) and r.lower().endswith(".arxml"):
                files.append(r)
                continue
            for dp, _dn, fn in os.walk(r):
                for f in sorted(fn):
                    if f.lower().endswith(".arxml"):
                        files.append(os.path.join(dp, f))
        return files

    def _cache_file(self):
        if not self.cache_dir:
            return None
        os.makedirs(self.cache_dir, exist_ok=True)
        key = abs(hash((self.sip_dir, tuple(self.extra_dirs)))) % (10 ** 10)
        return os.path.join(self.cache_dir, f"bswmd_index_{key}.json")

    def scan(self, progress=None):
        files = self.bswmd_files()
        stamps = {f: os.path.getmtime(f) for f in files}
        cache = self._cache_file()
        cached = {}
        if cache and os.path.exists(cache):
            try:
                with open(cache, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if data.get("version") == INDEX_VERSION:
                    cached = data.get("files", {})
            except (OSError, ValueError):
                cached = {}
        entries = {}
        for i, f in enumerate(files):
            c = cached.get(f)
            if c and c.get("mtime") == stamps[f]:
                entries[f] = c
            else:
                entries[f] = self._index_file(f)
                entries[f]["mtime"] = stamps[f]
            if progress:
                progress(i + 1, len(files), f)
        if cache:
            try:
                with open(cache, "w", encoding="utf-8") as fh:
                    json.dump({"version": INDEX_VERSION, "files": entries}, fh)
            except OSError:
                pass
        self.files = files
        self.module_index.clear()
        self.impls.clear()
        self.config_values_index.clear()
        self.refined_to_vendor.clear()
        for f, e in entries.items():
            for m in e.get("modules", []):
                if m["path"] in self.module_index:
                    continue
                self.module_index[m["path"]] = {"file": f, "refined": m.get("refined")}
                if m.get("refined"):
                    self.refined_to_vendor.setdefault(m["refined"], []).append(m["path"])
            for im in e.get("impls", []):
                self.impls[im["path"]] = BswImpl(file=f, **{k: v for k, v in im.items()})
            for cv in e.get("configs", []):
                self.config_values_index.setdefault(cv, []).append(f)
        return self

    @staticmethod
    def _index_file(path: str) -> dict:
        out = {"modules": [], "impls": [], "configs": []}
        try:
            tree = etree.parse(path, etree.XMLParser(huge_tree=True, remove_comments=True))
        except (etree.XMLSyntaxError, OSError):
            return out
        tags = {"ECUC-MODULE-DEF", "BSW-IMPLEMENTATION", "ECUC-MODULE-CONFIGURATION-VALUES"}
        for p, el in arxml.iter_identifiables(tree.getroot(), tags):
            t = arxml.local(el)
            if t == "ECUC-MODULE-DEF":
                out["modules"].append({"path": p, "refined": text(el, "REFINED-MODULE-DEF-REF")})
            elif t == "BSW-IMPLEMENTATION":
                vs = el.find(q("VENDOR-SPECIFIC-MODULE-DEF-REFS"))
                mdef = None
                if vs is not None:
                    r = vs.find(q("VENDOR-SPECIFIC-MODULE-DEF-REF"))
                    mdef = r.text.strip() if r is not None and r.text else None
                pre = [r.text.strip() for r in el.iter(q("PRECONFIGURED-CONFIGURATION-REF")) if r.text]
                rec = [r.text.strip() for r in el.iter(q("RECOMMENDED-CONFIGURATION-REF")) if r.text]
                out["impls"].append({"path": p, "module_def": mdef, "sw_version": text(el, "SW-VERSION"),
                                     "vendor_id": text(el, "VENDOR-ID"),
                                     "ar_release": text(el, "AR-RELEASE-VERSION"),
                                     "preconfig": pre, "recommended": rec})
            else:
                out["configs"].append(p)
        return out

    # ------------------------------------------------------------ lookups
    def _tree(self, path: str):
        with self._lock:
            t = self._trees.get(path)
            if t is None:
                t = etree.parse(path, etree.XMLParser(huge_tree=True, remove_comments=True))
                self._trees[path] = t
            return t

    def module_paths(self):
        return sorted(self.module_index)

    def module(self, path: str) -> ModuleDef | None:
        with self._lock:
            m = self._modules.get(path)
            if m is not None:
                return m
            info = self.module_index.get(path)
            if info is None:
                return None
            tree = self._tree(info["file"])
            el = None
            for p, e in arxml.iter_identifiables(tree.getroot(), {"ECUC-MODULE-DEF"}):
                if p == path:
                    el = e
                    break
            if el is None:
                return None
            m = self._build_module(el, path, info["file"])
            self._modules[path] = m
            return m

    def module_for(self, def_path: str) -> ModuleDef | None:
        """Module definition that contains *def_path* (longest matching prefix)."""
        best = None
        for mp in self.module_index:
            if def_path == mp or def_path.startswith(mp + "/"):
                if best is None or len(mp) > len(best):
                    best = mp
        return self.module(best) if best else None

    def find(self, def_path: str) -> Def | None:
        m = self.module_for(def_path)
        if m is None:
            return None
        if def_path == m.path:
            return m
        return m.index.get(def_path)

    def standard_path(self, def_path: str) -> str:
        """Map a vendor definition path to the AUTOSAR standard one (via REFINED-MODULE-DEF-REF)."""
        for mp, info in self.module_index.items():
            if (def_path == mp or def_path.startswith(mp + "/")) and info.get("refined"):
                return info["refined"] + def_path[len(mp):]
        return def_path

    def same_definition(self, a: str, b: str) -> bool:
        if a == b:
            return True
        return self.standard_path(a) == self.standard_path(b)

    def impl(self, impl_path: str) -> BswImpl | None:
        return self.impls.get(impl_path)

    def impl_for_module(self, module_def_path: str) -> BswImpl | None:
        for im in self.impls.values():
            if im.module_def == module_def_path:
                return im
        return None

    def config_values(self, path: str, prefer_file: str | None = None):
        """ECUC-MODULE-CONFIGURATION-VALUES element (pre-config / recommended) by AR path.

        Several SIP files may define the same path for different use cases (e.g.
        ``MemMap_ARM_pre.arxml``); the one in *prefer_file* (the BSW implementation's file)
        wins, otherwise the path is ambiguous and ``None`` is returned.
        """
        files = self.config_values_index.get(path) or []
        if prefer_file and prefer_file in files:
            f = prefer_file
        elif len(files) == 1:
            f = files[0]
        else:
            return None
        tree = self._tree(f)
        for p, e in arxml.iter_identifiables(tree.getroot(), {"ECUC-MODULE-CONFIGURATION-VALUES"}):
            if p == path:
                return e
        return None

    # ------------------------------------------------------------ building
    def _build_module(self, el, path: str, file: str) -> ModuleDef:
        m = ModuleDef(path=path, name=arxml.short_name(el), tag="ECUC-MODULE-DEF", kind="module")
        self._fill_common(m, el)
        m.refined = text(el, "REFINED-MODULE-DEF-REF")
        m.supported_variants = [v.text.strip() for v in el.iter(q("SUPPORTED-CONFIG-VARIANT")) if v.text]
        pb = text(el, "POST-BUILD-VARIANT-SUPPORT")
        m.post_build_variant_support = None if pb is None else pb == "true"
        m.file = file
        m.module = m
        m.impl = self.impl_for_module(path)
        m.index[path] = m
        conts = el.find(q("CONTAINERS"))
        if conts is not None:
            for c in conts:
                self._build_node(c, m, m)
        return m

    def _fill_common(self, d: Def, el):
        d.uuid = el.get("UUID")
        d.desc = _desc(el)
        lo = text(el, "LOWER-MULTIPLICITY")
        d.lower = int(lo) if lo and lo.isdigit() else 0
        if (text(el, "UPPER-MULTIPLICITY-INFINITE") or "").lower() in ("true", "1"):
            d.upper = math.inf
        else:
            up = text(el, "UPPER-MULTIPLICITY")
            d.upper = int(up) if up and up.isdigit() else 1
        d.origin = text(el, "ORIGIN", "") or ""
        d.scope = text(el, "SCOPE")
        d.requires_index = text(el, "REQUIRES-INDEX") == "true"
        pbc = text(el, "POST-BUILD-CHANGEABLE")
        d.post_build_changeable = None if pbc is None else pbc == "true"
        cc = []
        for icc in el.iter(q("ECUC-IMPLEMENTATION-CONFIGURATION-CLASS")):
            cc.append((text(icc, "CONFIG-CLASS", ""), text(icc, "CONFIG-VARIANT", "")))
        d.config_classes = cc
        sdg = _sdg_values(el)
        d.unit = sdg.get("DV:Unit")
        d.base_unit = sdg.get("DV:BaseUnit")
        d.number_format = sdg.get("DV:DefaultFormat")

    def _build_node(self, el, parent: Def, module: ModuleDef):
        tag = arxml.local(el)
        name = arxml.short_name(el)
        if not name:
            return
        path = parent.path + "/" + name
        if tag in CONTAINER_KINDS:
            d = Def(path=path, name=name, tag=tag, kind=CONTAINER_KINDS[tag], parent=parent, module=module)
            self._fill_common(d, el)
            parent.children.append(d)
            module.index[path] = d
            for group in ("PARAMETERS", "REFERENCES", "SUB-CONTAINERS", "CHOICES"):
                g = el.find(q(group))
                if g is not None:
                    for c in g:
                        if isinstance(c.tag, str):
                            self._build_node(c, d, module)
        elif tag in PARAM_KINDS or tag in REF_KINDS:
            kind = PARAM_KINDS.get(tag) or REF_KINDS[tag]
            d = Def(path=path, name=name, tag=tag, kind=kind, parent=parent, module=module)
            self._fill_common(d, el)
            d.symbolic_name_value = text(el, "SYMBOLIC-NAME-VALUE") == "true"
            dv = el.find(".//" + q("DEFAULT-VALUE"))
            d.default = dv.text.strip() if dv is not None and dv.text is not None else None
            mn = el.find(".//" + q("MIN"))
            mx = el.find(".//" + q("MAX"))
            d.min = _num(mn.text) if mn is not None else None
            d.max = _num(mx.text) if mx is not None else None
            ml = el.find(".//" + q("MIN-LENGTH"))
            xl = el.find(".//" + q("MAX-LENGTH"))
            d.min_length = int(ml.text) if ml is not None and ml.text and ml.text.strip().isdigit() else None
            d.max_length = int(xl.text) if xl is not None and xl.text and xl.text.strip().isdigit() else None
            lits = el.find(q("LITERALS"))
            if lits is not None:
                d.literals = [arxml.short_name(l) for l in lits if isinstance(l.tag, str) and arxml.short_name(l)]
            dests = [r.text.strip() for r in el.iter(q("DESTINATION-REF")) if r.text]
            d.dest = dests
            dt = el.find(".//" + q("DESTINATION-TYPE"))
            d.dest_type = dt.text.strip() if dt is not None and dt.text else None
            if kind == "instance":
                tt = el.find(".//" + q("DESTINATION-CONTEXT"))
                d.dest_type = d.dest_type or (tt.text.strip() if tt is not None and tt.text else None)
            parent.children.append(d)
            module.index[path] = d
