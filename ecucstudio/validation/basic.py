"""DaVinci compatible *basic rules* (AR-ECUC / AR-BSWMD / Cfg result ids).

IDs, titles and message wording follow DaVinci Configurator's
``com.vector.cfg.validation.basicrules.msr`` plug-in so that results from this
tool and from ``DVCfgCmd -v`` read the same.
"""
from __future__ import annotations

import math
import os
import re
from collections import defaultdict

from .. import arxml
from ..arxml import q
from ..project import (CONTAINER_TAG, MODULE_TAG, container_children, definition_ref,
                       is_user_defined, raw_value, value_elements)
from . import Result, Rule, Severity, SolvingAction

TITLES = {
    "AR-ECUC02008": "Invalid multiplicity",
    "AR-ECUC02027": "Integer value out of range",
    "AR-ECUC02028": "Float value out of range",
    "AR-ECUC02030": "Linker symbol invalid characters",
    "AR-ECUC02031": "Linker symbol too long",
    "AR-ECUC02039": "Reference target does not match reference definition",
    "AR-ECUC02067": "A choice-container must have exactly one choice child container",
    "AR-ECUC02093": "Reference points to inactive object",
    "AR-ECUC02108": "Container name with symbolic-name-value is not unique within its definition.",
    "AR-ECUC03005": "Enumeration value does not match definition",
    "AR-ECUC03019": "Incorrect definition of configuration element",
    "AR-ECUC06052": "The module configuration variant must be set to a valid value",
    "AR-BSWMD00033": "Deviation from Preconfiguration",
    "AR-BSWMD00034": "Deviation from Published Information Default",
    "AR-GST00021": "Non-unique case-insensitive shortname",
    "Cfg00020": "Deviation from initial configuration",
    "Cfg00021": "Parameter value has wrong data type",
    "Cfg00022": "Missing parameter value",
    "Cfg00024": "Missing reference target",
    "Cfg00028": "A module configuration does not reference a bsw implementation",
    "Cfg00031": "Symbolic name value parameter is required",
    "Cfg00032": "String length out of range",
}

LINKER_RE = re.compile(r"^[_.$%a-zA-Z][_.$%a-zA-Z0-9]*$")
INT_RE = re.compile(r"^[+-]?(0[xX][0-9a-fA-F]+|0[bB][01]+|\d+)$")
TYPE_NAME = {"boolean": "boolean", "integer": "integer", "float": "float"}
REDUCED = "[Reduced Severity due to User-Defined Parameter] "


def parse_bool(s):
    s = (s or "").strip().lower()
    if s in ("true", "1"):
        return True
    if s in ("false", "0"):
        return False
    return None


def parse_int(s):
    s = (s or "").strip()
    if not INT_RE.match(s):
        return None
    neg = s.startswith("-")
    b = s.lstrip("+-")
    v = int(b, 16) if b[:2].lower() == "0x" else int(b[2:], 2) if b[:2].lower() == "0b" else int(b)
    return -v if neg else v


def parse_float(s):
    s = (s or "").strip()
    low = s.lower()
    if low in ("inf", "+inf", "infinity"):
        return math.inf
    if low in ("-inf", "-infinity"):
        return -math.inf
    try:
        v = parse_int(s)
        return float(v) if v is not None else float(s)
    except ValueError:
        return None


def fmt_num(v):
    if v is None:
        return "?"
    if v == math.inf:
        return "INF"
    if v == -math.inf:
        return "-INF"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def param_obj(container_path, pdef_name, index, value=None):
    s = f"{container_path}[{index}:{pdef_name}]"
    if value is not None:
        s += f"(value={value})"
    return s


def _sev(sev: Severity, v) -> tuple[Severity, str]:
    """User-defined parameters reduce Error to Warning like DaVinci does."""
    if v is not None and sev >= Severity.ERROR and is_user_defined(v):
        return Severity.WARNING, REDUCED
    return sev, ""


