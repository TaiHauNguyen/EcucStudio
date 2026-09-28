"""Validation engine: results, solving actions, rule registry and plugin loading."""
from __future__ import annotations

import importlib.util
import os
import traceback
from dataclasses import dataclass, field
from enum import IntEnum


class Severity(IntEnum):
    INFO = 0
    IMPROVEMENT = 1
    WARNING = 2
    ERROR = 3
    FATAL = 4

    @property
    def label(self) -> str:
        return {0: "Info", 1: "Improvement", 2: "Warning", 3: "Error", 4: "FatalError"}[int(self)]

    @classmethod
    def parse(cls, s: str) -> "Severity":
        s = (s or "").lower()
        return {"info": cls.INFO, "improvement": cls.IMPROVEMENT, "warning": cls.WARNING,
                "error": cls.ERROR, "fatalerror": cls.FATAL, "fatal": cls.FATAL}.get(s, cls.INFO)


@dataclass
class SolvingAction:
    description: str
    apply: callable
    preferred: bool = False


@dataclass
class Result:
    rule_id: str                       # e.g. AR-ECUC02008, Cfg00022, COM02335
    severity: Severity
    title: str                         # short text of the result id group
    message: str                       # full description
    obj: str | None = None             # object path (DaVinci CE format)
    definition: str | None = None
    source: str = "local"              # local | plugin:<name> | DaVinci
    element: object = None             # lxml element for navigation (container/value)
    param: str | None = None           # parameter definition path (for "not instantiated")
    actions: list[SolvingAction] = field(default_factory=list)
    acknowledged: str | None = None
    ondemand: bool = False

    @property
    def short_id(self) -> str:
        """DaVinci's acknowledgement format: COM02335 -> COM2335, Cfg00020 -> Cfg20."""
        i = len(self.rule_id)
        while i > 0 and self.rule_id[i - 1].isdigit():
            i -= 1
        num = self.rule_id[i:]
        return self.rule_id[:i] + (str(int(num)) if num else "")

    @property
    def preferred_action(self):
        for a in self.actions:
            if a.preferred:
                return a
        return self.actions[0] if len(self.actions) == 1 else None


class Rule:
    """Base class of a validation rule. Subclasses implement :meth:`check`."""
    id = "RULE00000"
    title = ""
    default_severity = Severity.ERROR
    enabled = True

    def check(self, ctx: "ValidationContext"):  # pragma: no cover - interface
        return []


@dataclass
class ValidationContext:
    model: object          # project.EcucModel
    defs: object           # bswmd.DefinitionRepository
    project: object = None # project.DpaProject
    options: dict = field(default_factory=dict)


class Validator:
    def __init__(self):
        self.rules: list[Rule] = []
        self.errors: list[str] = []

    def add(self, rule: Rule):
        self.rules.append(rule)

    def load_plugins(self, folder: str):
        """Load every ``*.py`` in *folder*; each may define ``RULES = [Rule(), ...]``."""
        if not folder or not os.path.isdir(folder):
            return
        for f in sorted(os.listdir(folder)):
            if not f.endswith(".py") or f.startswith("_"):
                continue
            path = os.path.join(folder, f)
            try:
                spec = importlib.util.spec_from_file_location(f"ecucstudio_rule_{f[:-3]}", path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                for r in getattr(mod, "RULES", []):
                    r.source = f"plugin:{f[:-3]}"
                    self.rules.append(r)
            except Exception:  # a broken plugin must not break the tool
                self.errors.append(f"{f}: {traceback.format_exc(limit=2)}")

    def run(self, ctx: ValidationContext, progress=None, cancel=None) -> list[Result]:
        results: list[Result] = []
        active = [r for r in self.rules if r.enabled]
        for i, rule in enumerate(active):
            if cancel is not None and cancel.is_set():
                break
            if progress:
                progress(i, len(active), rule.title or rule.id)
            try:
                for res in rule.check(ctx) or []:
                    if res.source == "local" and getattr(rule, "source", None):
                        res.source = rule.source
                    results.append(res)
            except Exception:
                self.errors.append(f"{rule.id}: {traceback.format_exc(limit=3)}")
        apply_acknowledgements(results, getattr(ctx.project, "acknowledgements", []) or [],
                               ctx.options.get("local_acks", {}))
        return results


def apply_acknowledgements(results, dpa_acks, local_acks):
    """Mark results acknowledged in the .dpa (DaVinci) or in the tool's side-car file."""
    by_key = {}
    for a in dpa_acks:
        by_key[(a.get("id"), (a.get("description") or "").strip())] = a.get("comment") or "acknowledged"
    for r in results:
        if r.severity >= Severity.ERROR:
            continue
        key = (r.short_id, r.message.strip())
        if key in by_key:
            r.acknowledged = by_key[key]
        elif key in local_acks:
            r.acknowledged = local_acks[key]
        else:
            k2 = f"{r.rule_id}|{r.obj or ''}|{r.message.strip()}"
            if k2 in local_acks:
                r.acknowledged = local_acks[k2]


def default_validator(plugin_dirs=()) -> Validator:
    from . import basic
    v = Validator()
    for r in basic.RULES:
        v.add(r)
    for d in plugin_dirs:
        v.load_plugins(d)
    return v
