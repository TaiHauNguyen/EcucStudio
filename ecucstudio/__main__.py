"""Command line entry point.

    python -m ecucstudio [gui] [project.dpa]
    python -m ecucstudio info     project.dpa
    python -m ecucstudio validate project.dpa [--modules Com,Det] [--json out.json] [--davinci]
    python -m ecucstudio set      project.dpa "/ActiveEcuC/Det/DetGeneral[0:DetEnableDet]" true [--save]
    python -m ecucstudio generate project.dpa [-m /MICROSAR/Det,/MICROSAR/Com] [--gen-type REAL]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time


def _session(dpa):
    from .session import Session
    t = time.time()
    s = Session().open_dpa(dpa, progress=lambda m, f=None: None)
    print(f"Loaded {s.project.name}: {len(s.model.modules)} modules, {len(s.model.path_index)} containers "
          f"({time.time() - t:.1f} s)")
    return s


def cmd_info(a):
    s = _session(a.project)
    p = s.project
    print(f"Derivative {p.derivative}, compiler {p.compiler}, SIP {', '.join(p.sip_ids)} at {p.sip_dir}")
    from . import davinci
    for i in davinci.find_installations(p.sip_dir):
        print("DVCfgCmd:", i.label)
    for m in s.model.modules:
        from .project import definition_ref
        from . import arxml
        print(f"  {arxml.short_name(m):<20} {definition_ref(m)}")
    return 0


def _print_results(results, limit):
    from collections import Counter
    c = Counter((r.severity.label, r.rule_id) for r in results)
    for (sev, rid), n in sorted(c.items(), key=lambda x: (-["Info", "Improvement", "Warning", "Error",
                                                            "FatalError"].index(x[0][0]), x[0][1])):
        print(f"  {sev:<11} {rid:<14} {n}")
    shown = 0
    for r in sorted(results, key=lambda r: -int(r.severity)):
        if shown >= limit:
            break
        shown += 1
        print(f"[{r.severity.label}] {r.rule_id} ({r.source}) {r.message.splitlines()[0][:220]}")


def cmd_validate(a):
    s = _session(a.project)
    mods = [m.strip() for m in a.modules.split(",")] if a.modules else None
    t = time.time()
    res = s.validate(modules=mods)
    print(f"Local validation: {len(res)} result(s) in {time.time() - t:.1f} s")
    if a.davinci:
        from . import davinci
        inst = davinci.find_installations(s.project.sip_dir, [a.dvcfgcmd] if a.dvcfgcmd else [])
        if not inst:
            print("DVCfgCmd.exe not found", file=sys.stderr)
            return 2
        report = os.path.join(s.report_dir(), "ValidationReport.xml")
        run = davinci.DvRun(davinci.build_validate_cmd(inst[0].exe, s.project.path, report),
                            on_line=lambda l: print("  dv>", l) if a.verbose else None).start()
        rc = run.wait()
        rep = davinci.parse_report(report)
        print(f"DaVinci validation exit code {rc}: {len(rep.validation)} result(s) {rep.counts}")
        res = res + rep.validation
    _print_results(res, a.limit)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump([{"id": r.rule_id, "severity": r.severity.label, "source": r.source, "object": r.obj,
                        "definition": r.definition, "message": r.message, "acknowledged": r.acknowledged}
                       for r in res], fh, indent=1)
    from .validation import Severity
    return 1 if any(r.severity >= Severity.ERROR and not r.acknowledged for r in res) else 0


def cmd_set(a):
    from .project import parse_object_ref
    s = _session(a.project)
    path, param, idx = parse_object_ref(a.object)
    el = s.model.resolve(path)
    if el is None or not param:
        print(f"Container not found or no parameter given: {a.object}", file=sys.stderr)
        return 2
    from .project import definition_ref
    cdef = s.defs.find(definition_ref(el))
    pdef = cdef.child(param) if cdef else None
    if pdef is None:
        print(f"Parameter {param} is not defined for {cdef.path if cdef else path}", file=sys.stderr)
        return 2
    s.model.set_value(el, pdef, a.value, index=idx or 0)
    res = s.validate_container(el)
    for r in res:
        print(f"[{r.severity.label}] {r.rule_id} {r.message.splitlines()[0]}")
    if a.save:
        print("Saved:", ", ".join(s.model.save()))
    else:
        print("Not saved (use --save)")
    return 1 if res else 0


def cmd_generate(a):
    from . import davinci
    from .project import read_dpa
    p = read_dpa(a.project)
    inst = davinci.find_installations(p.sip_dir, [a.dvcfgcmd] if a.dvcfgcmd else [])
    if not inst:
        print("DVCfgCmd.exe not found", file=sys.stderr)
        return 2
    opts = davinci.GenerateOptions(modules=[m for m in (a.modules or "").split(",") if m],
                                   gen_type=a.gen_type, save_project=a.save_project)
    from .settings import CACHE_DIR
    rdir = os.path.join(os.path.dirname(CACHE_DIR), "reports", p.name)
    os.makedirs(rdir, exist_ok=True)
    out = a.report or os.path.join(rdir, "GenerationReport.xml")
    cmd = davinci.build_generate_cmd(inst[0].exe, p.path, out, opts)
    print(">", davinci.format_cmd(cmd))
    rc = davinci.DvRun(cmd, on_line=print).start().wait()
    rep = davinci.parse_report(out)
    print(f"Exit code {rc}: {rep.process_result}")
    for g in rep.generation:
        if g.state != "SUCCESSFUL":
            print(f"  {g.name}: {g.state} {g.error[:200]}")
    return rc


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].lower().endswith(".dpa") or argv[0] == "gui":
        if argv and argv[0] == "gui":
            argv = argv[1:]
        from .gui.app import main as gui_main
        gui_main(argv[0] if argv else None)
        return 0
    ap = argparse.ArgumentParser(prog="ecucstudio")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("info")
    p.add_argument("project")
    p.set_defaults(fn=cmd_info)
    p = sub.add_parser("validate")
    p.add_argument("project")
    p.add_argument("--modules")
    p.add_argument("--json")
    p.add_argument("--davinci", action="store_true", help="also run DVCfgCmd -v and merge its results")
    p.add_argument("--dvcfgcmd")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(fn=cmd_validate)
    p = sub.add_parser("set")
    p.add_argument("project")
    p.add_argument("object", help='e.g. "/ActiveEcuC/Det/DetGeneral[0:DetEnableDet]"')
    p.add_argument("value")
    p.add_argument("--save", action="store_true")
    p.set_defaults(fn=cmd_set)
    p = sub.add_parser("generate")
    p.add_argument("project")
    p.add_argument("-m", "--modules", help="comma separated module definition paths or short names")
    p.add_argument("--gen-type", choices=("REAL", "VTT"))
    p.add_argument("--save-project", action="store_true")
    p.add_argument("--dvcfgcmd")
    p.add_argument("--report")
    p.set_defaults(fn=cmd_generate)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