class StructureRule(Rule):
    """Single traversal that performs all definition based checks."""
    id = "AR-ECUC"
    title = "Basic definition checks (AR-ECUC/Cfg)"

    def check(self, ctx):
        self.ctx = ctx
        self.model = ctx.model
        self.defs = ctx.defs
        self.disabled = set(ctx.options.get("disabled_ids", ()))
        self.out = []
        self.snv_targets = set()       # containers referenced by symbolic name references
        only = ctx.options.get("modules")      # optional list of module short names
        for mod in self.model.modules:
            if only and arxml.short_name(mod) not in only:
                continue
            self._module(mod)
        self._check_snv_required()
        return self.out

    # ---------------------------------------------------------------- utils
    def emit(self, rid, sev, msg, obj=None, definition=None, element=None, actions=(), param=None):
        if rid in self.disabled:
            return
        self.out.append(Result(rid, sev, TITLES.get(rid, rid), msg, obj=obj, definition=definition,
                               element=element, actions=list(actions), param=param))

    # --------------------------------------------------------------- module
    def _module(self, mod):
        path = self.model.path_of(mod)
        dref = definition_ref(mod)
        mdef = self.defs.module(dref) if dref else None
        if mdef is None:
            self.emit("AR-ECUC03019", Severity.ERROR,
                      f"The module configuration {path} references the definition {dref or '<none>'}, "
                      f"which is not available in the SIP or the additional BSWMD files.",
                      obj=path, definition=dref, element=mod)
            return
        variant = arxml.text(mod, "IMPLEMENTATION-CONFIG-VARIANT")
        if variant and mdef.supported_variants and variant not in mdef.supported_variants:
            self.emit("AR-ECUC06052", Severity.ERROR,
                      f"The variant setting {variant} of module {path} is not allowed.\n"
                      f"Valid values are:\n" + "\n".join(mdef.supported_variants),
                      obj=path, definition=dref, element=mod)
        if arxml.text(mod, "MODULE-DESCRIPTION-REF") is None and dref.startswith("/MICROSAR/"):
            self.emit("Cfg00028", Severity.WARNING,
                      f"The module configuration {path} does not reference a bsw implemenation. "
                      f"Mechanisms like pre-configuration and bsw-internal-behavior generation won't work "
                      f"without this reference.", obj=path, definition=dref, element=mod)
        self._container_body(mod, mdef, path)
        self._preconfig(mod, mdef, path)

    # ------------------------------------------------------------ container
    def _container_body(self, el, cdef, path, recursive=True):
        # ---- sub containers -------------------------------------------------
        subs = container_children(el)
        by_def = defaultdict(list)
        names = defaultdict(list)
        for s in subs:
            by_def[definition_ref(s)].append(s)
            names[(arxml.short_name(s) or "").lower()].append(s)
        for lname, lst in names.items():
            if len(lst) > 1:
                exact = {arxml.short_name(x) for x in lst}
                if len(exact) > 1:
                    self.emit("AR-GST00021", Severity.WARNING,
                              f"The content of shortName needs to be unique (case insensitive) within a given "
                              f"Identifiable. The following child elements of parent element '{path}' are "
                              f"violating this constraint: " + " , ".join(f"'{path}/{n}'" for n in sorted(exact)),
                              obj=path, element=el)
        known = {c.path: c for c in cdef.containers()}
        for dref, lst in by_def.items():
            cd = known.get(dref)
            if cd is None:
                cd = next((c for c in cdef.containers() if self.defs.same_definition(c.path, dref)), None)
            if cd is None:
                for s in lst:
                    sp = f"{path}/{arxml.short_name(s)}"
                    self.emit("AR-ECUC03019", Severity.ERROR,
                              f"The container {sp} with definition {dref} is not allowed below "
                              f"{cdef.path} (definition not found).",
                              obj=sp, definition=dref, element=s,
                              actions=[SolvingAction("Delete the container", self._deleter(s))])
                continue
            if recursive:
                for s in lst:
                    self._container_body(s, cd, f"{path}/{arxml.short_name(s)}")
        for cd in cdef.containers():
            n = len(by_def.get(cd.path, []))
            if n < cd.lower:
                msg = (f"Mandatory container {cd.name} is missing in {path}." if n == 0 else
                       f"{path} contains a fewer number of container {cd.name} than lowerMultiplicity specifies.")
                self.emit("AR-ECUC02008", Severity.ERROR, msg, obj=path if n else None, definition=cd.path,
                          element=el, actions=[SolvingAction(f"Create container {cd.name}",
                                                             self._creator(el, cd, cd.lower - n),
                                                             preferred=True)])
            elif n > cd.upper:
                surplus = by_def[cd.path][int(cd.upper):]
                self.emit("AR-ECUC02008", Severity.ERROR,
                          f"{path} contains a bigger number of container {cd.name} than upperMultiplicity "
                          f"specifies.", obj=path, definition=cd.path, element=el,
                          actions=[SolvingAction(f"Delete {len(surplus)} surplus container(s)",
                                                 self._deleter(*surplus))])
        if cdef.kind == "choice" and len(subs) != 1:
            self.emit("AR-ECUC02067", Severity.ERROR,
                      f"The choice-container {path} must have exactly one choice as child, but has "
                      f"{len(subs)} child containers.", obj=path, definition=cdef.path, element=el)
        if arxml.local(el) == MODULE_TAG:
            return
        # ---- parameters / references ---------------------------------------
        vals = value_elements(el)
        pby = defaultdict(list)
        pdefs = {p.path: p for p in cdef.params()}
        for v in vals:
            pby[definition_ref(v)].append(v)
        for dref, lst in pby.items():
            pd = pdefs.get(dref)
            if pd is None:
                pd = next((p for p in cdef.params() if self.defs.same_definition(p.path, dref)), None)
            if pd is None:
                for i, v in enumerate(lst):
                    o = param_obj(path, dref.rsplit("/", 1)[-1], i)
                    self.emit("AR-ECUC03019", Severity.ERROR,
                              f"The parameter {o} with definition {dref} is not defined in {cdef.path}.",
                              obj=o, definition=dref, element=v,
                              actions=[SolvingAction("Delete the parameter", self._deleter(v))])
                continue
            tag = arxml.local(lst[0])
            if pd.value_tag and tag != pd.value_tag:
                for i, v in enumerate(lst):
                    o = param_obj(path, pd.name, i)
                    self.emit("AR-ECUC03019", Severity.ERROR,
                              f"The parameter {o} with model type {tag} is assigned to an incompatible "
                              f"parameter definition {pd.path}", obj=o, definition=pd.path, element=v)
                continue
            for i, v in enumerate(lst):
                self._value(el, path, pd, v, i)
        for pd in cdef.params():
            n = len(pby.get(pd.path, []))
            if n == 0:
                n = sum(len(l) for d, l in pby.items() if d != pd.path and self.defs.same_definition(d, pd.path))
            if n < pd.lower:
                acts = []
                if pd.default is not None and not pd.is_ref:
                    acts.append(SolvingAction(f"Create parameter {pd.name} with default value {pd.default}",
                                              self._value_creator(el, pd, pd.default), preferred=True))
                msg = (f"Mandatory parameter {pd.name} is missing in {path}." if n == 0 else
                       f"{path} contains a fewer number of parameter {pd.name} than lowerMultiplicity specifies.")
                self.emit("AR-ECUC02008", Severity.ERROR, msg, obj=path, definition=pd.path, element=el,
                          actions=acts, param=pd.path)
            elif n > pd.upper:
                surplus = pby[pd.path][int(pd.upper):]
                self.emit("AR-ECUC02008", Severity.ERROR,
                          f"{path} contains a bigger number of parameter {pd.name} than upperMultiplicity "
                          f"specifies.", obj=path, definition=pd.path, element=el, param=pd.path,
                          actions=[SolvingAction("Delete surplus parameter instance(s)", self._deleter(*surplus))])

    # ---------------------------------------------------------------- value
    def _value(self, container, cpath, pd, v, idx):
        raw = raw_value(v)
        o = param_obj(cpath, pd.name, idx, raw)
        oref = param_obj(cpath, pd.name, idx)
        if pd.is_ref:
            self._reference(container, cpath, pd, v, idx, raw, o)
            return
        if raw is None or (raw == "" and pd.kind in ("boolean", "integer", "float")):
            sev, pre = _sev(Severity.ERROR, v)
            acts = []
            if pd.default is not None:
                acts.append(SolvingAction(f"Set to default value {pd.default}",
                                          self._setter(container, pd, idx, pd.default), preferred=True))
            self.emit("Cfg00022", sev, pre + f"The value of parameter {oref} is missing or empty.",
                      obj=oref, definition=pd.path, element=v, actions=acts)
            return
        k = pd.kind
        if k == "boolean":
            if parse_bool(raw) is None:
                self._wrong_type(container, pd, v, idx, o, "boolean")
        elif k == "integer":
            n = parse_int(raw)
            if n is None:
                self._wrong_type(container, pd, v, idx, o, "integer")
            elif (pd.min is not None and n < pd.min) or (pd.max is not None and n > pd.max):
                self._range("AR-ECUC02027", container, pd, v, idx, o, n)
        elif k == "float":
            n = parse_float(raw)
            if n is None:
                self._wrong_type(container, pd, v, idx, o, "float")
            elif (pd.min is not None and n < pd.min) or (pd.max is not None and n > pd.max):
                self._range("AR-ECUC02028", container, pd, v, idx, o, n)
        elif k == "enum":
            if pd.literals and raw not in pd.literals:
                sev, pre = _sev(Severity.ERROR, v)
                acts = []
                if pd.default in pd.literals:
                    acts.append(SolvingAction(f"Set to default literal {pd.default}",
                                              self._setter(container, pd, idx, pd.default), preferred=True))
                close = [l for l in pd.literals if l.lower() == raw.lower()]
                for l in close:
                    acts.append(SolvingAction(f"Set to {l}", self._setter(container, pd, idx, l),
                                              preferred=not acts))
                self.emit("AR-ECUC03005", sev,
                          pre + f"The value of the enumeration parameter {o} is not in the list of allowed "
                          f"ones [{', '.join(pd.literals)}].", obj=oref, definition=pd.path, element=v,
                          actions=acts)
        elif k == "linker":
            if not LINKER_RE.match(raw):
                sev, pre = _sev(Severity.ERROR, v)
                self.emit("AR-ECUC02030", sev,
                          pre + f"The linker symbol parameter {o} contains invalid characters. Only the pattern "
                          f"\"[_.$%a-zA-Z][_.$%a-zA-Z0-9]{{0,*}}\" is allowed.",
                          obj=oref, definition=pd.path, element=v)
            elif len(raw) > 255:
                sev, pre = _sev(Severity.ERROR, v)
                self.emit("AR-ECUC02031", sev, pre + f"The value of linker symbol parameter {o} is too long. "
                          f"Only 255 characters are allowed.", obj=oref, definition=pd.path, element=v)
        if k in ("string", "multiline", "function", "linker"):
            if pd.min_length is not None and len(raw) < pd.min_length:
                sev, pre = _sev(Severity.ERROR, v)
                self.emit("Cfg00032", sev, pre + f"The value of the textual parameter {o} is shorter than the "
                          f"minimum allowed length of {pd.min_length} character(s) specified in its definition.",
                          obj=oref, definition=pd.path, element=v)
            if pd.max_length is not None and len(raw) > pd.max_length:
                sev, pre = _sev(Severity.ERROR, v)
                self.emit("Cfg00032", sev, pre + f"The value of the textual parameter {o} exceeds the maximum "
                          f"allowed length of {pd.max_length} character(s) specified in its definition.",
                          obj=oref, definition=pd.path, element=v)
        # published information defaults (AR-BSWMD00034)
        if pd.default is not None and not pd.path.startswith("/MICROSAR/") and self._is_published(pd) and k in ("integer", "float", "boolean", "enum",
                                                                          "string"):
            if not _same_value(k, raw, pd.default):
                self.emit("AR-BSWMD00034", Severity.WARNING,
                          f"The parameter {o} differs from published information default value {pd.default}.\n"
                          f"This may be ok when value is provided by external tooling.\n"
                          f"Look into these locations for published information default values:\n"
                          f"{pd.module.file if pd.module else ''}",
                          obj=oref, definition=pd.path, element=v,
                          actions=[SolvingAction(f"Reset to published default {pd.default}",
                                                 self._setter(container, pd, idx, pd.default))])

    @staticmethod
    def _is_published(pd):
        d = pd.parent
        while d is not None and d.kind != "module":
            if d.name.endswith("PublishedInformation"):
                return True
            d = d.parent
        return False

    def _wrong_type(self, container, pd, v, idx, o, tname):
        sev, pre = _sev(Severity.ERROR, v)
        acts = []
        if pd.default is not None:
            acts.append(SolvingAction(f"Set to default value {pd.default}",
                                      self._setter(container, pd, idx, pd.default), preferred=True))
        self.emit("Cfg00021", sev, pre + f"The parameter {o} has a wrong data type. It must be a {tname} value.",
                  obj=o.split("(value=")[0], definition=pd.path, element=v, actions=acts)

    def _range(self, rid, container, pd, v, idx, o, n):
        sev, pre = _sev(Severity.ERROR, v)
        acts = []
        if pd.min is not None and n < pd.min and pd.min != -math.inf:
            acts.append(SolvingAction(f"Set to minimum {fmt_num(pd.min)}",
                                      self._setter(container, pd, idx, fmt_num(pd.min)), preferred=True))
        if pd.max is not None and n > pd.max and pd.max != math.inf:
            acts.append(SolvingAction(f"Set to maximum {fmt_num(pd.max)}",
                                      self._setter(container, pd, idx, fmt_num(pd.max)), preferred=True))
        self.emit(rid, sev, pre + f"The parameter {o} is not in range [{fmt_num(pd.min)}, {fmt_num(pd.max)}].",
                  obj=o.split("(value=")[0], definition=pd.path, element=v, actions=acts)

    def _reference(self, container, cpath, pd, v, idx, raw, o):
        oref = param_obj(cpath, pd.name, idx)
        if not raw:
            sev, pre = _sev(Severity.ERROR, v)
            self.emit("Cfg00022", sev, pre + f"The value of reference {oref} is missing or empty.",
                      obj=oref, definition=pd.path, element=v,
                      actions=[SolvingAction("Delete the reference", self._deleter(v))])
            return
        if pd.kind in ("foreign", "instance", "uri"):
            fx = self.ctx.options.get("foreign_index")
            if fx is not None and raw not in fx and not self.model.resolve(raw):
                sev, pre = _sev(Severity.ERROR, v)
                self.emit("Cfg00024", sev, pre + f"The target of reference {o} is missing.",
                          obj=oref, definition=pd.path, element=v)
            return
        target = self.model.resolve(raw)
        if target is None:
            sev, pre = _sev(Severity.ERROR, v)
            self.emit("Cfg00024", sev, pre + f"The target of reference {o} is missing.",
                      obj=oref, definition=pd.path, element=v,
                      actions=[SolvingAction("Delete the reference", self._deleter(v))])
            return
        tdef = definition_ref(target)
        if pd.dest:
            if not any(self.defs.same_definition(tdef, d) for d in pd.dest):
                sev, pre = _sev(Severity.ERROR, v)
                if pd.kind == "choice-reference":
                    msg = (f"The target of the choice-reference {o} has the definition {tdef}. According to "
                           f"the choice-reference's definition, only the following target definitions are "
                           f"allowed:\n" + "\n".join(pd.dest))
                else:
                    msg = (f"The target of the reference value {o} is of type {tdef}, but the definition "
                           f"specifies a target of type {pd.dest[0]}.")
                self.emit("AR-ECUC02039", sev, pre + msg, obj=oref, definition=pd.path, element=v)
        if not self.model.is_active_module(raw):
            sev, pre = _sev(Severity.ERROR, v)
            self.emit("AR-ECUC02093", sev, pre + f"The target of the reference value {o} is an inactive object, "
                      f"because it's not a member of the active module configuration.",
                      obj=oref, definition=pd.path, element=v)
        if pd.kind == "symbolic":
            self.snv_targets.add(raw)

    def _check_snv_required(self):
        for tpath in sorted(self.snv_targets):
            t = self.model.resolve(tpath)
            if t is None:
                continue
            cdef = self.defs.find(definition_ref(t))
            if cdef is None:
                continue
            snv = [p for p in cdef.params() if p.symbolic_name_value]
            if not snv:
                continue
            present = {definition_ref(v) for v in value_elements(t)}
            for p in snv:
                if p.path not in present:
                    self.emit("Cfg00031", Severity.ERROR,
                              f"The container {tpath} must contain the symbolic name value parameter {p.name} "
                              f"because it is referenced by at least one symbolic name reference.",
                              obj=tpath, definition=p.path, element=t, param=p.path)

    # ----------------------------------------------------------- preconfig
    def _preconfig(self, mod, mdef, mpath):
        if "AR-BSWMD00033" in self.disabled or mdef.impl is None:
            return
        for pre in mdef.impl.preconfig:
            pel = self.defs.config_values(pre, mdef.impl.file)
            if pel is not None and not self._preconfig_applies(pel, mdef.impl.file):
                continue
            if pel is None:
                continue
            base = arxml.ar_path(pel)
            for ppath, pc in arxml.iter_identifiables(pel, {CONTAINER_TAG}, base.rsplit("/", 1)[0]):
                rel = ppath[len(base):]
                target = self.model.resolve(mpath + rel)
                if target is None:
                    continue
                cur = defaultdict(list)
                for v in value_elements(target):
                    cur[definition_ref(v)].append(v)
                for pv in value_elements(pc):
                    d = definition_ref(pv)
                    want = raw_value(pv)
                    have = cur.get(d)
                    if not have or want is None:
                        continue
                    pd = self.defs.find(d)
                    kind = pd.kind if pd else "string"
                    if pd is not None and pd.is_ref:
                        if want.startswith(base + "/"):
                            want = mpath + want[len(base):]
                        elif want == base:
                            want = mpath
                    if pd is not None and is_user_defined(have[0]):
                        continue
                    got = raw_value(have[0])
                    if not _same_value(kind, got, want):
                        o = param_obj(mpath + rel, d.rsplit("/", 1)[-1], 0, got)
                        self.emit("AR-BSWMD00033", Severity.WARNING,
                                  f"The parameter {o} differs from preconfigured value {want}.\n"
                                  f"Look into these locations for preconfigured values:\n{mdef.impl.file}",
                                  obj=o.split("(value=")[0], definition=d, element=have[0],
                                  actions=[SolvingAction(f"Reset to preconfigured value {want}",
                                                         self._setter(target, pd, 0, want), preferred=True)]
                                  if pd else [])

    def _preconfig_applies(self, pel, impl_file):
        """Pre-configs in use-case specific files (e.g. MemMap_ARM_pre.arxml) are only active
        for that use case; only trust the implementation file, *_SafeBSW_pre.arxml and the
        project's additional BSWMD folders."""
        f = pel.getroottree().docinfo.URL or ""
        f = os.path.normcase(os.path.abspath(f.replace("file:/", ""))) if f else ""
        if f == os.path.normcase(os.path.abspath(impl_file)) or f.endswith("_safebsw_pre.arxml"):
            return True
        return any(f.startswith(os.path.normcase(d)) for d in self.defs.extra_dirs)

    # ------------------------------------------------------ action factories
    def _deleter(self, *els):
        model = self.model

        def run():
            for e in els:
                if e.getparent() is not None:
                    model.delete_element(e)
        return run

    def _creator(self, parent, cdef, count):
        model = self.model

        def run():
            for _ in range(max(1, count)):
                model.add_container(parent, cdef)
        return run

    def _value_creator(self, container, pdef, value):
        model = self.model
        return lambda: model.set_value(container, pdef, value, index=len(model.find_values(container, pdef.path)))

    def _setter(self, container, pdef, idx, value):
        model = self.model
        return lambda: model.set_value(container, pdef, value, index=idx)


