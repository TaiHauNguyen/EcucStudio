"""Command line of the CAN <-> Ethernet gateway generator.

    python -m ecucstudio gateway inspect  --base network.arxml [--dbc bus.dbc]
    python -m ecucstudio gateway template [--base network.arxml] --dbc bus.dbc --node ECU -o gateway.json
    python -m ecucstudio gateway suggest  gateway.json [--write]
    python -m ecucstudio gateway plan     gateway.json | generated_gateway.arxml
    python -m ecucstudio gateway reopen   generated_gateway.arxml -o gateway.json
    python -m ecucstudio gateway generate gateway.json [-o out.arxml]
    python -m ecucstudio gateway gui      [gateway.json]
    python -m ecucstudio gateway routes   network.arxml [--csv routes.csv]
    python -m ecucstudio gateway edit     network.arxml --header PDU=0x123 --delete PDU --port SOCKET=50000
    python -m ecucstudio gateway editor   [network.arxml]
    python -m ecucstudio gateway report   gateway.json | generated_gateway.arxml | topology.json [-o report]
    python -m ecucstudio gateway topology plan|generate|contract topology.json [--ecu ZoneB]
    python -m ecucstudio gateway topology gui [topology.json]
"""
from __future__ import annotations

import argparse
import os
import sys
import time


def _load(path):
    """A gateway.json, or a generated gateway file (its configuration, to regenerate it)."""
    if path.lower().endswith(".arxml"):
        from .regen import config_from_file
        cfg, notes = config_from_file(path)
        for n in notes:
            print("[INFO]   ", n)
        return cfg
    from .config import GatewayConfig
    return GatewayConfig.load(path)


def cmd_reopen(a):
    from .regen import config_from_file
    cfg, notes = config_from_file(a.file)
    for n in notes:
        print("[INFO]   ", n)
    cfg.save(a.out)
    print("Written", os.path.abspath(a.out), "- edit it (buses, messages) and run 'generate' to regenerate",
          os.path.basename(a.file))
    return 0


def _print_plan(plan, verbose=True):
    from . import report
    print(report.summary(plan))
    if verbose and plan.routes:
        print()
        print(report.text_table(plan))
    for w in plan.infos:
        print("[INFO]   ", w)
    for w in plan.warnings:
        print("[WARNING]", w)
    for e in plan.errors:
        print("[ERROR]  ", e)


def cmd_inspect(a):
    from . import dbcread, dvproject, report
    from .base import Base
    if a.base and dvproject.is_project(a.base):
        proj = dvproject.read(a.base)
        base = dvproject.load_communication(proj)
        print(f"DaVinci project {proj.name}: ECU instance {proj.ecu_path}")
        print(f"Communication description: {proj.communication}")
        for i in proj.inputs:
            print(f"  input {i.category:<34} {os.path.basename(i.path)}")
        print(report.inventory(base))
        print(f"CAN channels of {proj.ecu_name} (messages received / sent):")
        for ch, rx, tx in dvproject.ecu_can_channels(base, proj.ecu_path):
            print(f"  {ch:<50} {rx:>4} / {tx:<4}")
    elif a.base:
        print(report.inventory(Base(a.base)))
    for path in a.dbc or []:
        db = dbcread.load(path)
        print(f"\nDBC {db.name}: {len(db.messages)} messages, baud {db.baudrate or '?'}"
              f"{', CAN FD' if db.has_fd else ''}")
        for n in db.nodes:
            rx, tx = db.node_messages(n)
            print(f"  node {n:<24} receives {len(rx):>4}   sends {len(tx):>4}")
    return 0


