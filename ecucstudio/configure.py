"""DaVinci-like configuration operations on top of the ECUC model.

* create a module configuration from its BSWMD (Project Settings > Modules > Add)
* apply the SIP's recommended and pre-configuration when a module/container is created
  (DaVinci: "recommended and default values are applied when a new object is created;
  pre-configuration values are applied and are read-only")
* create all missing mandatory parameters / sub-containers of a container
"""
from __future__ import annotations

import copy

from . import arxml
from .arxml import q
from .project import (CONTAINER_ORDER, CONTAINER_TAG, MODULE_TAG, container_children, definition_ref,
                      raw_value, value_elements)

VALUE_GROUPS = ("PARAMETER-VALUES", "REFERENCE-VALUES")


# ---------------------------------------------------------------------------
# templates (recommended / pre-configuration) of a module
# ---------------------------------------------------------------------------

def module_templates(defs, mdef):
    """[(kind, ECUC-MODULE-CONFIGURATION-VALUES element, its AR path)] with kind 'rec' or 'pre'."""
    out = []
    impl = mdef.impl if mdef is not None else None
    if impl is None:
        return out
    from .validation.basic import StructureRule
    checker = StructureRule()
    checker.defs = defs
    for kind, paths in (("rec", impl.recommended), ("pre", impl.preconfig)):
        for p in paths:
            el = defs.config_values(p, impl.file)
            if el is None:
                continue
            if kind == "pre" and not checker._preconfig_applies(el, impl.file):
                continue       # use-case specific pre-configuration (e.g. MemMap_ARM_pre.arxml)
            out.append((kind, el, arxml.ar_path(el)))
    return out


def _map_ref(text, src_base, dst_base):
    if text and (text == src_base or text.startswith(src_base + "/")):
        return dst_base + text[len(src_base):]
    return text


def _fresh_copy(el, src_base, dst_base):
    c = copy.deepcopy(el)
    for sub in c.iter(q(CONTAINER_TAG)):
        sub.set("UUID", arxml.new_uuid())
    for r in c.iter(q("VALUE-REF")):
        r.text = _map_ref(r.text.strip() if r.text else r.text, src_base, dst_base)
    return c


def _group(el, name):
    """Value / sub-container group of an element that is not inserted yet (plain append order)."""
    g = el.find(q(name))
    if g is not None:
        return g
    g = arxml.make(name)
    order = CONTAINER_ORDER if arxml.local(el) == CONTAINER_TAG else ["CONTAINERS"]
    pos = order.index(name) if name in order else len(order)
    kids = [c for c in el if isinstance(c.tag, str)]
    for i, c in enumerate(kids):
        ln = arxml.local(c)
        if ln in order and order.index(ln) > pos:
            c.addprevious(g)
            return g
    el.append(g)
    return g


def merge_template(target, tmpl, src_base, dst_base):
    """Merge values and sub-containers of *tmpl* (from a _Rec/_Pre configuration) into *target*.

    Template values win over defaults; sub-containers are matched by short name + definition.
    Works on detached elements (before insertion into the document).
    """
    if arxml.local(tmpl) != MODULE_TAG:
        for gname in VALUE_GROUPS:
            tg = tmpl.find(q(gname))
            if tg is None:
                continue
            for tv in tg:
                if not isinstance(tv.tag, str):
                    continue
                d = definition_ref(tv)
                existing = [v for v in value_elements(target) if definition_ref(v) == d]
                new = _fresh_copy(tv, src_base, dst_base)
                if existing:
                    existing[0].getparent().replace(existing[0], new)
                else:
                    _group(target, gname).append(new)
    tsubs = container_children(tmpl)
    if not tsubs:
        return
    gname = "CONTAINERS" if arxml.local(target) == MODULE_TAG else "SUB-CONTAINERS"
    for tc in tsubs:
        match = None
        for c in container_children(target):
            if arxml.short_name(c) == arxml.short_name(tc) and definition_ref(c) == definition_ref(tc):
                match = c
                break
        if match is None:
            for c in container_children(target):
                # a mandatory container created with the definition name for the same definition
                if definition_ref(c) == definition_ref(tc) and arxml.short_name(c) == definition_ref(c).rsplit("/", 1)[-1]:
                    c.find(q("SHORT-NAME")).text = arxml.short_name(tc)
                    match = c
                    break
        if match is not None:
            merge_template(match, tc, src_base, dst_base)
        else:
            _group(target, gname).append(_fresh_copy(tc, src_base, dst_base))


def template_for_container(defs, mdef, cdef_path):
    """First container of the module's recommended configuration with definition *cdef_path*."""
    for kind, el, base in module_templates(defs, mdef):
        if kind != "rec":
            continue
        for path, c in arxml.iter_identifiables(el, {CONTAINER_TAG}, base.rsplit("/", 1)[0]):
            if definition_ref(c) == cdef_path:
                return c, base
    return None, None