def _same_value(kind, a, b):
    if a is None or b is None:
        return a == b
    if kind == "boolean":
        return parse_bool(a) == parse_bool(b)
    if kind == "integer":
        x, y = parse_int(a), parse_int(b)
        return x == y if x is not None and y is not None else a.strip() == b.strip()
    if kind == "float":
        x, y = parse_float(a), parse_float(b)
        return x == y if x is not None and y is not None else a.strip() == b.strip()
    return a.strip() == b.strip()


class InitialConfigRule(Rule):
    """Cfg00020: derived containers of the InitialEcuC that were deleted by the user."""
    id = "Cfg00020"
    title = "Deviation from initial configuration"

    def check(self, ctx):
        model = ctx.model
        if not model.initial or "Cfg00020" in set(ctx.options.get("disabled_ids", ())):
            return []
        out = []
        # group initial containers by parent relative path
        parent_children = defaultdict(list)
        for rel in model.initial:
            if rel.count("/") >= 2:
                parent_children[rel.rsplit("/", 1)[0]].append(rel)
        # base package prefix of the active configuration (e.g. /ActiveEcuC)
        prefix = None
        for m in model.modules:
            prefix = model.path_of(m).rsplit("/", 1)[0]
            break
        if prefix is None:
            return []
        for prel, kids in sorted(parent_children.items()):
            ppath = prefix + prel
            parent = model.resolve(ppath)
            if parent is None:
                continue
            missing = [k.rsplit("/", 1)[-1] for k in kids if model.resolve(prefix + k) is None]
            if not missing:
                continue
            is_mod = arxml.local(parent) == MODULE_TAG
            what = "module configuration" if is_mod else "container"
            lines = "\n".join(f"Container {n} not found in Container {ppath}." for n in missing)
            out.append(Result("Cfg00020", Severity.WARNING, TITLES["Cfg00020"],
                              f"The {what} {ppath} is initial configured, but has different configured values:\n"
                              f"These derived containers are deleted by the user and could be restored:\n{lines}",
                              obj=ppath, definition=definition_ref(parent), element=parent,
                              actions=[SolvingAction(f"Restore {len(missing)} derived container(s)",
                                                     _restorer(model, parent, [model.initial[prel + '/' + n]
                                                                               for n in missing]))]))
        # parameters that differ from their derived (initial) value
        for rel_key, init_val in model.initial_values.items():
            rel, dref = rel_key
            if rel_key in model.initial_soft:
                continue  # soft derived values may be changed freely
            cont = model.resolve(prefix + rel)
            if cont is None or init_val is None:
                continue
            vals = [v for v in value_elements(cont) if definition_ref(v) == dref]
            if len(vals) != 1:
                continue
            v = vals[0]
            cur = raw_value(v)
            pd = ctx.defs.find(dref)
            kind = pd.kind if pd else "string"
            ipref = model.initial_prefix or "/InitialEcuC"
            if pd is not None and pd.is_ref and init_val.startswith(ipref + "/"):
                init_val = prefix + init_val[len(ipref):]
            if cur is None or _same_value(kind, cur, init_val):
                continue
            pname = dref.rsplit("/", 1)[-1]
            out.append(Result("Cfg00020", Severity.WARNING, TITLES["Cfg00020"],
                              f"The parameter {param_obj(prefix + rel, pname, 0, cur)} differs from initial "
                              f"configured value {param_obj(ipref + rel, pname, 0, init_val)}.",
                              obj=param_obj(prefix + rel, pname, 0), definition=dref, element=v,
                              actions=[SolvingAction(f"Reset to initial value {init_val}",
                                                     (lambda c=cont, d=pd, iv=init_val:
                                                      model.set_value(c, d, iv)) if pd else (lambda: None),
                                                     preferred=True)]))
        return out


def _restorer(model, parent, initial_elements):
    import copy

    def run():
        grp_name = "CONTAINERS" if arxml.local(parent) == MODULE_TAG else "SUB-CONTAINERS"
        for ie in initial_elements:
            el = copy.deepcopy(ie)
            grp = parent.find(q(grp_name))
            if grp is None:
                grp = arxml.make(grp_name)
                arxml.insert_child(parent, grp)
            arxml.insert_child(grp, el)
            model._index_subtree(el, True, model.path_of(parent))
            model._touch(el, "added")
        model._unrecorded = True   # restoring is not undoable
    return run


def validate_container(ctx, el):
    """Fast live validation of one container (its values and direct children only)."""
    r = StructureRule()
    r.ctx, r.model, r.defs = ctx, ctx.model, ctx.defs
    r.disabled = set(ctx.options.get("disabled_ids", ()))
    r.out, r.snv_targets = [], set()
    cdef = ctx.defs.find(definition_ref(el))
    if cdef is None:
        return []
    r._container_body(el, cdef, ctx.model.path_of(el), recursive=False)
    return r.out


RULES = [StructureRule(), InitialConfigRule()]