def cmd_template(a):
    from . import dvproject
    from .config import BusInput, GatewayConfig
    proj = dvproject.read(a.base) if a.base and dvproject.is_project(a.base) else None
    if not proj and not a.dbc:
        print("[ERROR] give --dbc (or --base with a DaVinci project .dpa)", file=sys.stderr)
        return 2
    if proj:                    # additional input file of the project
        default_out = os.path.join(proj.dir, f"{proj.ecu_name}_CanEthGateway.arxml")
    elif a.base:
        default_out = os.path.splitext(a.base)[0] + "_gateway.arxml"
    else:                       # only DBC files: a new network file next to the first DBC
        default_out = os.path.splitext(a.dbc[0])[0] + "_network.arxml"
    cfg = GatewayConfig(base=os.path.abspath(a.base) if a.base else "",
                        output=os.path.abspath(a.output_arxml or default_out), ecu=a.ecu or "",
                        schema=a.schema)
    for d in a.dbc or []:
        cfg.buses.append(BusInput(dbc=os.path.abspath(d), node=a.node or ""))
    if proj:
        channels = a.can_channel or [c for c, _rx, _tx in dvproject.ecu_can_channels(
            dvproject.load_communication(proj), proj.ecu_path)]
        for c in channels:
            cfg.buses.append(BusInput(channel=c))
    cfg.ethernet.channel = a.channel or ""
    cfg.ethernet.vlan_id = a.vlan
    cfg.ethernet.ecu_ip = a.ecu_ip or ""
    cfg.ethernet.new_channel = bool(a.new_channel)
    if not a.no_suggest:
        from .suggest import apply, suggest
        try:
            for s in apply(cfg, suggest(cfg)):
                print("[SUGGESTED]", s)
        except (OSError, RuntimeError, ValueError) as exc:
            print("[INFO]      no Ethernet suggestion:", exc)
    cfg.save(a.out)
    print("Written", os.path.abspath(a.out), "- check the Ethernet settings and run 'plan'.")
    if proj and not a.can_channel:
        print(f"All {len(cfg.buses)} CAN channel(s) of {proj.ecu_name} are listed in 'buses'; remove the ones "
              f"that are not routed.")
    return 0


def cmd_suggest(a):
    from .suggest import apply, suggest
    cfg = _load(a.config)
    sugg = suggest(cfg)
    applied = apply(cfg, sugg) if a.write else []
    for s in sugg:
        tag = "[WRITTEN]  " if s in applied else ("[KEPT]     " if a.write else "[SUGGESTED]")
        print(tag, s)
    if not sugg:
        print("Nothing to suggest: all Ethernet settings are filled in.")
    if a.write and applied:
        cfg.save(a.config)
        print("Updated", os.path.abspath(a.config))
    return 0


def cmd_plan(a):
    from .planner import make_plan
    t = time.time()
    plan = make_plan(_load(a.config))
    _print_plan(plan)
    from . import report
    print(f"\n{report.count_text(len(plan.enabled_routes), len(plan.enabled_can_routes))}, "
          f"{len(plan.warnings)} warning(s), {len(plan.errors)} error(s) "
          f"({time.time() - t:.1f} s)")
    return 1 if plan.errors else 0


def cmd_generate(a):
    from . import report
    from .planner import make_plan
    from .writer import generate
    t = time.time()
    cfg = _load(a.config)
    if a.output:
        cfg.output = os.path.abspath(a.output)
    plan = make_plan(cfg)
    _print_plan(plan, verbose=a.verbose)
    if plan.errors:
        print("\nNothing written: fix the errors above.")
        return 1
    res = generate(plan)
    csv_path = os.path.splitext(res.output)[0] + "_gateway_routes.csv"
    report.write_csv(plan, csv_path)
    from . import paths
    html_path, _ = paths.write_report(paths.report_of_plan(plan), os.path.splitext(res.output)[0])
    for w in res.warnings[len(plan.warnings):]:
        print("[WARNING]", w)
    print(f"\nWritten {res.output} ({report.count_text(len(res.routes), len(res.can_routes))}, "
          f"{time.time() - t:.1f} s)")
    print("Created:", ", ".join(f"{k} {v}" for k, v in sorted(res.created.items())))
    if res.extension:
        print("CAN -> CAN (PduR only, no Com):", res.extension,
              "- add it to the Input Files of the DaVinci project next to the DBC files")
    print("Route table:", csv_path)
    print("Message paths:", html_path)
    return 0


def _route_table(rows, cols):
    widths = [max(len(c), *(len(str(r[i])) for r in rows)) if rows else len(c) for i, c in enumerate(cols)]
    widths = [min(w, 60) for w in widths]
    line = lambda vals: "  ".join(str(v)[:w].ljust(w) for v, w in zip(vals, widths)).rstrip()
    return "\n".join([line(cols), line(["-" * w for w in widths])] + [line(r) for r in rows])