# ---------------------------------------------------------------------------
# operations
# ---------------------------------------------------------------------------

def default_variant(mdef):
    sv = mdef.supported_variants or []
    for v in ("VARIANT-PRE-COMPILE", "VARIANT-LINK-TIME", "VARIANT-POST-BUILD"):
        if v in sv:
            return v
    return sv[0] if sv else "VARIANT-PRE-COMPILE"


def build_module(model, defs, mdef, name=None, variant=None, apply_templates=True):
    """New ECUC-MODULE-CONFIGURATION-VALUES for *mdef* (not inserted yet)."""
    name = name or mdef.name
    el = arxml.make(MODULE_TAG, attrib={"UUID": arxml.new_uuid()})
    arxml.sub(el, "SHORT-NAME", name)
    if mdef.path.startswith("/MICROSAR/"):
        admin = arxml.sub(el, "ADMIN-DATA")
        sdg = arxml.sub(arxml.sub(admin, "SDGS"), "SDG", attrib={"GID": "DV:CfgPostBuild"})
        arxml.sub(sdg, "SD", "false", {"GID": "DV:postBuildVariantSupport"})
    arxml.sub(el, "DEFINITION-REF", mdef.path, {"DEST": "ECUC-MODULE-DEF"})
    arxml.sub(el, "IMPLEMENTATION-CONFIG-VARIANT", variant or default_variant(mdef))
    if mdef.impl is not None:
        arxml.sub(el, "MODULE-DESCRIPTION-REF", mdef.impl.path, {"DEST": "BSW-IMPLEMENTATION"})
    conts = arxml.sub(el, "CONTAINERS")
    for c in mdef.containers():
        for i in range(c.lower):
            conts.append(model._build_container(c, c.name if i == 0 else f"{c.name}_{i:03d}", True))
    if apply_templates:
        pkg_path = model.value_collection()[2]
        dst = (arxml.ar_path(pkg_path) if pkg_path is not None else "/ActiveEcuC") + "/" + name
        for _kind, tmpl, base in module_templates(defs, mdef):    # rec first, then pre (pre wins)
            merge_template(el, tmpl, base, dst)
    if not [c for c in conts if isinstance(c.tag, str)]:
        el.remove(conts)
    return el


def assign_free_ids(session, el, cdef):
    """Symbolic-name-value integer parameters (handle ids) of a new container get the next free value
    among all containers of the same definition — like DaVinci does, avoiding e.g. COM95100."""
    model = session.model
    for p in cdef.params():
        if not (p.symbolic_name_value and p.kind == "integer"):
            continue
        used = set()
        for c in model.containers_of_def(cdef.path, session.defs):
            for v in value_elements(c):
                if definition_ref(v) == p.path:
                    try:
                        used.add(int((raw_value(v) or "").strip(), 0))
                    except ValueError:
                        pass
        vals = [v for v in value_elements(el) if definition_ref(v) == p.path]
        if not vals:
            continue
        lo = int(p.min) if isinstance(p.min, (int, float)) and p.min not in (float("-inf"),) else 0
        n = lo
        while n in used:
            n += 1
        if p.max is not None and p.max != float("inf") and n > p.max:
            continue
        val = vals[0].find(q("VALUE"))
        if val is not None:
            val.text = str(n)


def create_container(session, parent, cdef, name=None, count=1, with_defaults=True, apply_recommended=True):
    """Add *count* containers like DaVinci: defaults + recommended configuration of the SIP."""
    model, defs = session.model, session.defs
    mod = model.module_of(parent)
    mdef = defs.module(definition_ref(mod)) if mod is not None else None
    tmpl, base = (template_for_container(defs, mdef, cdef.path) if (apply_recommended and mdef) else (None, None))
    created = []
    for i in range(count):
        nm = name if (name and i == 0) else model.unique_name(parent, name or cdef.name)
        dst = model.path_of(mod) if mod is not None else ""

        def prepare(el, tmpl=tmpl, base=base, dst=dst):
            if tmpl is not None and with_defaults:
                merge_template(el, tmpl, base, dst)
            assign_free_ids(session, el, cdef)
        created.append(model.add_container(parent, cdef, nm, with_defaults=with_defaults, prepare=prepare))
    return created


def complete_container(session, el, cdef):
    """Create every missing mandatory parameter (with default) and sub-container. Returns #changes."""
    model = session.model
    n = 0
    if arxml.local(el) != MODULE_TAG:
        for p in cdef.params():
            if p.lower < 1 or p.is_ref or p.default is None:
                continue
            have = len(model.find_values(el, p.path, session.defs))
            for i in range(have, p.lower):
                model.set_value(el, p, p.default, index=i)
                n += 1
    if cdef.kind != "choice":
        for c in cdef.containers():
            have = len([x for x in container_children(el) if session.defs.same_definition(definition_ref(x), c.path)])
            for i in range(have, c.lower):
                create_container(session, el, c)
                n += 1
    return n
