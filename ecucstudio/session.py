"""A loaded workspace: project, definitions, ECUC model, validation state."""
from __future__ import annotations

import os
import tempfile
import time

from . import settings as settings_mod
from .bswmd import DefinitionRepository
from .project import EcucModel, read_dpa
from .validation import ValidationContext, default_validator
from .validation.basic import validate_container

HERE = os.path.dirname(os.path.abspath(__file__))
BUILTIN_RULES = os.path.join(os.path.dirname(HERE), "rules")


class Session:
    def __init__(self, cfg: settings_mod.Settings | None = None):
        self.cfg = cfg or settings_mod.Settings()
        self.project = None
        self.defs: DefinitionRepository | None = None
        self.model: EcucModel | None = None
        self.results = []           # local validation results
        self.dv_results = []        # results imported from DaVinci report
        self.validator = None
        self.load_time = 0.0

    # ----------------------------------------------------------------- loading
    def open_dpa(self, dpa: str, progress=None):
        t = time.time()
        step = progress or (lambda msg, frac=None: None)
        step("Reading project file", 0.02)
        self.project = read_dpa(dpa)
        self._load(self.project.sip_dir, self.project.add_bswmds, self.project.ecuc_files(),
                   self.project.initial_ecuc, step)
        self.load_time = time.time() - t
        return self

    def open_files(self, files, sip_dir, extra_bswmd=(), progress=None):
        """Open loose ECUC arxml files (no .dpa) with definitions from *sip_dir*."""
        t = time.time()
        self.project = None
        self._load(sip_dir, list(extra_bswmd), list(files), None, progress or (lambda m, f=None: None))
        self.load_time = time.time() - t
        return self

    def _load(self, sip_dir, extra, files, initial, step):
        step("Indexing BSWMD definitions", 0.05)
        self.defs = DefinitionRepository(sip_dir, extra, cache_dir=settings_mod.CACHE_DIR)
        self.defs.scan(progress=lambda i, n, f: step(f"Indexing BSWMD {i}/{n}", 0.05 + 0.3 * i / max(n, 1)))
        step("Loading ECUC files", 0.35)
        self.model = EcucModel().load(files, progress=lambda i, n, f: step(
            f"Loading {os.path.basename(f)}", 0.35 + 0.45 * i / max(n, 1)))
        if initial:
            step("Loading initial (derived) configuration", 0.82)
            self.model.load_initial(initial)
        step("Loading AUTOSAR standard definitions (fallback)", 0.88)
        from .fallback import FallbackDefinitions, standard_definition_dirs
        self.fallback = FallbackDefinitions(self.defs, self.model,
                                            standard_definition_dirs(sip_dir, self.cfg.get("dvcfgcmd")),
                                            cache_dir=settings_mod.CACHE_DIR)
        step("Preparing module definitions", 0.9)
        for m in self.model.modules:
            from .project import definition_ref
            self.defs.module(definition_ref(m))
        self.validator = default_validator(self.plugin_dirs())
        step("Ready", 1.0)

    def plugin_dirs(self):
        dirs = [BUILTIN_RULES] + list(self.cfg.get("plugin_dirs", []))
        return [d for d in dirs if d and os.path.isdir(d)]

    # --------------------------------------------------------------- validation
    def context(self, **options):
        opts = {"local_acks": self._local_acks()}
        opts.update(options)
        return ValidationContext(self.model, self.defs, self.project, opts)

    def _local_acks(self):
        acks = self.cfg.acks_for(self.project.path if self.project else "")
        out = {}
        for k, comment in acks.items():
            out[k] = comment
        return out

    def validate(self, progress=None, cancel=None, **options):
        self.validator = default_validator(self.plugin_dirs())
        self.results = self.validator.run(self.context(**options), progress=progress, cancel=cancel)
        return self.results

    def container_def(self, el):
        """(definition, source) for editing *el*; source is None for a normal SIP BSWMD definition,
        otherwise a text telling that a fallback (AUTOSAR standard / inferred from ECUC) is used."""
        fb = getattr(self, "fallback", None)
        if fb is None:
            from .project import definition_ref
            return self.defs.find(definition_ref(el)), None
        return fb.container_def(el)

    def validate_container(self, el):
        return validate_container(self.context(), el)

    def acknowledge(self, result, comment: str):
        acks = self.cfg.acks_for(self.project.path if self.project else "")
        acks[f"{result.rule_id}|{result.obj or ''}|{result.message.strip()}"] = comment
        result.acknowledged = comment
        self.cfg.save()

    def revoke(self, result):
        acks = self.cfg.acks_for(self.project.path if self.project else "")
        acks.pop(f"{result.rule_id}|{result.obj or ''}|{result.message.strip()}", None)
        result.acknowledged = None
        self.cfg.save()

    # ------------------------------------------------------------ parameter state
    def preconfig_map(self, module_el):
        """{relative container path: {def path: value}} of the module's active pre-configuration."""
        from . import arxml
        from .project import CONTAINER_TAG, definition_ref, raw_value, value_elements
        from .validation.basic import StructureRule
        cache = self.__dict__.setdefault("_pre_cache", {})
        if module_el in cache:
            return cache[module_el]
        out = {}
        mdef = self.defs.module(definition_ref(module_el))
        if mdef is not None and mdef.impl is not None:
            checker = StructureRule()
            checker.defs = self.defs
            for pre in mdef.impl.preconfig:
                pel = self.defs.config_values(pre, mdef.impl.file)
                if pel is None or not checker._preconfig_applies(pel, mdef.impl.file):
                    continue
                base = arxml.ar_path(pel)
                for ppath, pc in arxml.iter_identifiables(pel, {CONTAINER_TAG}, base.rsplit("/", 1)[0]):
                    rel = ppath[len(base):]
                    vals = out.setdefault(rel, {})
                    for v in value_elements(pc):
                        vals[definition_ref(v)] = raw_value(v)
        cache[module_el] = out
        return out

    def param_state(self, container, pdef, v):
        """(state label, read-only reason or None) following DaVinci's *Parameter States*."""
        from .project import is_auto_value, is_user_defined, raw_value
        from .validation.basic import _same_value
        if v is None:
            return "not set", None
        model = self.model
        if is_user_defined(v):
            return "user-defined", None
        mod = model.module_of(container)
        rel_mod = model.path_of(container)[len(model.path_of(mod)):]
        pre = self.preconfig_map(mod).get(rel_mod, {}).get(pdef.path)
        if pre is not None:
            return "pre-configured", "Pre-configured"
        rel = model.relative_path(container)
        key = (rel, pdef.path)
        if key in model.initial_values:
            iv = model.initial_values[key]
            if key in model.initial_soft:
                return "derived (soft)", None
            if _same_value(pdef.kind, raw_value(v), iv):
                return "derived", "Derived (from input files)"
        if is_auto_value(v):
            return "calculated", None
        if pdef.default is not None and _same_value(pdef.kind, raw_value(v), pdef.default):
            return "default", None
        return "", None

    # ------------------------------------------------------------------- misc
    def report_dir(self):
        # reports/logs go to the user's app data, the project folder stays untouched
        name = self.project.name if self.project else "files"
        d = os.path.join(os.path.dirname(settings_mod.CACHE_DIR), "reports", name)
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            d = os.path.join(tempfile.gettempdir(), "EcucStudio")
            os.makedirs(d, exist_ok=True)
        return d