def cmd_routes(a):
    import csv
    from .existing import ROUTE_COLUMNS, GatewayModel, route_rows
    m = GatewayModel(a.file)
    routes = [r for r in m.routes if not a.direction or r.direction == a.direction]
    rows = route_rows(m, routes)
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(ROUTE_COLUMNS)
            w.writerows(rows)
        print("Written", os.path.abspath(a.csv))
    else:
        print(_route_table(rows, ROUTE_COLUMNS))
    import collections
    print(f"\n{len(routes)} route(s): " + ", ".join(f"{k} {v}" for k, v in
                                                     sorted(collections.Counter(r.direction for r in routes).items())))
    for c in m.check():
        print("[WARNING]", c)
    return 0


def cmd_edit(a):
    from .existing import EditError, GatewayModel
    m = GatewayModel(a.file)

    def one_route(name):
        hits = m.find_routes(name)
        if len(hits) != 1:
            raise EditError(f"'{name}' matches {len(hits)} routes (use the Ethernet PDU or header id name)")
        return hits[0]

    try:
        for item in a.header:
            name, _, value = item.partition("=")
            if name in {h for h in m.ids} or name in {p.rsplit("/", 1)[-1] for p in m.ids}:
                id_path = next(p for p in m.ids if name in (p, p.rsplit("/", 1)[-1]))
            else:
                ids = (one_route(name).eth.ids if one_route(name).eth else [])
                if len(ids) != 1:
                    raise EditError(f"route '{name}' has {len(ids)} header ids: give the header id name instead")
                id_path = ids[0].path
            m.set_header_id(id_path, value)
        for item in a.port:
            name, _, value = item.partition("=")
            hits = [s for s in m.sockets() if name in (s.path, s.name)]
            if len(hits) != 1:
                raise EditError(f"socket '{name}' not found (or ambiguous)")
            m.set_port(hits[0].path, value)
        for item in a.ip:
            name, _, value = item.partition("=")
            ip, _, mask = value.partition("/")
            hits = [e for e in m.endpoints() if name in (e.path, e.name)]
            if len(hits) != 1:
                raise EditError(f"endpoint '{name}' not found (or ambiguous)")
            m.set_endpoint(hits[0].path, ip or None, mask or None)
        if a.delete:
            routes = []
            for name in a.delete:
                hits = m.find_routes(name)
                if not hits:
                    raise EditError(f"no route matches '{name}'")
                routes += [r for r in hits if r not in routes]
            m.delete_routes(routes, cleanup=not a.keep_pdus)
    except EditError as exc:
        print("[ERROR]", exc, file=sys.stderr)
        print("Nothing written.")
        return 1
    for c in m.changes:
        print("[CHANGED]" if not c.startswith("warning") else "[WARNING]", c)
    if not m.changes:
        print("Nothing to change.")
        return 0
    out = m.save(a.output or None)
    print("Written", out + ("" if a.output else " (previous version kept as .bak)"))
    return 0


def cmd_editor(a):
    from ..__main__ import run_window

    def start():
        from .editor_gui import main as editor_main
        editor_main(a.file)
    return run_window(f"gateway editor file={a.file or '-'}", start)


def _topology_plan(path):
    from .topology import TopologyConfig, make_topology_plan
    from .topology import report as treport
    t = time.time()
    tplan = make_topology_plan(TopologyConfig.load(path))
    return tplan, treport, time.time() - t


def _print_topology(tplan, treport, verbose=True):
    print(treport.text(tplan, verbose))
    for kind, items in (("[INFO]   ", tplan.infos), ("[WARNING]", tplan.warnings), ("[ERROR]  ", tplan.errors)):
        for m in items:
            print(kind, m)


def cmd_topology_plan(a):
    tplan, treport, dt = _topology_plan(a.topology)
    _print_topology(tplan, treport)
    print(f"\n{len(tplan.enabled_cross)} ECU -> ECU route(s), {len(tplan.links)} Ethernet PDU(s), "
          f"{len(tplan.warnings)} warning(s), {len(tplan.errors)} error(s) ({dt:.1f} s)")
    return 0 if tplan.ok else 1


