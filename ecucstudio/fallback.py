"""Definitions for ECUC elements whose vendor BSWMD definition cannot be found.

Order of precedence (used by the editor so every module stays viewable/editable):
1. the SIP's BSWMD (normal case, handled by DefinitionRepository),
2. the AUTOSAR standard definition shipped with DaVinci Configurator
   (``<DVCfg>/StandardDefinition/AUTOSAR_MOD_ECUConfigurationParameters*.arxml``), re-rooted
   onto the vendor path used in the ECUC so written DEFINITION-REFs stay correct,
3. a definition inferred from the ECUC itself: every value carries
   ``DEFINITION-REF DEST="ECUC-...-DEF"`` which tells its type.
"""
from __future__ import annotations

import dataclasses
import math
import os

from .bswmd import CONTAINER_KINDS, PARAM_KINDS, REF_KINDS, Def, DefinitionRepository
from .project import MODULE_TAG, container_children, definition_ref, value_elements
from . import arxml
from .arxml import q

SOURCE_STANDARD = "AUTOSAR standard definition (DaVinci StandardDefinition)"
SOURCE_ECUC = "inferred from the ECUC file (no definition found)"


def standard_definition_dirs(sip_dir=None, dvcfgcmd=None):
    cands = []
    if dvcfgcmd:
        cands.append(os.path.join(os.path.dirname(dvcfgcmd), "StandardDefinition"))
    if sip_dir:
        cands.append(os.path.join(sip_dir, "DaVinciConfigurator", "Core", "StandardDefinition"))
    return [c for c in cands if os.path.isdir(c)]


def reroot(d: Def, path: str, parent=None, synthetic=True) -> Def:
    """Deep copy of definition *d* (and its sub tree) placed at vendor definition *path*."""
    c = dataclasses.replace(d, path=path, parent=parent, children=[], module=None if synthetic else d.module)
    c.synthetic = synthetic
    for ch in d.children:
        c.children.append(reroot(ch, path + "/" + ch.name, c, synthetic))
    return c


def _dest_of(el):
    r = el.find(q("DEFINITION-REF"))
    return r.get("DEST", "") if r is not None else ""


class FallbackDefinitions:
    def __init__(self, defs: DefinitionRepository, model, std_dirs=(), cache_dir=None):
        self.defs = defs
        self.model = model
        self.std = DefinitionRepository(None, list(std_dirs), cache_dir=cache_dir).scan() if std_dirs else None
        self._cache = {}

    # ------------------------------------------------------------ helpers
    def _module_name(self, el):
        m = self.model.module_of(el)
        return definition_ref(m).rsplit("/", 1)[-1] if m is not None else None, m

    def standard_for(self, def_path, el):
        """AUTOSAR standard Def for a vendor definition path, or None."""
        if self.std is None:
            return None
        name, mod = self._module_name(el)
        if not name:
            return None
        mref = definition_ref(mod)
        rest = def_path[len(mref):] if def_path.startswith(mref) else ""
        return self.std.find(f"/AUTOSAR/EcucDefs/{name}{rest}")

    # ---------------------------------------------------------------- API
    def container_def(self, el):
        """(Def, source) usable by the editor for module/container *el*; source None = SIP BSWMD."""
        dref = definition_ref(el)
        d = self.defs.find(dref)
        if d is not None and not self._has_unknown_children(el, d):
            return d, None
        key = (id(el), dref)
        if key in self._cache:
            return self._cache[key]
        std = self.standard_for(dref, el) if d is None else None
        res = (self._build(el, dref, base=d or std), None if d is not None else
               (SOURCE_STANDARD if std is not None else SOURCE_ECUC))
        self._cache[key] = res
        return res

    def invalidate(self):
        self._cache.clear()

    def _has_unknown_children(self, el, d):
        known = {c.path for c in d.children}
        for v in value_elements(el) if arxml.local(el) != MODULE_TAG else []:
            if definition_ref(v) not in known and not any(self.defs.same_definition(definition_ref(v), k)
                                                          for k in known):
                return True
        for c in container_children(el):
            if definition_ref(c) not in known and not any(self.defs.same_definition(definition_ref(c), k)
                                                          for k in known):
                return True
        return False

    def _build(self, el, dref, base):
        """Container Def at vendor path *dref*: children of *base* (re-rooted) + whatever the ECUC has."""
        is_module = arxml.local(el) == MODULE_TAG
        kind = "module" if is_module else CONTAINER_KINDS.get(_dest_of(el), "container")
        tag = "ECUC-MODULE-DEF" if is_module else (_dest_of(el) or "ECUC-PARAM-CONF-CONTAINER-DEF")
        syn = Def(path=dref, name=dref.rsplit("/", 1)[-1], tag=tag, kind=kind,
                  lower=base.lower if base else 0, upper=base.upper if base else 1,
                  desc=base.desc if base else "", long_name=base.long_name if base else "",
                  origin=(base.origin if base else "") or "")
        syn.synthetic = True
        seen = set()
        if base is not None:
            rerooted = base.path != dref
            for c in base.children:
                if rerooted:
                    copy = reroot(c, dref + "/" + c.name, syn)
                else:       # SIP definition with extra ECUC children: keep the real definitions
                    copy = c
                syn.children.append(copy)
                seen.add(c.name)
        # values present in the ECUC but unknown to the definition
        if not is_module:
            for v in value_elements(el):
                vref = definition_ref(v)
                name = vref.rsplit("/", 1)[-1]
                if name in seen:
                    continue
                seen.add(name)
                dest = _dest_of(v)
                k = PARAM_KINDS.get(dest) or REF_KINDS.get(dest) or (
                    "reference" if arxml.local(v) == "ECUC-REFERENCE-VALUE" else "string")
                p = Def(path=vref, name=name, tag=dest or "ECUC-STRING-PARAM-DEF", kind=k, parent=syn,
                        lower=0, upper=math.inf, origin="ECUC", desc="Definition not found — type taken from "
                                                                     "the ECUC value (DEFINITION-REF DEST).")
                if k == "foreign":
                    r = v.find(q("VALUE-REF"))
                    p.dest_type = r.get("DEST") if r is not None else None
                p.synthetic = True
                syn.children.append(p)
        for c in container_children(el):
            cref = definition_ref(c)
            name = cref.rsplit("/", 1)[-1]
            if name in seen:
                continue
            seen.add(name)
            std = self.standard_for(cref, c)
            if std is not None:
                sub = reroot(std, cref, syn)
            else:
                sub = Def(path=cref, name=name, tag=_dest_of(c) or "ECUC-PARAM-CONF-CONTAINER-DEF",
                          kind=CONTAINER_KINDS.get(_dest_of(c), "container"), parent=syn, lower=0,
                          upper=math.inf, origin="ECUC")
                sub.synthetic = True
            syn.children.append(sub)
        return syn
