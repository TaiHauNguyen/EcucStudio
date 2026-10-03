"""Command line entry point.

    python -m ecucstudio [gui] [project.dpa]
    python -m ecucstudio info     project.dpa
    python -m ecucstudio diag     project.dpa          (unresolved definitions, split BSWMD files)
    python -m ecucstudio validate project.dpa [--modules Com,Det] [--json out.json] [--davinci]
    python -m ecucstudio set      project.dpa "/ActiveEcuC/Det/DetGeneral[0:DetEnableDet]" true [--save]
    python -m ecucstudio generate project.dpa [-m /MICROSAR/Det,/MICROSAR/Com] [--gen-type REAL]
    python -m ecucstudio gateway  ...                  (CAN <-> Ethernet gateway generator, see --help)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time


MIN_PY = (3, 8)


def log_path():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "EcucStudio")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "ecucstudio.log")


def write_log(text):
    try:
        with open(log_path(), "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text.rstrip() + "\n")
    except OSError:
        pass


def check_environment() -> int:
    """0 = ok, 1 = missing pip-installable package, 2 = fatal (Python too old / no tkinter)."""
    rc = 0
    print(f"Python {sys.version.split()[0]} at {sys.executable}")
    if sys.version_info < MIN_PY:
        print(f"[ERROR] Python {MIN_PY[0]}.{MIN_PY[1]} or newer is required.")
        return 2
    try:
        import tkinter
        print(f"[OK]    tkinter {tkinter.TkVersion}")
    except ImportError as e:
        print(f"[ERROR] tkinter is not available ({e}). Re-install Python and enable 'tcl/tk and IDLE'.")
        rc = 2
    try:
        import lxml.etree
        print(f"[OK]    lxml {'.'.join(map(str, lxml.etree.LXML_VERSION[:3]))}")
    except ImportError:
        print("[MISSING] lxml  ->  pip install -r requirements.txt")
        rc = max(rc, 1)
    try:
        import cantools
        print(f"[OK]    cantools {cantools.__version__}")
    except ImportError:
        print("[OPTIONAL] cantools is missing (only the CAN-Ethernet gateway generator needs it)")
    return rc


def run_window(label, start):
    """Run a GUI entry point; errors go to the log and, without a console (pythonw), to a dialog."""
    import traceback
    write_log(f"start {label} (python {sys.version.split()[0]}, {sys.executable})")
    try:
        start()
    except Exception:
        tb = traceback.format_exc()
        write_log(f"{label} crashed:\n" + tb)
        sys.stderr.write(tb)
        try:  # pythonw has no console: show the error in a dialog
            import tkinter
            from tkinter import messagebox
            r = tkinter.Tk()
            r.withdraw()
            messagebox.showerror("EcucStudio", f"EcucStudio could not start:\n\n{tb[-1500:]}\n\nLog: {log_path()}")
            r.destroy()
        except Exception:
            pass
        return 1
    return 0


def _run_gui(dpa):
    def start():
        from .gui.app import main as gui_main
        gui_main(dpa)
    return run_window(f"GUI project={dpa or '-'}", start)


def _dvcfgcmd_candidates(a):
    """--dvcfgcmd first, then the path chosen in the GUI (Project Settings)."""
    from .settings import Settings
    out = [a.dvcfgcmd] if getattr(a, "dvcfgcmd", None) else []
    cfg = Settings().get("dvcfgcmd")
    if cfg:
        out.append(cfg)
    return out


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


def cmd_diag(a):
    """Explain unresolved definitions (e.g. 'The definition of this element was not found')."""
    from collections import defaultdict
    from . import davinci
    from .project import definition_ref
    s = _session(a.project)
    p, d = s.project, s.defs
    print(f"SIP folder     : {p.sip_dir}  (exists: {os.path.isdir(p.sip_dir)})")
    print(f"Extra BSWMD    : {', '.join(p.add_bswmds) or '-'}")
    print(f"BSWMD files    : {len(d.files)}, module definitions: {len(d.module_index)}")
    ver = davinci.sip_tool_version(p.sip_dir)
    print(f"SIP DaVinci    : {ver[0] + ' ' + ver[1] if ver else 'not in SIP'}")
    split = {k: v["files"] for k, v in d.module_index.items() if len(v.get("files", [])) > 1}
    print(f"Split modules  : {len(split)} (definition spread over several files, merged)")
    for k, files in sorted(split.items())[:30]:
        print(f"  {k}: " + ", ".join(os.path.basename(f) for f in files))
    missing = defaultdict(list)
    for path, el in s.model.path_index.items():
        dref = definition_ref(el)
        if d.find(dref) is None:
            missing[dref].append(path)
    for m in s.model.modules:
        if d.module(definition_ref(m)) is None:
            missing[definition_ref(m)].append(s.model.path_of(m))
    print(f"Unresolved definitions: {len(missing)} ({sum(len(v) for v in missing.values())} elements)")
    for dref, paths in sorted(missing.items())[:a.limit]:
        ex = d.explain(dref)
        print(f"- {dref}   ({len(paths)} element(s), e.g. {paths[0]})")
        print(f"    module: {ex['module'] or '-'}")
        for f in ex["files"]:
            print(f"    file  : {f}")
        if ex.get("nearest"):
            print(f"    deepest known: {ex['nearest']}")
        if ex.get("hint"):
            print(f"    reason: {ex['hint']}")
    return 1 if missing else 0


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
        inst = davinci.find_installations(s.project.sip_dir, _dvcfgcmd_candidates(a))
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
    inst = davinci.find_installations(p.sip_dir, _dvcfgcmd_candidates(a))
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
    if argv and argv[0] == "--check":
        return check_environment()
    if argv and argv[0] == "gateway":
        from .gateway.cli import main as gateway_main
        return gateway_main(argv[1:])
    if not argv or argv[0].lower().endswith(".dpa") or argv[0] == "gui":
        if argv and argv[0] == "gui":
            argv = argv[1:]
        return _run_gui(argv[0] if argv else None)
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
    p = sub.add_parser("diag", help="explain unresolved BSWMD definitions")
    p.add_argument("project")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(fn=cmd_diag)
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