def cmd_topology_generate(a):
    from . import report
    from .topology import generate_topology
    tplan, treport, dt = _topology_plan(a.topology)
    _print_topology(tplan, treport, verbose=a.verbose)
    if not tplan.ok:
        print("\nNothing written: fix the errors above.")
        return 1
    for name, res in generate_topology(tplan, a.ecu or None):
        print(f"Written {res.output} ({name}: {report.count_text(len(res.routes), len(res.can_routes))})")
        if res.extension:
            print(f"Written {res.extension} ({name}: CAN -> CAN, PduR only, no Com - add it to the Input Files "
                  f"of the DaVinci project next to the DBC files)")
    print("Lock file:", tplan.cfg.lock_path)
    print("Contract :", tplan.cfg.contract_path)
    return 0


def cmd_topology_contract(a):
    from .topology import write_contract
    tplan, treport, dt = _topology_plan(a.topology)
    if not tplan.ok:
        _print_topology(tplan, treport, verbose=False)
        return 1
    print("Written", write_contract(tplan, a.output))
    return 0


def cmd_topology_gui(a):
    from ..__main__ import run_window

    def start():
        from .topology.gui import main as topology_main
        topology_main(a.topology)
    return run_window(f"gateway topology file={a.topology or '-'}", start)


def cmd_report(a):
    """Message path report of a gateway configuration / generated file, or of a topology."""
    import json
    from . import paths
    is_topology = False
    if a.config.lower().endswith(".json"):
        with open(a.config, encoding="utf-8-sig") as fh:
            is_topology = "ecus" in json.load(fh)
    if is_topology:
        from .topology import TopologyConfig, make_topology_plan
        tplan = make_topology_plan(TopologyConfig.load(a.config))
        errors = tplan.errors
        rep = paths.report_of_topology(tplan)
    else:
        from .planner import make_plan
        plan = make_plan(_load(a.config))
        errors = plan.errors
        rep = paths.report_of_plan(plan)
    for e in errors:
        print("[ERROR]  ", e)
    stem = os.path.splitext(a.output)[0] if a.output else os.path.splitext(os.path.abspath(a.config))[0]
    html_path, csv_path = paths.write_report(rep, stem)
    for p in rep.paths:
        print(f"{p.can_id:<12} {p.names:<30} {p.origin}  ->  {' -> '.join(p.via)}  ->  {p.destination}")
    print(f"\n{rep.messages} message(s), {len(rep.paths)} path(s), {len(rep.not_routed)} not routed")
    print("Written", html_path)
    print("Written", csv_path)
    return 1 if errors else 0


def cmd_gui(a):
    from ..__main__ import run_window

    def start():
        from .gui import main as gui_main
        gui_main(a.config)
    return run_window(f"gateway generator config={a.config or '-'}", start)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecucstudio gateway",
                                 description="Generate a CAN <-> Ethernet PDU gateway into a system description")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("inspect", help="list ECUs, channels, sockets of a base file and nodes of DBC files")
    p.add_argument("--base")
    p.add_argument("--dbc", action="append")
    p.set_defaults(fn=cmd_inspect)
    p = sub.add_parser("template", help="write a starting gateway.json")
    p.add_argument("--base", help="network file or DaVinci project .dpa (omit when there are only DBC files)")
    p.add_argument("--ecu", help="ECU instance name of a new file (default: the DBC node)")
    p.add_argument("--schema", default="AUTOSAR_00052", help="schema of a new file (DaVinci 5.24: AUTOSAR_00049 "
                                                             "or older)")
    p.add_argument("--dbc", action="append", help="DBC file (repeat for several buses)")
    p.add_argument("--can-channel", action="append",
                   help="with a .dpa: CAN channel of the project (path or cluster name; default: all of the ECU)")
    p.add_argument("--node")
    p.add_argument("--channel", help="Ethernet channel (path, short name or VLANnn)")
    p.add_argument("--new-channel", action="store_true", help="create a new Ethernet channel (VLAN)")
    p.add_argument("--vlan", type=int, help="VLAN id of a new channel (base file without Ethernet / --new-channel)")
    p.add_argument("--ecu-ip", help="IP address of the ECU when its network endpoint is created")
    p.add_argument("--output-arxml")
    p.add_argument("--no-suggest", action="store_true", help="do not fill in suggested Ethernet settings")
    p.add_argument("-o", "--out", required=True)
    p.set_defaults(fn=cmd_template)
    p = sub.add_parser("suggest", help="suggest the empty Ethernet settings of a gateway.json")
    p.add_argument("config")
    p.add_argument("--write", action="store_true", help="write the suggestions into the empty fields of the file")
    p.set_defaults(fn=cmd_suggest)
    p = sub.add_parser("plan", help="show the routes, header ids and warnings without writing")
    p.add_argument("config")
    p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("generate", help="write base file + gateway")
    p.add_argument("config")
    p.add_argument("-o", "--output")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(fn=cmd_generate)
    p = sub.add_parser("gui")
    p.add_argument("config", nargs="?")
    p.set_defaults(fn=cmd_gui)
    p = sub.add_parser("reopen", help="configuration of a generated gateway file, to change and regenerate it")
    p.add_argument("file")
    p.add_argument("-o", "--out", required=True)
    p.set_defaults(fn=cmd_reopen)
    p = sub.add_parser("routes", help="list the gateway routes of a network file")
    p.add_argument("file")
    p.add_argument("--direction", help="e.g. CAN->ETH")
    p.add_argument("--csv", help="write the table to a CSV file")
    p.set_defaults(fn=cmd_routes)
    p = sub.add_parser("edit", help="change the gateway of a network file")
    p.add_argument("file")
    p.add_argument("--header", action="append", default=[], metavar="ROUTE=VALUE",
                   help="header id of a route (Ethernet PDU, CAN frame or header id name)")
    p.add_argument("--port", action="append", default=[], metavar="SOCKET=PORT")
    p.add_argument("--ip", action="append", default=[], metavar="ENDPOINT=IP[/NETMASK]")
    p.add_argument("--delete", action="append", default=[], metavar="ROUTE", help="delete matching routes")
    p.add_argument("--keep-pdus", action="store_true", help="delete only the gateway mapping, keep the Ethernet PDUs")
    p.add_argument("-o", "--output", help="write to another file (default: the file itself, with .bak)")
    p.set_defaults(fn=cmd_edit)
    p = sub.add_parser("topology", help="several gateway ECUs on one Ethernet network (multi-ECU mode)")
    tsub = p.add_subparsers(dest="tcmd", required=True)
    q = tsub.add_parser("plan", help="pair the messages between the ECUs and show the Ethernet PDUs")
    q.add_argument("topology")
    q.set_defaults(fn=cmd_topology_plan)
    q = tsub.add_parser("generate", help="write the gateway file of the ECUs, the lock file and the contract")
    q.add_argument("topology")
    q.add_argument("--ecu", action="append", help="only this ECU (repeat; default: every ECU with generate)")
    q.add_argument("-v", "--verbose", action="store_true")
    q.set_defaults(fn=cmd_topology_generate)
    q = tsub.add_parser("contract", help="write the contract (every Ethernet PDU of the network) as CSV")
    q.add_argument("topology")
    q.add_argument("-o", "--output")
    q.set_defaults(fn=cmd_topology_contract)
    q = tsub.add_parser("gui", help="open the topology window")
    q.add_argument("topology", nargs="?")
    q.set_defaults(fn=cmd_topology_gui)
    p = sub.add_parser("report", help="message path report (HTML + CSV): where every message comes from, which "
                                      "gateways it passes and where it goes")
    p.add_argument("config", help="gateway.json, a generated gateway .arxml or a topology .json")
    p.add_argument("-o", "--output", help="file name stem / .html of the report (default: next to the input)")
    p.set_defaults(fn=cmd_report)
    p = sub.add_parser("editor", help="open the gateway editor window")
    p.add_argument("file", nargs="?")
    p.set_defaults(fn=cmd_editor)
    a = ap.parse_args(argv)
    if not getattr(a, "fn", None):
        return cmd_gui(argparse.Namespace(config=None))
    try:
        return a.fn(a)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
